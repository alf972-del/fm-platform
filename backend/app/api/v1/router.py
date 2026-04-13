"""
app/api/v1/router.py — Router principal que agrupa todos los sub-routers.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    work_orders,
    assets,
    centers,
    sensors,
    pm_plans,
    users,
    contracts,
    analytics,
    webhooks,
    spaces,
    auth,
)

api_router = APIRouter()

api_router.include_router(auth.router,         prefix="/auth",         tags=["Auth"])
api_router.include_router(centers.router,      prefix="/centers",      tags=["Centers"])
api_router.include_router(assets.router,       prefix="/assets",       tags=["Assets"])
api_router.include_router(work_orders.router,  prefix="/work-orders",  tags=["Work Orders"])
api_router.include_router(pm_plans.router,     prefix="/pm-plans",     tags=["PM Plans"])
api_router.include_router(sensors.router,      prefix="/sensors",      tags=["Sensors / IoT"])
api_router.include_router(users.router,        prefix="/users",        tags=["Users"])
api_router.include_router(contracts.router,    prefix="/contracts",    tags=["Contracts"])
api_router.include_router(spaces.router,       prefix="/spaces",       tags=["Spaces"])
api_router.include_router(analytics.router,    prefix="/analytics",    tags=["Analytics"])
api_router.include_router(webhooks.router,     prefix="/webhooks",     tags=["Webhooks"])
