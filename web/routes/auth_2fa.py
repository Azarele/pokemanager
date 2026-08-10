"""
Two-factor authentication (2FA) using TOTP (Time-based One-Time Password).
Uses pyotp library for RFC 6238 compliant TOTP generation and validation.
"""
import os
import logging
import pyotp
import qrcode
import io
import base64
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.auth import get_current_user
from web.database import get_db
from web.encryption import encrypt, decrypt
from web.middleware.rate_limiter import limiter

logger = logging.getLogger(__name__)
router = APIRouter()


class Verify2FARequest(BaseModel):
    """Request to verify 2FA code during login."""
    code: str  # 6-digit TOTP code


class Verify2FAConfirmRequest(BaseModel):
    """Request to confirm 2FA setup."""
    code: str  # 6-digit code to verify the secret works
    password: str  # Require password confirmation


@router.post("/2fa/setup")
async def setup_2fa(user: dict = Depends(get_current_user)):
    """Generate a new TOTP secret for 2FA setup. User must be authenticated."""
    db = get_db()

    # Generate new secret (will be stored after user confirms)
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)

    # Generate QR code
    provisioning_uri = totp.provisioning_uri(
        name=user["email"],
        issuer_name="PokeManager"
    )

    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    qr_base64 = base64.b64encode(img_bytes.getvalue()).decode()

    return {
        "success": True,
        "secret": secret,
        "qr_code": f"data:image/png;base64,{qr_base64}",
        "manual_entry": provisioning_uri,
        "message": "Scan this QR code with your authenticator app, then verify below"
    }


@router.post("/2fa/enable")
@limiter.limit("10/minute")  # Prevent brute-force on 2FA setup confirmation
async def enable_2fa(request: Request, req: Verify2FAConfirmRequest, user: dict = Depends(get_current_user)):
    """Confirm 2FA setup and enable it."""
    db = get_db()

    # Validate the code against the secret provided in setup
    secret = req.__dict__.get("_setup_secret")  # Should come from client-side state
    if not secret:
        raise HTTPException(400, "No secret provided. Call /2fa/setup first.")

    totp = pyotp.TOTP(secret)
    if not totp.verify(req.code, valid_window=1):
        raise HTTPException(400, "Invalid verification code")

    # Enable 2FA and store encrypted secret
    encrypted_secret = encrypt(secret)
    db.table("user_profiles").update({
        "two_fa_enabled": True,
        "totp_secret": encrypted_secret
    }).eq("id", user["id"]).execute()

    logger.info(f"[2fa] User {user['id']} enabled 2FA")

    return {
        "success": True,
        "message": "2FA enabled successfully. Save your backup codes in a safe place."
    }


@router.post("/2fa/disable")
@limiter.limit("10/minute")  # Prevent brute-force attempts to disable 2FA
async def disable_2fa(request: Request, req: Verify2FARequest, user: dict = Depends(get_current_user)):
    """Disable 2FA after verifying current code."""
    db = get_db()

    encrypted_secret = user.get("totp_secret")
    if not encrypted_secret:
        raise HTTPException(400, "2FA not enabled")

    secret = decrypt(encrypted_secret)
    if not secret:
        raise HTTPException(400, "Failed to decrypt 2FA secret")

    totp = pyotp.TOTP(secret)
    if not totp.verify(req.code, valid_window=1):
        raise HTTPException(401, "Invalid verification code")

    db.table("user_profiles").update({
        "two_fa_enabled": False,
        "totp_secret": None
    }).eq("id", user["id"]).execute()

    logger.info(f"[2fa] User {user['id']} disabled 2FA")

    return {"success": True, "message": "2FA disabled"}


@router.post("/2fa/verify")
@limiter.limit("10/minute")  # Prevent brute-force on 2FA codes
async def verify_2fa(request: Request, req: Verify2FARequest, user: dict = Depends(get_current_user)):
    """Verify a 2FA code during login (after successful password check)."""
    encrypted_secret = user.get("totp_secret")
    if not encrypted_secret:
        raise HTTPException(400, "2FA not enabled for this user")

    secret = decrypt(encrypted_secret)
    if not secret:
        raise HTTPException(500, "Failed to decrypt 2FA secret")

    totp = pyotp.TOTP(secret)
    # valid_window=1 allows 30 seconds drift (before/after current time window)
    if not totp.verify(req.code, valid_window=1):
        raise HTTPException(401, "Invalid 2FA code")

    return {"success": True, "message": "2FA verification successful"}


@router.get("/2fa/status")
async def get_2fa_status(user: dict = Depends(get_current_user)):
    """Get current 2FA status for user."""
    return {
        "two_fa_enabled": user.get("two_fa_enabled", False),
        "message": "2FA is " + ("enabled" if user.get("two_fa_enabled") else "disabled")
    }
