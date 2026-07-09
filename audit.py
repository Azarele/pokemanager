"""
Append-only mutation audit log.

One public function: log_mutation(command, item_id, action, details)

Writes one JSON line per call to logs/audit-YYYYMM.jsonl (monthly rotation).
Non-blocking and non-throwing: all errors are swallowed with a single stdout
warning so audit failures never block inventory mutations.

Example line:
  {"ts": "2026-05-27T14:23:11.042819", "command": "add", "item_id": 42,
   "action": "added", "details": {"card_name": "Pikachu", ...}}
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import config


def log_mutation(
    command: str,
    item_id: Optional[int],
    action: str,
    details: dict,
) -> None:
    """
    Append one JSON line to logs/audit-YYYYMM.jsonl.

    Parameters
    ----------
    command : Short identifier — "add", "sell", "remove", "price_refresh", "edit".
    item_id : Affected item's ID, or None for bulk operations.
    action  : Human-readable verb phrase — "added", "sold", "removed", etc.
    details : Arbitrary dict of relevant fields (prices, region, etc.).
    """
    try:
        log_dir = Path(config.AUDIT_LOG_DIR)
        log_dir.mkdir(exist_ok=True)
        now = datetime.now()
        log_file = log_dir / f"audit-{now.strftime('%Y%m')}.jsonl"
        entry = {
            "ts":      now.isoformat(),
            "command": command,
            "item_id": item_id,
            "action":  action,
            "details": details,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        print(f"[audit] Warning: log_mutation failed ({exc})")
