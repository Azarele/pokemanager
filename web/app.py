import sys
import os

# Ensure pokemaz root is importable (needed when uvicorn imports this module).
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, HTTPException, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from web.routes import inventory, listings, analytics, pricing, watchlist, sales, calculator, price_history, auth_routes, settings, billing, legal, admin, scan, ebay_sync, staff, instagram
from web.middleware.auth import AuthMiddleware
from web.middleware.rate_limit import RateLimitMiddleware
from web.ws_manager import manager

logger = logging.getLogger(__name__)

# Validate Stripe configuration at startup
stripe_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
if not stripe_key:
    logger.warning("STRIPE_SECRET_KEY not set — Stripe features will be unavailable")

_here = Path(__file__).parent
_index_html   = (_here / "templates" / "index.html").read_text()
_landing_html = (_here / "templates" / "landing.html").read_text()

app = FastAPI(title="PokeManager", version="1.0.0")

# Public routes (no auth required)
public_router = APIRouter()

@public_router.get("/api/analytics/public-stats")
async def public_stats():
    try:
        import os
        from supabase import create_client

        # Use service key to bypass RLS
        sb = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_KEY")
        )

        sold = sb.table("inventory_items").select("sell_price").eq("status", "Sold").execute()
        inv = sb.table("inventory_items").select("item_id").eq("status", "Inventory").execute()
        users = sb.table("user_profiles").select("id").execute()

        revenue = int(sum(float(r.get("sell_price") or 0) for r in sold.data))

        print(f"[public-stats] revenue={revenue}, sold={len(sold.data)}, inv={len(inv.data)}, users={len(users.data)}")

        return {
            "total_revenue": revenue,
            "total_sold": len(sold.data),
            "total_in_stock": len(inv.data),
            "total_users": len(users.data)
        }
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"total_revenue": 0, "total_sold": 0, "total_in_stock": 0, "total_users": 0}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


# Include public routes BEFORE middleware
app.include_router(public_router)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)

app.include_router(auth_routes.router,   prefix="/api/auth")
app.include_router(inventory.router,     prefix="/api/inventory")
app.include_router(listings.router,      prefix="/api/listings")
app.include_router(analytics.router,     prefix="/api/analytics")
app.include_router(pricing.router,       prefix="/api/pricing")
app.include_router(watchlist.router,     prefix="/api/watchlist")
app.include_router(sales.router,         prefix="/api/sales")
app.include_router(calculator.router,    prefix="/api/calculator")
app.include_router(price_history.router, prefix="/api/price-history")
app.include_router(settings.router,      prefix="/api/settings")
app.include_router(billing.router,       prefix="/api/billing")
app.include_router(admin.router,         prefix="/api/admin")
app.include_router(scan.router,          prefix="/api/scan")
app.include_router(ebay_sync.router,     prefix="/api/ebay")
app.include_router(instagram.router,     prefix="/api/instagram")
app.include_router(staff.router,         prefix="/api/staff")
app.include_router(legal.router,         prefix="")

app.mount("/static", StaticFiles(directory=str(_here / "static")), name="static")


@app.on_event("startup")
async def startup_check():
    required = ["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "JWT_SECRET"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logger.warning(f"Missing env vars: {missing}")
        logger.warning("Some features will not work. Check your .env file.")
    else:
        logger.info("PokeManager starting — all required env vars present")

    asyncio.create_task(ebay_sync_background_loop())


async def ebay_sync_background_loop():
    """Background task: sync eBay sales every 30 minutes for all users."""
    await asyncio.sleep(60)  # Wait 1 minute before first sync
    while True:
        try:
            await ebay_sync.sync_all_users_ebay()
        except Exception as e:
            logger.error(f"Error in eBay sync background loop: {e}")
        await asyncio.sleep(1800)  # 30 minutes


@app.exception_handler(404)
async def not_found(request, exc):
    return HTMLResponse("""
        <html><body style="background:#0f0f13;color:#e8e8f0;font-family:sans-serif;
                           display:flex;align-items:center;justify-content:center;
                           height:100vh;margin:0;flex-direction:column;gap:16px">
            <div style="font-size:48px">🃏</div>
            <h1 style="margin:0">Page not found</h1>
            <a href="/" style="color:#6c63ff">Back to PokeManager</a>
        </body></html>
    """, status_code=404)


@app.exception_handler(500)
async def server_error(request, exc):
    return HTMLResponse("""
        <html><body style="background:#0f0f13;color:#e8e8f0;font-family:sans-serif;
                           display:flex;align-items:center;justify-content:center;
                           height:100vh;margin:0;flex-direction:column;gap:16px">
            <div style="font-size:48px">⚠️</div>
            <h1 style="margin:0">Something went wrong</h1>
            <p style="color:#888899;margin:0">The server encountered an error.</p>
            <a href="/" style="color:#6c63ff">Back to PokeManager</a>
        </body></html>
    """, status_code=500)


@app.get("/api/status")
async def api_status(request: Request):
    """Status endpoint — returns per-user counts if logged in, basic status if not."""
    try:
        from web.auth import get_current_user_optional
        from web import db_inventory as db

        user = await get_current_user_optional(request)

        if not user:
            return {"status": "ok", "version": "1.0.0", "name": "PokeManager"}

        items = await db.get_all_items(user["id"])
        in_stock = len([i for i in items if i["status"] == "Inventory"])
        sold     = len([i for i in items if i["status"] == "Sold"])

        return {
            "status":   "ok",
            "in_stock": in_stock,
            "sold":     sold,
        }
    except Exception as e:
        return {"status": "ok"}


@app.websocket("/ws/updates")
async def ws_updates(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        manager.disconnect(websocket)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(content=_landing_html)


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def serve_spa(full_path: str, request: Request):
    # Don't serve SPA for API routes or legal pages
    if full_path.startswith("api/") or full_path in ("terms", "privacy", "cookies"):
        raise HTTPException(404)
    return HTMLResponse(content=_index_html)
