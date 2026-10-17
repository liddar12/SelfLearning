"""Owner-only ASGI boundary; no cookies, URL tokens, CORS, or browser persistence."""
from __future__ import annotations

import hmac
from starlette.responses import JSONResponse

MAX_JSON = 256 * 1024
MAX_IMAGE = 3 * 1024 * 1024
MAX_LEARNING_JSON = 3 * 1024 * 1024
NFL_INGEST_PATHS = frozenset(("/api/v1/nfl/objects", "/api/v1/nfl/generations"))
NFL_CAPTURE_PATHS = frozenset(("/api/v1/nfl/capture-chunks", "/api/v1/nfl/capture-deliveries"))
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class OwnerBoundary:
    def __init__(self, app, *, token: str, origins: set[str], ingest_token: str = "", capture_token: str = ""):
        self.app, self.token, self.origins = app, token, origins
        self.ingest_token = ingest_token
        self.capture_token = capture_token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        private = (path.startswith("/api/") and path != "/api/health") or path.startswith("/mcp")

        async def secure_send(message):
            if message["type"] == "http.response.start":
                original = list(message.get("headers", []))
                extras = dict(SECURITY_HEADERS)
                if path.startswith(("/api/", "/mcp")):
                    extras["Cache-Control"] = "no-store"
                names = {name.lower().encode() for name in extras}
                message["headers"] = [(k, v) for k, v in original if k.lower() not in names]
                message["headers"] += [(k.lower().encode(), v.encode()) for k, v in extras.items()]
            await send(message)

        async def reject(status, detail):
            headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
            await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, secure_send)

        if private:
            if any(type(key) is not str for key in (self.token, self.ingest_token, self.capture_token)):
                return await reject(503, "Scoped access is not configured")
            nfl_ingest = path in NFL_INGEST_PATHS
            nfl_capture = (path in NFL_CAPTURE_PATHS or path.startswith("/api/v1/nfl/capture-deliveries/")
                           or path == "/api/v2/nfl/capture-applications" or path.startswith("/api/v2/nfl/capture-applications/"))
            credential = self.capture_token if nfl_capture else self.ingest_token if nfl_ingest else self.token
            configured = [k for k in (self.token, self.ingest_token, self.capture_token) if k]
            if (len(credential) < 32 or credential == "[SENSITIVE]"
                    or len(configured) != len(set(configured))):
                return await reject(503, "Scoped access is not configured")
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            expected = ("Bearer " + credential).encode()
            if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
                return await reject(401, "Scoped access key required")
            origin = headers.get(b"origin")
            if origin and origin.decode("latin-1") not in self.origins:
                return await reject(403, "Origin not allowed")
            maximum = (MAX_LEARNING_JSON if nfl_ingest or nfl_capture else MAX_IMAGE
                       if path == "/api/uploads" and scope["method"] == "POST" else MAX_JSON)
            try:
                length = int(headers.get(b"content-length", b"0"))
                if length < 0:
                    return await reject(400, "Invalid content length")
                if length > maximum:
                    return await reject(413, "Request exceeds the size limit")
            except ValueError:
                return await reject(400, "Invalid content length")
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > maximum:
                    return await reject(413, "Request exceeds the size limit")
                if not message.get("more_body", False):
                    break
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            return await self.app(scope, bounded_receive, secure_send)
        return await self.app(scope, receive, secure_send)
