"""
user_config.py — per-user configuration that overlays global config.

`lister_ebay_api.py` and `ai_helper.py` read credentials from the global
`config` module at call time rather than accepting them as parameters, so
per-user credentials are applied by temporarily overwriting those globals
for the duration of a request. The swap is serialized with a lock (only one
request can hold "foreign" credentials on the globals at a time) so two
users' concurrent eBay/Gemini calls can't interleave and use the wrong key.
"""
import asyncio

import config as global_config

router_lock = asyncio.Lock()


class UserConfig:
    """Wraps global config with per-user overrides from Supabase."""

    def __init__(self, user: dict):
        self._user = user

    def __getattr__(self, name: str):
        user_val = self._user.get(name.lower())
        if user_val is not None:
            return user_val
        return getattr(global_config, name, None)

    @property
    def EBAY_APP_ID(self) -> str:
        return self._user.get("ebay_app_id") or global_config.EBAY_APP_ID

    @property
    def EBAY_DEV_ID(self) -> str:
        return self._user.get("ebay_dev_id") or global_config.EBAY_DEV_ID

    @property
    def EBAY_CERT_ID(self) -> str:
        return self._user.get("ebay_cert_id") or global_config.EBAY_CERT_ID

    @property
    def EBAY_REFRESH_TOKEN(self) -> str:
        return self._user.get("ebay_refresh_token") or global_config.EBAY_REFRESH_TOKEN

    @property
    def EBAY_FULFILLMENT_POLICY_ID(self) -> str:
        return self._user.get("ebay_fulfillment_policy_id") or global_config.EBAY_FULFILLMENT_POLICY_ID

    @property
    def EBAY_PAYMENT_POLICY_ID(self) -> str:
        return self._user.get("ebay_payment_policy_id") or global_config.EBAY_PAYMENT_POLICY_ID

    @property
    def EBAY_RETURN_POLICY_ID(self) -> str:
        return self._user.get("ebay_return_policy_id") or global_config.EBAY_RETURN_POLICY_ID

    @property
    def GEMINI_API_KEY(self) -> str:
        return self._user.get("gemini_api_key") or global_config.GEMINI_API_KEY

    @property
    def EBAY_FEE_RATE(self) -> float:
        return float(self._user.get("ebay_fee_rate") or global_config.EBAY_FEE_RATE)

    @property
    def POSTAGE_COST(self) -> float:
        return float(self._user.get("postage_cost") or 1.50)

    @property
    def AUTO_SYNC_EBAY_PRICES(self) -> bool:
        val = self._user.get("auto_sync_ebay_prices")
        if val is not None:
            return bool(val)
        return global_config.AUTO_SYNC_EBAY_PRICES

    @property
    def has_ebay(self) -> bool:
        return bool(self.EBAY_APP_ID and self.EBAY_CERT_ID and self.EBAY_REFRESH_TOKEN)

    @property
    def has_gemini(self) -> bool:
        return bool(self.GEMINI_API_KEY)


def get_user_config(user: dict) -> UserConfig:
    return UserConfig(user)


# Globals that lister_ebay_api.py / ai_helper.py read at call time. Any name
# added here is swapped in for the duration of `apply(user)` and restored after.
_OVERRIDE_ATTRS = (
    "EBAY_APP_ID", "EBAY_DEV_ID", "EBAY_CERT_ID", "EBAY_REFRESH_TOKEN",
    "EBAY_FULFILLMENT_POLICY_ID", "EBAY_PAYMENT_POLICY_ID", "EBAY_RETURN_POLICY_ID",
    "EBAY_FEE_RATE", "GEMINI_API_KEY",
)


class _ConfigOverride:
    def __init__(self, cfg: UserConfig):
        self._cfg = cfg
        self._orig: dict = {}

    async def __aenter__(self) -> UserConfig:
        await router_lock.acquire()
        for attr in _OVERRIDE_ATTRS:
            self._orig[attr] = getattr(global_config, attr, None)
            value = getattr(self._cfg, attr, None)
            if value:
                setattr(global_config, attr, value)
        if global_config.GEMINI_API_KEY != self._orig.get("GEMINI_API_KEY"):
            self._reset_gemini_client()
        return self._cfg

    async def __aexit__(self, *exc_info) -> None:
        changed = global_config.GEMINI_API_KEY != self._orig.get("GEMINI_API_KEY")
        for attr, value in self._orig.items():
            setattr(global_config, attr, value)
        if changed:
            self._reset_gemini_client()
        router_lock.release()

    @staticmethod
    def _reset_gemini_client() -> None:
        # ai_helper caches a module-level genai.Client keyed off config.GEMINI_API_KEY
        # at first use — force it to rebuild so a swapped-in key actually takes effect.
        try:
            import ai_helper
            ai_helper._client = None
        except Exception:
            pass


def apply(user: dict) -> _ConfigOverride:
    """Async context manager — temporarily overlays `user`'s eBay/Gemini
    credentials onto the global config module for calls that can't take
    config as a parameter.

    Usage:
        async with user_config.apply(user) as cfg:
            await lister_ebay_api.list_item_on_ebay(...)
    """
    return _ConfigOverride(get_user_config(user))
