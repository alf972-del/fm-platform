"""
FM Platform — Backend completo en Python (FastAPI)
===================================================
Equivalente funcional del stack NestJS descrito en la arquitectura.
Usa FastAPI + SQLAlchemy + PostgreSQL + Redis + BullMQ (RQ).

Estructura de módulos:
  main.py               → Entry point ASGI
  config.py             → Settings y variables de entorno
  database.py           → Conexión PostgreSQL + RLS helper
  models/               → SQLAlchemy ORM models
  schemas/              → Pydantic schemas (request/response)
  api/                  → Routers FastAPI (endpoints)
  services/             → Lógica de negocio
  workers/              → Tareas asíncronas (RQ)
  middleware/           → Auth JWT, tenant, logging
  utils/                → QR, SLA calculator, notificaciones

NOTA: Este archivo es el entry point completo.
      Los módulos importados están definidos en sus propios archivos.
"""

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from config import settings
from database import engine, Base
from middleware.auth import JWTMiddleware
from middleware.tenant import TenantMiddleware
from middleware.logging import LoggingMiddleware

# ── Routers ──────────────────────────────────────────
from api.centers   import router as centers_router
from api.assets    import router as assets_router
from api.work_orders import router as wo_router
from api.pm_plans  import router as pm_router
from api.sensors   import router as sensors_router
from api.users     import router as users_router
from api.contracts import router as contracts_router
from api.analytics import router as analytics_router
from api.spaces    import router as spaces_router
from api.soft_fm   import router as softfm_router
from api.webhooks  import router as webhooks_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup y shutdown del servidor."""
    # Startup
    print("🚀 FM Platform iniciando...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Base de datos conectada")
    yield
    # Shutdown
    await engine.dispose()
    print("👋 FM Platform detenido")


# ── App principal ─────────────────────────────────────
app = FastAPI(
    title="FM Platform API",
    description="API REST para gestión integral de Facility Management — Hard FM + Soft FM + IoT + ESG",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Middlewares propios ────────────────────────────────
app.add_middleware(LoggingMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(JWTMiddleware)

# ── Routers ───────────────────────────────────────────
PREFIX = "/v1"

app.include_router(centers_router,   prefix=f"{PREFIX}/centers",     tags=["Centros"])
app.include_router(assets_router,    prefix=f"{PREFIX}/assets",      tags=["Activos"])
app.include_router(wo_router,        prefix=f"{PREFIX}/work-orders",  tags=["Órdenes de Trabajo"])
app.include_router(pm_router,        prefix=f"{PREFIX}/pm-plans",     tags=["Preventivo"])
app.include_router(sensors_router,   prefix=f"{PREFIX}/sensors",      tags=["IoT / Sensores"])
app.include_router(users_router,     prefix=f"{PREFIX}/users",        tags=["Usuarios"])
app.include_router(contracts_router, prefix=f"{PREFIX}/contracts",    tags=["Contratos"])
app.include_router(analytics_router, prefix=f"{PREFIX}/analytics",    tags=["Analytics"])
app.include_router(spaces_router,    prefix=f"{PREFIX}/spaces",       tags=["Espacios"])
app.include_router(softfm_router,    prefix=f"{PREFIX}/soft-fm",      tags=["Soft FM"])
app.include_router(webhooks_router,  prefix=f"{PREFIX}/webhooks",     tags=["Webhooks"])


# ── Health check ──────────────────────────────────────
@app.get("/health", tags=["Sistema"])
async def health_check():
    return {"status": "ok", "version": "1.0.0", "service": "fm-platform-api"}


# ── Handler global de errores ─────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """RFC 7807 Problem Details para errores no manejados."""
    return JSONResponse(
        status_code=500,
        content={
            "type": "https://api.fmplatform.io/errors/internal",
            "title": "Internal Server Error",
            "status": 500,
            "detail": str(exc) if settings.DEBUG else "Error interno del servidor",
            "instance": str(request.url),
        },
    )


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.PORT,
        reload=settings.DEBUG,
        workers=1 if settings.DEBUG else 4,
    )
