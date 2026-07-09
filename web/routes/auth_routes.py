from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from web.auth import create_access_token, create_refresh_token, decode_token
from web.database import get_db

router = APIRouter()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str = ""


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        "access_token", access_token,
        httponly=True, samesite="lax",
        max_age=3600, secure=False,
    )
    response.set_cookie(
        "refresh_token", refresh_token,
        httponly=True, samesite="lax",
        max_age=86400 * 30, secure=False,
    )


def _ensure_profile(db, user_id: str, email: str, display_name: str = "") -> None:
    existing = db.table("user_profiles").select("id").eq("id", user_id).execute()
    if not existing.data:
        db.table("user_profiles").insert({
            "id": user_id,
            "email": email,  # retained for promotional purposes per Privacy Policy
            "display_name": display_name or email.split("@")[0],
        }).execute()


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/register")
async def register(body: RegisterRequest):
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    db = get_db()
    try:
        auth_response = db.auth.sign_up({
            "email": body.email,
            "password": body.password,
        })
    except Exception as e:
        raise HTTPException(400, f"Registration failed: {e}")

    user = auth_response.user
    if not user:
        raise HTTPException(400, "Registration failed — email may already be in use")

    _ensure_profile(db, user.id, body.email, body.display_name)

    # Send welcome email
    try:
        from web.email import send_welcome
        send_welcome(body.email, body.display_name or body.email.split("@")[0])
    except Exception as e:
        print(f"[email] Welcome email failed: {e}")

    access_token  = create_access_token(user.id, body.email)
    refresh_token = create_refresh_token(user.id)

    resp = JSONResponse({"success": True, "user": {"id": user.id, "email": body.email}})
    _set_auth_cookies(resp, access_token, refresh_token)
    return resp


@router.post("/login")
async def login(body: LoginRequest):
    db = get_db()
    try:
        auth_response = db.auth.sign_in_with_password({
            "email": body.email,
            "password": body.password,
        })
    except Exception:
        raise HTTPException(401, "Invalid email or password")

    user = auth_response.user
    if not user:
        raise HTTPException(401, "Invalid email or password")

    _ensure_profile(db, user.id, body.email)

    access_token  = create_access_token(user.id, body.email)
    refresh_token = create_refresh_token(user.id)

    resp = JSONResponse({"success": True, "user": {"id": user.id, "email": body.email}})
    _set_auth_cookies(resp, access_token, refresh_token)
    return resp


@router.post("/logout")
async def logout():
    resp = JSONResponse({"success": True})
    resp.delete_cookie("access_token")
    resp.delete_cookie("refresh_token")
    return resp


@router.post("/refresh")
async def refresh(request: Request):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(401, "No refresh token")

    payload = decode_token(token)
    if payload.get("type") != "refresh":
        raise HTTPException(401, "Invalid token type")

    user_id = payload["sub"]
    db = get_db()
    profile = db.table("user_profiles").select("email").eq("id", user_id).single().execute()
    if not profile.data:
        raise HTTPException(401, "User not found")

    new_access = create_access_token(user_id, profile.data["email"])
    resp = JSONResponse({"success": True})
    resp.set_cookie("access_token", new_access, httponly=True, samesite="lax", max_age=3600)
    return resp


@router.get("/me")
async def me(request: Request):
    from web.auth import get_current_user_optional
    user = await get_current_user_optional(request)
    if not user:
        return JSONResponse({"authenticated": False})
    return JSONResponse({
        "authenticated": True,
        "user": {
            "id":           user["id"],
            "email":        user["email"],
            "display_name": user.get("display_name", ""),
            "plan":         user.get("plan", "free"),
            "role":         user.get("role", "user"),
            "has_ebay":     bool(user.get("ebay_app_id")),
            "has_gemini":   bool(user.get("gemini_api_key")),
        },
    })


@router.post("/google")
async def google_oauth():
    """Generate Google OAuth URL via Supabase."""
    import os
    supabase_url = os.getenv("SUPABASE_URL", "")
    site_url = os.getenv("SITE_URL", "http://localhost:8000")

    if not supabase_url:
        return JSONResponse({"url": None, "error": "Supabase not configured"}, status_code=500)

    # Supabase OAuth flow
    redirect_to = f"{site_url}/api/auth/callback"
    oauth_url = f"{supabase_url}/auth/v1/authorize?provider=google&redirect_to={redirect_to}"

    return JSONResponse({"url": oauth_url})


@router.get("/callback")
async def oauth_callback(request: Request):
    """
    Supabase OAuth returns tokens as URL hash fragments.
    Serve HTML page that reads hash client-side and exchanges for session.
    """
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""<!DOCTYPE html>
<html>
<head><title>Signing in to PokeManager...</title>
<link rel="icon" type="image/png" href="/static/icons/favicon-32.png">
</head>
<body style="background:#0f0f13;color:#e8e8f0;font-family:sans-serif;
             display:flex;align-items:center;justify-content:center;
             height:100vh;margin:0;flex-direction:column;gap:12px">
    <div style="font-size:40px">🃏</div>
    <p style="color:#888899">Signing you in...</p>
    <script>
        const hash = window.location.hash.substring(1);
        const params = new URLSearchParams(hash);
        const accessToken  = params.get('access_token');
        const refreshToken = params.get('refresh_token');
        const error = params.get('error_description') || params.get('error');

        if (error) {
            window.location.href = '/login?error=' + encodeURIComponent(error);
        } else if (accessToken) {
            fetch('/api/auth/google-session', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({access_token: accessToken, refresh_token: refreshToken}),
            })
            .then(r => r.json())
            .then(d => { window.location.href = d.success ? '/' : '/login?error=' + (d.error || 'auth_failed'); })
            .catch(() => { window.location.href = '/login?error=network_error'; });
        } else {
            window.location.href = '/login?error=no_token';
        }
    </script>
</body>
</html>""")


@router.post("/google-session")
async def google_session(body: dict):
    """Exchange Supabase OAuth tokens for our own JWT session."""
    from fastapi.responses import JSONResponse
    access_token = body.get("access_token")
    if not access_token:
        return JSONResponse({"success": False, "error": "No token"})
    try:
        db = get_db()
        user_resp = db.auth.get_user(access_token)
        user = user_resp.user
        if not user:
            return JSONResponse({"success": False, "error": "Invalid token"})

        # Ensure profile exists
        existing = db.table("user_profiles").select("id").eq("id", user.id).execute()
        if not existing.data:
            name = (user.user_metadata or {}).get("full_name") or user.email.split("@")[0]
            db.table("user_profiles").insert({
                "id": user.id, "email": user.email,
                "display_name": name, "plan": "free",
            }).execute()

            # Send welcome email to new users
            try:
                from web.email import send_welcome
                send_welcome(user.email, name)
            except Exception as e:
                print(f"[email] Welcome email failed: {e}")

        # Issue our own JWT tokens
        our_access = create_access_token(user.id, user.email)
        our_refresh = create_refresh_token(user.id)
        resp = JSONResponse({"success": True})
        _set_auth_cookies(resp, our_access, our_refresh)
        return resp
    except Exception as e:
        print(f"[oauth] google-session error: {e}")
        return JSONResponse({"success": False, "error": str(e)})
