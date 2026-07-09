"""
Tier checking helpers. Import and use in any route that needs gating.
"""

FEATURES = {
    "unlimited_items":      ["gym_leader", "champion"],
    "ebay_listing":         ["gym_leader", "champion"],
    "ai_descriptions":      ["gym_leader", "champion"],
    "ai_descriptions_managed": ["champion"],
    "price_history":        ["gym_leader", "champion"],
    "export_accounting":    ["gym_leader", "champion"],
    "priority_support":     ["champion"],
}

FREE_ITEM_LIMIT = 50


def get_plan(user: dict) -> str:
    # Admins get champion access regardless of plan
    if user.get("role") == "admin":
        return "champion"
    return user.get("plan") or "free"


def can(user: dict, feature: str) -> bool:
    # Admins can do everything
    if user.get("role") == "admin":
        return True
    plan = get_plan(user)
    return plan in FEATURES.get(feature, [])


def check_item_limit(user: dict, current_count: int) -> bool:
    """Returns True if user can add more items."""
    # Admins have no limit
    if user.get("role") == "admin":
        return True
    if can(user, "unlimited_items"):
        return True
    return current_count < FREE_ITEM_LIMIT


def require_feature(user: dict, feature: str):
    """Raise HTTPException if user doesn't have access to feature."""
    from fastapi import HTTPException
    if not can(user, feature):
        plan = get_plan(user)
        raise HTTPException(
            status_code=403,
            detail={
                "error":    "upgrade_required",
                "feature":  feature,
                "plan":     plan,
                "message":  f"This feature requires a Gym Leader or Champion subscription.",
            }
        )
