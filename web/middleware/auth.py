"""
Auth middleware — validates JWT on every request.
Unauthenticated browsers are redirected to /login.
Unauthenticated API calls get a 401 JSON response.
"""
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from web.auth import decode_token

# Paths (or prefixes) that do not require authentication.
_UNPROTECTED = {
    "/login",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh",
    "/api/auth/me",
    "/api/auth/google",
    "/api/auth/callback",
    "/api/auth/google-session",
    "/api/billing/webhook",
    "/api/status",
    "/static",
    "/favicon.ico",
    "/ws",          # WebSocket upgrade must pass through
}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if any(path == p or path.startswith(p + "/") for p in _UNPROTECTED):
            return await call_next(request)

        # Resolve token — cookie first, then Authorization header
        token = request.cookies.get("access_token")
        if not token:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]

        if not token:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse("/login")

        try:
            payload = decode_token(token)
            request.state.user_id    = payload["sub"]
            request.state.user_email = payload.get("email", "")
        except Exception:
            # Expired/invalid — try to use refresh token for browser sessions
            refresh = request.cookies.get("refresh_token")
            if refresh and not path.startswith("/api/"):
                return RedirectResponse("/api/auth/refresh")
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Token expired"}, status_code=401)
            return RedirectResponse("/login")

        return await call_next(request)
