from collections import defaultdict
from time import time
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, calls: int = 1000, period: int = 60):
        super().__init__(app)
        self._calls  = calls
        self._period = period
        self._hits   = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        now    = time()
        hits   = [h for h in self._hits[client] if now - h < self._period]

        if len(hits) >= self._calls:
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

        hits.append(now)
        self._hits[client] = hits
        return await call_next(request)