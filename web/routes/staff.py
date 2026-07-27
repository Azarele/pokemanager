import logging
import os
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web.auth import get_current_user
from web.database import get_db as _get_db

logger = logging.getLogger(__name__)

router = APIRouter()


class InviteStaffRequest(BaseModel):
    email: str
    role: str = "staff"
    permissions: dict = None


class AcceptInviteRequest(BaseModel):
    token: str


@router.get("/members")
async def get_staff_members(user: dict = Depends(get_current_user)):
    """Get all staff members for this account (Champion only)."""
    if user.get("plan") != "champion":
        return {"success": False, "error": "Staff accounts require Champion plan"}

    database = _get_db()

    # Get staff accounts where user is the owner
    result = database.table("staff_accounts")\
        .select("*")\
        .eq("owner_id", user["id"])\
        .execute()

    staff = result.data or []
    logger.info(f"[staff] Found {len(staff)} staff members for owner {user['id']}")

    return {"success": True, "staff": staff}


@router.post("/invite")
async def invite_staff(req: InviteStaffRequest, user: dict = Depends(get_current_user)):
    """Send invite to staff member (Champion only)."""
    if user.get("plan") != "champion":
        return {"success": False, "error": "Staff accounts require Champion plan"}

    logger.info(f"[staff] Invite request from {user['id']}: email={req.email}, role={req.role}")

    database = _get_db()

    # Generate invite token
    token = secrets.token_urlsafe(32)

    # Set default permissions based on role
    if req.permissions is None:
        if req.role == "viewer":
            req.permissions = {
                "view_inventory": True,
                "add_items": False,
                "edit_items": False,
                "delete_items": False,
                "view_sales": True,
                "record_sales": False,
                "view_analytics": False,
                "manage_listings": False,
                "view_financials": False,
            }
        elif req.role == "manager":
            req.permissions = {
                "view_inventory": True,
                "add_items": True,
                "edit_items": True,
                "delete_items": False,
                "view_sales": True,
                "record_sales": True,
                "view_analytics": True,
                "manage_listings": True,
                "view_financials": False,
            }
        else:  # staff
            req.permissions = {
                "view_inventory": True,
                "add_items": True,
                "edit_items": False,
                "delete_items": False,
                "view_sales": True,
                "record_sales": True,
                "view_analytics": False,
                "manage_listings": False,
                "view_financials": False,
            }

    # Create invite record
    try:
        result = database.table("staff_accounts").insert({
            "owner_id": user["id"],
            "invited_email": req.email,
            "role": req.role,
            "permissions": req.permissions,
            "invite_token": token,
            "invite_status": "pending",
        }).execute()

        logger.info(f"[staff] Created invite: token={token[:10]}..., email={req.email}")

        # TODO: Send email via Resend
        invite_link = f"{os.getenv('SITE_URL', 'http://localhost:3000')}/staff/accept?token={token}"
        logger.info(f"[staff] Invite link: {invite_link}")

        return {
            "success": True,
            "message": f"Invite sent to {req.email}",
            "invite_link": invite_link
        }
    except Exception as e:
        logger.info(f"[staff] Invite failed: {e}")
        return {"success": False, "error": str(e)}


@router.post("/accept")
async def accept_invite(req: AcceptInviteRequest, user: dict = Depends(get_current_user)):
    """Accept staff invite (auto-links to current user)."""
    logger.info(f"[staff] Accept invite: token={req.token[:10]}..., user={user['id']}")

    database = _get_db()

    # Find invite
    result = database.table("staff_accounts")\
        .select("*")\
        .eq("invite_token", req.token)\
        .eq("invite_status", "pending")\
        .execute()

    invites = result.data or []
    if not invites:
        return {"success": False, "error": "Invite not found or already used"}

    invite = invites[0]
    owner_id = invite["owner_id"]

    # Check if invite is not expired (7 days)
    created_at = datetime.fromisoformat(invite["created_at"].replace("Z", "+00:00"))
    if datetime.utcnow() > created_at + timedelta(days=7):
        return {"success": False, "error": "Invite has expired"}

    # Update invite to accepted
    database.table("staff_accounts").update({
        "staff_user_id": user["id"],
        "invite_status": "accepted",
        "accepted_at": datetime.utcnow().isoformat(),
    }).eq("invite_token", req.token).execute()

    # Update user_profiles with staff context
    database.table("user_profiles").update({
        "owner_id": owner_id,
        "is_staff": True,
        "staff_role": invite["role"],
        "staff_permissions": invite["permissions"],
    }).eq("id", user["id"]).execute()

    logger.info(f"[staff] Accepted invite: staff={user['id']}, owner={owner_id}")

    return {
        "success": True,
        "message": "Invite accepted! You now have access to the team account.",
        "owner_id": owner_id
    }


@router.delete("/members/{staff_id}")
async def remove_staff(staff_id: str, user: dict = Depends(get_current_user)):
    """Remove staff member (owner only)."""
    logger.info(f"[staff] Remove staff: staff_id={staff_id}, owner={user['id']}")

    database = _get_db()

    # Verify this staff relationship belongs to the owner
    result = database.table("staff_accounts")\
        .select("*")\
        .eq("id", staff_id)\
        .eq("owner_id", user["id"])\
        .execute()

    if not result.data:
        return {"success": False, "error": "Staff member not found"}

    staff = result.data[0]
    staff_user_id = staff["staff_user_id"]

    # Remove staff relationship
    database.table("staff_accounts").delete().eq("id", staff_id).execute()

    # Reset user_profiles
    if staff_user_id:
        database.table("user_profiles").update({
            "owner_id": None,
            "is_staff": False,
            "staff_role": None,
            "staff_permissions": None,
        }).eq("id", staff_user_id).execute()

    logger.info(f"[staff] Removed staff member {staff_user_id}")

    return {"success": True, "message": "Staff member removed"}


@router.patch("/members/{staff_id}/permissions")
async def update_permissions(staff_id: str, data: dict, user: dict = Depends(get_current_user)):
    """Update staff member permissions (owner only)."""
    logger.info(f"[staff] Update permissions: staff_id={staff_id}, owner={user['id']}")

    database = _get_db()

    # Verify ownership
    result = database.table("staff_accounts")\
        .select("*")\
        .eq("id", staff_id)\
        .eq("owner_id", user["id"])\
        .execute()

    if not result.data:
        return {"success": False, "error": "Staff member not found"}

    staff = result.data[0]
    permissions = data.get("permissions", {})

    # Update permissions
    database.table("staff_accounts").update({
        "permissions": permissions
    }).eq("id", staff_id).execute()

    # Update user_profiles if staff is currently linked
    if staff["staff_user_id"]:
        database.table("user_profiles").update({
            "staff_permissions": permissions
        }).eq("id", staff["staff_user_id"]).execute()

    logger.info(f"[staff] Updated permissions for {staff['staff_user_id']}")

    return {"success": True, "message": "Permissions updated"}


@router.get("/activity")
async def get_activity_log(user: dict = Depends(get_current_user)):
    """Get activity log for owner's account."""
    limit = 100
    logger.info(f"[staff] Activity log request from {user['id']}")

    database = _get_db()

    # Get activity for this owner
    result = database.table("activity_log")\
        .select("*")\
        .eq("owner_id", user["id"])\
        .order("created_at", desc=True)\
        .limit(limit)\
        .execute()

    activities = result.data or []
    logger.info(f"[staff] Found {len(activities)} activity records")

    return {"success": True, "activities": activities, "limit": limit}


async def log_activity(owner_id: str, user_id: str, user_email: str, action: str, item_id: int = None, details: dict = None):
    """Log activity for audit trail."""
    try:
        database = _get_db()
        database.table("activity_log").insert({
            "owner_id": owner_id,
            "user_id": user_id,
            "user_email": user_email,
            "action": action,
            "item_id": item_id,
            "details": details or {},
        }).execute()
        logger.info(f"[activity] Logged: {action} by {user_email} for item {item_id}")
    except Exception as e:
        logger.info(f"[activity] Failed to log activity: {e}")
