import time
from contextlib import asynccontextmanager

import sentry_sdk
import structlog
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import get_settings
from db import engine, Base
from routers import alerts, assistant, auth, facilities, health_scores, predict, redistribution, sync, webhooks
from services.websocket_manager import WebSocketManager

settings = get_settings()
log = structlog.get_logger()
ws_manager = WebSocketManager()


if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1 if settings.is_production else 1.0,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", environment=settings.environment)
    app.state.ws_manager = ws_manager
    yield
    await engine.dispose()
    log.info("shutdown")


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="SmartHealth API",
    description="District Health Operating System — AI-powered PHC/CHC management",
    version="1.0.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator().instrument(app).expose(app, endpoint="/metrics")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration_ms, 2),
    )
    return response


@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok", "environment": settings.environment}


@app.websocket("/ws/alerts")
async def alerts_websocket(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# Routers
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
app.include_router(facilities.router, prefix="/api/v1", tags=["facilities"])
app.include_router(alerts.router, prefix="/api/v1", tags=["alerts"])
app.include_router(predict.router, prefix="/api/v1", tags=["predictions"])
app.include_router(redistribution.router, prefix="/api/v1", tags=["redistribution"])
app.include_router(health_scores.router, prefix="/api/v1", tags=["health-scores"])
app.include_router(webhooks.router, prefix="/api/v1", tags=["webhooks"])
app.include_router(sync.router, prefix="/api/v1", tags=["sync"])
app.include_router(assistant.router, prefix="/api/v1", tags=["assistant"])
