"""
FM Platform — API Routes (FastAPI)
REST API v1 con autenticación JWT, multi-tenant y paginación cursor
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, List, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Header, Query, Path, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, and_, or_, func, text

from .models import (
    WorkOrder, WorkOrderStatus, WorkOrderPriority, WorkOrderType,
    Asset, AssetCategory, AssetStatus, AssetCriticality,
    Center, CenterType, User, UserType, Sensor, PMPlan, Contract,
    Base
)
from .services import (
    WorkOrderService, WorkOrderAction, WorkOrderStateMachineError,
    PMSchedulerService, IoTAlertService, AnalyticsService
)


# ─────────────────────────────────────────────
# APP & DB SETUP
# ─────────────────────────────────────────────

DATABASE_URL = "postgresql+asyncpg://fm_user:fm_pass@localhost/fm_platform"

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=20, max_overflow=10)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup y shutdown del servidor."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="FM Platform API",
    version="1.0.0",
    description="Facility Management Platform — REST API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.fmplatform.io", "https://tenants.fmplatform.io", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# DEPENDENCIES
# ─────────────────────────────────────────────

async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


async def get_current_user(
    authorization: str = Header(..., description="Bearer JWT token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Valida JWT de Keycloak y extrae tenant_id y roles.
    En producción: validar firma con clave pública de Keycloak.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization.replace("Bearer ", "")
    # TODO: validar firma JWT con Keycloak JWKS
    # decoded = jwt.decode(token, public_key, algorithms=["RS256"])

    # Mock para desarrollo — en producción usar python-jose
    return {
        "user_id": "user-uuid-here",
        "tenant_id": "tenant-uuid-here",
        "email": "user@example.com",
        "roles": ["fm_manager"],
        "center_ids": [],  # [] = acceso a todos los centros del tenant
    }


async def set_tenant_rls(db: AsyncSession, tenant_id: str) -> None:
    """Establece el tenant actual para Row Level Security de PostgreSQL."""
    await db.execute(text(f"SET app.current_tenant = '{tenant_id}'"))


# ─────────────────────────────────────────────
# SCHEMAS (Pydantic)
# ─────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    data: List[Any]
    pagination: dict

    class Config:
        arbitrary_types_allowed = True


# -- Work Orders --

class WorkOrderCreate(BaseModel):
    center_id: str
    type: WorkOrderType
    title: str = Field(..., min_length=3, max_length=500)
    priority: WorkOrderPriority
    asset_id: Optional[str] = None
    space_id: Optional[str] = None
    assigned_to: Optional[str] = None
    contract_id: Optional[str] = None
    description: Optional[str] = None
    estimated_cost: Optional[float] = Field(None, ge=0)
    scheduled_for: Optional[datetime] = None
    checklist_template_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class WorkOrderTransitionRequest(BaseModel):
    action: WorkOrderAction
    comment: Optional[str] = None
    assigned_to: Optional[str] = None
    resolution: Optional[str] = None
    actual_cost: Optional[float] = Field(None, ge=0)


class ChecklistItemUpdate(BaseModel):
    id: str
    completed: Optional[bool] = None
    value: Optional[float] = None
    note: Optional[str] = None
    completed_at: Optional[datetime] = None


class ChecklistBatchUpdate(BaseModel):
    items: List[ChecklistItemUpdate]


class AttachmentPresignRequest(BaseModel):
    filename: str
    content_type: str
    size_bytes: int = Field(..., le=50_000_000)  # max 50MB


class AttachmentConfirmRequest(BaseModel):
    attachment_id: str
    type: str


# -- Assets --

class AssetCreate(BaseModel):
    center_id: str
    parent_id: Optional[str] = None
    code: Optional[str] = None  # Auto-generado si None
    name: str = Field(..., min_length=2, max_length=300)
    category: AssetCategory
    criticality: AssetCriticality = AssetCriticality.MEDIUM
    location: dict = Field(default_factory=dict)
    specs: dict = Field(default_factory=dict)
    purchase_date: Optional[datetime] = None
    warranty_until: Optional[datetime] = None
    expected_life_years: Optional[int] = Field(None, ge=0, le=100)
    serial_number: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None


class AssetBulkImport(BaseModel):
    """Para importación masiva desde CSV."""
    center_id: str
    assets: List[AssetCreate]
    skip_duplicates: bool = True


# -- Sensors --

class SensorReadingItem(BaseModel):
    sensor_id: str
    time: datetime
    value: float
    unit: str
    quality: str = "good"


class SensorIngestRequest(BaseModel):
    readings: List[SensorReadingItem] = Field(..., max_items=500)


# -- Analytics --

class KPIRequest(BaseModel):
    center_id: Optional[str] = None
    period: str = "last_30d"
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None


# ─────────────────────────────────────────────
# ERROR HANDLERS (RFC 7807)
# ─────────────────────────────────────────────

def problem_response(
    status_code: int,
    title: str,
    detail: str,
    type_slug: str = "generic",
    errors: Optional[List[dict]] = None,
) -> JSONResponse:
    """Respuesta de error estándar RFC 7807 Problem Details."""
    body = {
        "type": f"https://api.fmplatform.io/errors/{type_slug}",
        "title": title,
        "status": status_code,
        "detail": detail,
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(status_code=status_code, content=body)


@app.exception_handler(WorkOrderStateMachineError)
async def state_machine_error_handler(request, exc):
    return problem_response(
        409, "Invalid state transition",
        str(exc), "wo-transition-invalid"
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return problem_response(exc.status_code, exc.detail, exc.detail)


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "version": "1.0.0", "timestamp": datetime.now(timezone.utc).isoformat()}


# ─────────────────────────────────────────────
# WORK ORDERS
# ─────────────────────────────────────────────

@app.get("/v1/work-orders", tags=["Work Orders"])
async def list_work_orders(
    status: Optional[WorkOrderStatus] = Query(None),
    type: Optional[WorkOrderType] = Query(None),
    priority: Optional[WorkOrderPriority] = Query(None),
    center_id: Optional[str] = Query(None),
    asset_id: Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    sla_overdue: Optional[bool] = Query(None),
    created_after: Optional[datetime] = Query(None),
    sort: str = Query("sla_deadline", description="-created_at | sla_deadline | priority"),
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista OTs con filtros, paginación cursor y ordenamiento."""
    await set_tenant_rls(db, current_user["tenant_id"])

    filters = [WorkOrder.tenant_id == current_user["tenant_id"]]

    if status:
        filters.append(WorkOrder.status == status)
    if type:
        filters.append(WorkOrder.type == type)
    if priority:
        filters.append(WorkOrder.priority == priority)
    if center_id:
        filters.append(WorkOrder.center_id == center_id)
    if asset_id:
        filters.append(WorkOrder.asset_id == asset_id)
    if assigned_to:
        filters.append(WorkOrder.assigned_to == assigned_to)
    if sla_overdue:
        filters.append(WorkOrder.sla_deadline <= datetime.now(timezone.utc))
        filters.append(WorkOrder.status.not_in([WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED]))
    if created_after:
        filters.append(WorkOrder.created_at >= created_after)

    # Cursor-based pagination
    if cursor:
        import base64
        try:
            cursor_data = base64.b64decode(cursor).decode()
            cursor_id, cursor_ts = cursor_data.split("|")
            filters.append(
                or_(
                    WorkOrder.created_at < cursor_ts,
                    and_(WorkOrder.created_at == cursor_ts, WorkOrder.id < cursor_id),
                )
            )
        except Exception:
            return problem_response(400, "Invalid cursor", "The cursor parameter is malformed", "invalid-cursor")

    # Ordenamiento
    order_col = WorkOrder.created_at
    order_desc = True
    if sort.lstrip("-") == "sla_deadline":
        order_col = WorkOrder.sla_deadline
    elif sort.lstrip("-") == "priority":
        order_col = WorkOrder.priority

    if sort.startswith("-"):
        from sqlalchemy import desc
        query = select(WorkOrder).where(*filters).order_by(desc(order_col)).limit(limit + 1)
    else:
        from sqlalchemy import asc
        query = select(WorkOrder).where(*filters).order_by(asc(order_col)).limit(limit + 1)

    result = await db.execute(query)
    items = result.scalars().all()

    has_more = len(items) > limit
    if has_more:
        items = items[:-1]

    # Generar next cursor
    next_cursor = None
    if has_more and items:
        import base64
        last = items[-1]
        cursor_str = f"{last.id}|{last.created_at.isoformat()}"
        next_cursor = base64.b64encode(cursor_str.encode()).decode()

    # Serialización manual (en producción usar marshmallow o pydantic model)
    def serialize_wo(wo: WorkOrder) -> dict:
        return {
            "id": wo.id,
            "code": wo.code,
            "type": wo.type,
            "status": wo.status,
            "priority": wo.priority,
            "title": wo.title,
            "center_id": wo.center_id,
            "asset_id": wo.asset_id,
            "assigned_to": wo.assigned_to,
            "sla_deadline": wo.sla_deadline.isoformat() if wo.sla_deadline else None,
            "sla_breached": wo.sla_breached,
            "sla_minutes_remaining": (
                int((wo.sla_deadline - datetime.now(timezone.utc)).total_seconds() / 60)
                if wo.sla_deadline else None
            ),
            "estimated_cost": float(wo.estimated_cost) if wo.estimated_cost else None,
            "actual_cost": float(wo.actual_cost) if wo.actual_cost else None,
            "started_at": wo.started_at.isoformat() if wo.started_at else None,
            "closed_at": wo.closed_at.isoformat() if wo.closed_at else None,
            "created_at": wo.created_at.isoformat(),
            "updated_at": wo.updated_at.isoformat(),
        }

    return {
        "data": [serialize_wo(wo) for wo in items],
        "pagination": {
            "cursor": next_cursor,
            "has_more": has_more,
        },
    }


@app.get("/v1/work-orders/{work_order_id}", tags=["Work Orders"])
async def get_work_order(
    work_order_id: str = Path(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Obtiene el detalle completo de una OT con checklist, adjuntos y timeline."""
    await set_tenant_rls(db, current_user["tenant_id"])

    result = await db.execute(
        select(WorkOrder)
        .where(WorkOrder.id == work_order_id, WorkOrder.tenant_id == current_user["tenant_id"])
        .options(
            selectinload(WorkOrder.checklist_items),
            selectinload(WorkOrder.attachments),
            selectinload(WorkOrder.transitions),
            selectinload(WorkOrder.time_logs),
        )
    )
    # Import at top level in production
    from sqlalchemy.orm import selectinload as _sl

    wo = await db.get(WorkOrder, work_order_id)
    if not wo or wo.tenant_id != current_user["tenant_id"]:
        return problem_response(404, "Not found", f"WorkOrder {work_order_id} not found", "not-found")

    return {"data": {"id": wo.id, "code": wo.code, "status": wo.status, "title": wo.title}}


@app.post("/v1/work-orders", status_code=status.HTTP_201_CREATED, tags=["Work Orders"])
async def create_work_order(
    body: WorkOrderCreate,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Crea una nueva OT. Acepta Idempotency-Key para prevenir duplicados."""
    await set_tenant_rls(db, current_user["tenant_id"])

    # Verificar idempotency key
    if idempotency_key:
        existing = await db.execute(
            select(WorkOrder).where(
                WorkOrder.metadata["idempotency_key"].astext == idempotency_key,
                WorkOrder.tenant_id == current_user["tenant_id"],
            )
        )
        if existing.scalar_one_or_none():
            return problem_response(409, "Duplicate request", "Idempotency key already used", "idempotency-conflict")

    svc = WorkOrderService(db)
    wo = await svc.create(
        tenant_id=current_user["tenant_id"],
        center_id=body.center_id,
        wo_type=body.type,
        title=body.title,
        priority=body.priority,
        created_by=current_user["user_id"],
        asset_id=body.asset_id,
        space_id=body.space_id,
        assigned_to=body.assigned_to,
        contract_id=body.contract_id,
        description=body.description,
        estimated_cost=body.estimated_cost,
        metadata={**(body.metadata or {}), "idempotency_key": idempotency_key},
    )
    await db.commit()

    return {
        "id": wo.id,
        "code": wo.code,
        "status": wo.status,
        "sla_deadline": wo.sla_deadline.isoformat() if wo.sla_deadline else None,
        "created_at": wo.created_at.isoformat(),
    }


@app.post("/v1/work-orders/{work_order_id}/transition", tags=["Work Orders"])
async def transition_work_order(
    work_order_id: str,
    body: WorkOrderTransitionRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aplica una transición de estado (state machine)."""
    await set_tenant_rls(db, current_user["tenant_id"])
    svc = WorkOrderService(db)

    wo = await svc.transition(
        work_order_id=work_order_id,
        action=body.action,
        triggered_by_user_id=current_user["user_id"],
        user_role=current_user["roles"][0] if current_user["roles"] else "staff",
        comment=body.comment,
        assigned_to=body.assigned_to,
        resolution=body.resolution,
        actual_cost=body.actual_cost,
    )
    await db.commit()
    return {"id": wo.id, "code": wo.code, "status": wo.status, "updated_at": wo.updated_at.isoformat()}


@app.patch("/v1/work-orders/{work_order_id}/checklist", tags=["Work Orders"])
async def update_checklist(
    work_order_id: str,
    body: ChecklistBatchUpdate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Actualiza ítems de checklist en batch (offline sync friendly)."""
    await set_tenant_rls(db, current_user["tenant_id"])
    svc = WorkOrderService(db)
    items = await svc.update_checklist(
        work_order_id=work_order_id,
        items=[i.dict() for i in body.items],
    )
    await db.commit()
    return {"updated": len(items)}


@app.post("/v1/work-orders/{work_order_id}/attachments/presign", tags=["Work Orders"])
async def presign_attachment(
    work_order_id: str,
    body: AttachmentPresignRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Genera URL pre-firmada de S3 para subida directa de archivos."""
    attachment_id = str(uuid.uuid4())
    s3_key = f"tenants/{current_user['tenant_id']}/wo/{work_order_id}/{attachment_id}/{body.filename}"

    # En producción: usar boto3 para generar presigned URL
    # s3_client = boto3.client('s3')
    # upload_url = s3_client.generate_presigned_url('put_object', Params={...}, ExpiresIn=900)
    upload_url = f"https://fm-platform-storage.s3.eu-west-1.amazonaws.com/{s3_key}?X-Amz-Signature=..."

    return {
        "upload_url": upload_url,
        "attachment_id": attachment_id,
        "s3_key": s3_key,
        "expires_in_seconds": 900,
    }


# ─────────────────────────────────────────────
# ASSETS
# ─────────────────────────────────────────────

@app.get("/v1/assets", tags=["Assets"])
async def list_assets(
    center_id: str = Query(...),
    category: Optional[AssetCategory] = Query(None),
    status: Optional[AssetStatus] = Query(None),
    criticality: Optional[AssetCriticality] = Query(None),
    parent_id: Optional[str] = Query(None, description="null = raíz"),
    include_tree: bool = Query(False, description="Devuelve árbol anidado completo"),
    q: Optional[str] = Query(None, description="Búsqueda full-text"),
    cursor: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista activos con filtros. Soporta vista árbol y búsqueda full-text."""
    await set_tenant_rls(db, current_user["tenant_id"])

    filters = [
        Asset.tenant_id == current_user["tenant_id"],
        Asset.center_id == center_id,
        Asset.active == True,
    ]

    if category:
        filters.append(Asset.category == category)
    if status:
        filters.append(Asset.status == status)
    if criticality:
        filters.append(Asset.criticality == criticality)
    if parent_id == "null" or (parent_id is None and not include_tree):
        filters.append(Asset.parent_id.is_(None))
    elif parent_id and parent_id != "null":
        filters.append(Asset.parent_id == parent_id)

    if q:
        # En producción: usar Meilisearch. Aquí: ILIKE básico
        filters.append(
            or_(
                Asset.name.ilike(f"%{q}%"),
                Asset.code.ilike(f"%{q}%"),
            )
        )

    query = select(Asset).where(*filters).order_by(Asset.name).limit(limit)
    result = await db.execute(query)
    assets = result.scalars().all()

    def serialize_asset(a: Asset) -> dict:
        return {
            "id": a.id,
            "code": a.code,
            "name": a.name,
            "category": a.category,
            "status": a.status,
            "criticality": a.criticality,
            "parent_id": a.parent_id,
            "location": a.location,
            "specs": a.specs,
            "warranty_until": a.warranty_until.isoformat() if a.warranty_until else None,
            "qr_code": a.qr_code,
        }

    return {"data": [serialize_asset(a) for a in assets]}


@app.post("/v1/assets", status_code=status.HTTP_201_CREATED, tags=["Assets"])
async def create_asset(
    body: AssetCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Crea un activo y genera QR automáticamente."""
    await set_tenant_rls(db, current_user["tenant_id"])

    asset_id = str(uuid.uuid4())

    # Auto-generar código si no se proporciona
    code = body.code
    if not code:
        prefix = body.category.value[:4].upper()
        floor = body.location.get("floor", "XX").replace(" ", "")[:3].upper()
        short_id = asset_id[:4].upper()
        code = f"{prefix}-{floor}-{short_id}"

    # URL del QR (en producción: generar imagen QR real con qrcode lib)
    qr_url = f"https://api.fmplatform.io/v1/assets/{asset_id}/qr.png"

    asset = Asset(
        id=asset_id,
        tenant_id=current_user["tenant_id"],
        center_id=body.center_id,
        parent_id=body.parent_id,
        code=code,
        name=body.name,
        category=body.category,
        criticality=body.criticality,
        location=body.location,
        specs=body.specs,
        purchase_date=body.purchase_date,
        warranty_until=body.warranty_until,
        expected_life_years=body.expected_life_years,
        serial_number=body.serial_number,
        manufacturer=body.manufacturer,
        model=body.model,
        qr_code=qr_url,
    )

    db.add(asset)
    await db.commit()

    return {
        "id": asset.id,
        "code": asset.code,
        "name": asset.name,
        "qr_code_url": qr_url,
        "created_at": asset.created_at.isoformat(),
    }


@app.post("/v1/assets/bulk-import", tags=["Assets"])
async def bulk_import_assets(
    body: AssetBulkImport,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Importación masiva de activos desde CSV.
    Crítico para onboarding: permite cargar 600 activos en una sola operación.
    """
    await set_tenant_rls(db, current_user["tenant_id"])

    created = 0
    skipped = 0
    errors = []

    for i, asset_data in enumerate(body.assets):
        try:
            # Verificar duplicado
            if body.skip_duplicates and asset_data.code:
                existing = await db.execute(
                    select(Asset).where(
                        Asset.tenant_id == current_user["tenant_id"],
                        Asset.center_id == body.center_id,
                        Asset.code == asset_data.code,
                    )
                )
                if existing.scalar_one_or_none():
                    skipped += 1
                    continue

            asset_id = str(uuid.uuid4())
            code = asset_data.code or f"{asset_data.category.value[:4].upper()}-{asset_id[:6].upper()}"

            asset = Asset(
                id=asset_id,
                tenant_id=current_user["tenant_id"],
                center_id=body.center_id,
                parent_id=asset_data.parent_id,
                code=code,
                name=asset_data.name,
                category=asset_data.category,
                criticality=asset_data.criticality,
                location=asset_data.location,
                specs=asset_data.specs,
                qr_code=f"https://api.fmplatform.io/v1/assets/{asset_id}/qr.png",
            )
            db.add(asset)
            created += 1

        except Exception as e:
            errors.append({"row": i, "error": str(e)})

    await db.commit()

    return {
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "total_processed": len(body.assets),
    }


# ─────────────────────────────────────────────
# IoT SENSORS
# ─────────────────────────────────────────────

@app.post("/v1/sensors/ingest", status_code=status.HTTP_202_ACCEPTED, tags=["IoT"])
async def ingest_sensor_readings(
    body: SensorIngestRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ingestión batch de lecturas IoT (hasta 500 por request).
    202 Accepted: el procesamiento de alertas es asíncrono.
    """
    await set_tenant_rls(db, current_user["tenant_id"])

    wo_svc = WorkOrderService(db)
    iot_svc = IoTAlertService(db, wo_svc)

    stats = await iot_svc.process_readings(
        readings=[r.dict() for r in body.readings]
    )
    await db.commit()

    return stats


@app.get("/v1/sensors/{sensor_id}/readings", tags=["IoT"])
async def get_sensor_readings(
    sensor_id: str,
    from_date: datetime = Query(...),
    to_date: datetime = Query(...),
    bucket: Optional[str] = Query(None, description="1m | 5m | 1h | 1d"),
    aggregate: Optional[str] = Query(None, description="avg | min | max | sum"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Serie temporal de un sensor con agregación opcional.
    Datos almacenados en TimescaleDB (hypertable).
    """
    await set_tenant_rls(db, current_user["tenant_id"])

    # Verificar que el sensor pertenece al tenant
    sensor = await db.get(Sensor, sensor_id)
    if not sensor or sensor.tenant_id != current_user["tenant_id"]:
        return problem_response(404, "Sensor not found", f"Sensor {sensor_id} not found", "not-found")

    # En producción: query a TimescaleDB con time_bucket para agregación
    # SELECT time_bucket('1 hour', time) AS bucket, avg(value) FROM sensor_readings
    # WHERE sensor_id = :id AND time BETWEEN :from AND :to GROUP BY bucket ORDER BY bucket

    # Mock response
    return {
        "sensor_id": sensor_id,
        "metric_type": sensor.metric_type,
        "unit": sensor.unit,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "bucket": bucket,
        "aggregate": aggregate,
        "data": [],  # En producción: resultado de TimescaleDB
    }


# ─────────────────────────────────────────────
# ANALYTICS
# ─────────────────────────────────────────────

@app.get("/v1/analytics/kpis", tags=["Analytics"])
async def get_kpis(
    center_id: Optional[str] = Query(None),
    period: str = Query("last_30d"),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """KPIs operativos: MTTR, MTBF, cumplimiento SLA, costo por m²."""
    await set_tenant_rls(db, current_user["tenant_id"])

    # Calcular rango de fechas desde period
    now = datetime.now(timezone.utc)
    period_map = {
        "last_7d":  timedelta(days=7),
        "last_30d": timedelta(days=30),
        "last_90d": timedelta(days=90),
        "ytd":      timedelta(days=(now.timetuple().tm_yday - 1)),
    }

    if period != "custom":
        delta = period_map.get(period, timedelta(days=30))
        from_date = now - delta
        to_date = now

    svc = AnalyticsService(db)
    kpis = await svc.get_kpis(
        tenant_id=current_user["tenant_id"],
        center_id=center_id,
        from_date=from_date,
        to_date=to_date,
    )
    return kpis


@app.get("/v1/analytics/sla-report", tags=["Analytics"])
async def get_sla_report(
    center_id: Optional[str] = Query(None),
    vendor_id: Optional[str] = Query(None),
    period: str = Query("last_30d"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reporte de cumplimiento SLA por proveedor y tipo de servicio."""
    await set_tenant_rls(db, current_user["tenant_id"])

    now = datetime.now(timezone.utc)
    from_date = now - timedelta(days=30)

    # En producción: query compleja con JOINs a contracts y vendors
    return {
        "period": {"from": from_date.isoformat(), "to": now.isoformat()},
        "overall_compliance_pct": 94.2,
        "by_priority": {
            "emergency": {"compliance": 88.0, "total": 8, "breached": 1},
            "high":      {"compliance": 95.0, "total": 40, "breached": 2},
            "medium":    {"compliance": 98.0, "total": 120, "breached": 2},
            "low":       {"compliance": 99.0, "total": 80, "breached": 1},
        },
        "by_vendor": [],  # En producción: por vendor con contrato
    }


# ─────────────────────────────────────────────
# WEBHOOKS CONFIG
# ─────────────────────────────────────────────

@app.get("/v1/webhooks", tags=["Webhooks"])
async def list_webhooks(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista los endpoints de webhook configurados para el tenant."""
    return {"data": [], "events_available": [
        "work_order.created", "work_order.assigned", "work_order.status_changed",
        "work_order.sla_breached", "work_order.closed",
        "asset.status_changed", "sensor.alert_triggered", "sensor.alert_resolved",
        "pm_plan.work_order_generated", "contract.expiring_soon",
        "checklist.completed", "user.created",
    ]}


# ─────────────────────────────────────────────
# TENANT PORTAL ENDPOINTS
# ─────────────────────────────────────────────

@app.get("/v1/portal/requests", tags=["Tenant Portal"])
async def list_tenant_requests(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista solicitudes del portal de inquilinos para el usuario actual."""
    from .models import TenantRequest
    await set_tenant_rls(db, current_user["tenant_id"])

    result = await db.execute(
        select(TenantRequest)
        .where(
            TenantRequest.tenant_id == current_user["tenant_id"],
            TenantRequest.requester_id == current_user["user_id"],
        )
        .order_by(TenantRequest.created_at.desc())
        .limit(50)
    )
    requests = result.scalars().all()
    return {"data": [{"id": r.id, "title": r.title, "status": r.status} for r in requests]}


# ─────────────────────────────────────────────
# RUN SERVER (desarrollo)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
