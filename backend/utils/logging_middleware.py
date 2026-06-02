import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_log = structlog.get_logger("emc-review.access")


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = uuid.uuid4().hex[:8]
        request.state.req_id = req_id
        structlog.contextvars.bind_contextvars(reqId=req_id)

        start = time.time()
        client_ip = request.client.host if request.client else "unknown"

        response: Response = await call_next(request)

        elapsed_ms = int((time.time() - start) * 1000)
        path = request.url.path
        method = request.method
        status = response.status_code

        response.headers["X-Request-Id"] = req_id

        if status >= 500:
            _log.error("request completed", method=method, path=path, status=status,
                        durationMs=elapsed_ms, clientIp=client_ip)
        elif status >= 400:
            _log.warning("request completed", method=method, path=path, status=status,
                       durationMs=elapsed_ms, clientIp=client_ip)
        else:
            _log.info("request completed", method=method, path=path, status=status,
                       durationMs=elapsed_ms, clientIp=client_ip)

        structlog.contextvars.unbind_contextvars("reqId")
        return response
