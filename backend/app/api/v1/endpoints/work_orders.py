"""
app/api/v1/endpoints/work_orders.py — CRUD + State Machine de Órdenes de Trabajo
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, not_
from sqlalchemy.orm import selectinload
from typing import Optional, List
from datetime import datetime, timezone
import uuid

from app.core.database import get_db
from app.core.auth import get_current_tenant, require_scope
from app.models.models import WorkOrder, WorkOrderStatus, WorkOrderPriority, Asset
from app.schemas.schemas import (
    WorkOrderCreate, WorkOrderTransition, WorkOrderResponse,
    ChecklistBatchUpdate, PresignRequest, PresignResponse,
    AttachmentConfirm, PaginatedResponse
)
from app.services.wo_service import WorkOrderService
from app.services.sla_service import SLAService
from app.services.notification_service import NotificationService
from app.services.s3_service import S3Service
from app.services.webhook_service import WebhookService

router = APIRouter()


# ── VALIDACIONES DE TRANSICIÓN (State Machine) ────────────────────────────────

TRANSITIONS: dict[WorkOrderStatus, dict[str, WorkOrderStatus]] = {
    WorkOrderStatus.DRAFT:       {"submit": WorkOrderStatus.PENDING},
    WorkOrderStatus.PENDING:     {"approve": WorkOrderStatus.APPROVED, "reject": WorkOrderStatus.CANCELLED},
    WorkOrderStatus.APPROVED:    {"assign": WorkOrderStatus.ASSIGNED},
    WorkOrderStatus.ASSIGNED:    {"start": WorkOrderStatus.IN_PROGRESS, "reassign": WorkOrderStatus.ASSIGNED},
    WorkOrderStatus.IN_PROGRESS: {
        "pause": WorkOrderStatus.PAUSED,
        "complete": WorkOrderStatus.COMPLETED,
        "escalate": WorkOrderStatus.IN_PROGRESS,
    },
    WorkOrderStatus.PAUSED:      {"resume": WorkOrderStatus.IN_PROGRESS},
    WorkOrderStatus.COMPLETED:   {"verify": WorkOrderStatus.VERIFIED, "reopen": WorkOrderStatus.IN_PROGRESS},
    WorkOrderStatus.VERIFIED:    {"close": WorkOrderStatus.CLOSED},
}
# Cancel disponible desde cualquier estado (excepto closed)
CANCEL_FORBIDDEN = {WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED}


def validate_transition(current: WorkOrderStatus, action: str) -> WorkOrderStatus:
    """Valida y devuelve el nuevo estado, o lanza 409."""
    if action == "cancel" and current not in CANCEL_FORBIDDEN:
        return WorkOrderStatus.CANCELLED
    allowed = TRANSITIONS.get(current, {})
    if action not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "type": "https://api.fmplatform.io/errors/invalid-transition",
                "title": "Invalid state transition",
                "status": 409,
                "detail": f"Cannot '{action}' from '{current}'. Valid actions: {list(allowed.keys())}",
            }
        )
    return allowed[action]


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse)
async def list_work_orders(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    center_id: Optional[uuid.UUID] = Query(None),
    asset_id: Optional[uuid.UUID] = Query(None),
    assigned_to: Optional[uuid.UUID] = Query(None),
    sla_overdue: Optional[bool] = Query(None),
    created_after: Optional[datetime] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    sort: str = Query("-sla_deadline"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:read")),
):
    """Lista OTs con filtros y paginación por cursor."""
    query = select(WorkOrder).where(WorkOrder.tenant_id == tenant_id)

    if status:
        query = query.where(WorkOrder.status == status)
    if type:
        query = query.where(WorkOrder.type == type)
    if priority:
        query = query.where(WorkOrder.priority == priority)
    if center_id:
        query = query.where(WorkOrder.center_id == center_id)
    if asset_id:
        query = query.where(WorkOrder.asset_id == asset_id)
    if assigned_to:
        query = query.where(WorkOrder.assigned_to == assigned_to)
    if sla_overdue is True:
        now = datetime.now(timezone.utc)
        query = query.where(
            and_(WorkOrder.sla_deadline < now,
                 WorkOrder.status.not_in(["closed", "cancelled"]))
        )
    if created_after:
        query = query.where(WorkOrder.created_at >= created_after)

    # Cursor pagination
    if cursor:
        cursor_dt = WorkOrderService.decode_cursor(cursor)
        query = query.where(WorkOrder.sla_deadline > cursor_dt)

    # Sorting
    if sort.startswith("-"):
        query = query.order_by(getattr(WorkOrder, sort[1:]).desc())
    else:
        query = query.order_by(getattr(WorkOrder, sort).asc())

    query = query.limit(limit + 1)

    result = await db.execute(query)
    rows = result.scalars().all()

    has_more = len(rows) > limit
    items = rows[:limit]

    now = datetime.now(timezone.utc)
    data = []
    for wo in items:
        remaining = None
        if wo.sla_deadline:
            diff = (wo.sla_deadline - now).total_seconds() / 60
            remaining = int(diff)
        data.append({
            **WorkOrderResponse.model_validate(wo).model_dump(),
            "sla_overdue": wo.sla_deadline and wo.sla_deadline < now,
            "sla_minutes_remaining": remaining,
        })

    next_cursor = WorkOrderService.encode_cursor(items[-1].sla_deadline) if has_more and items else None

    return {"data": data, "pagination": {"cursor": next_cursor, "has_more": has_more}}


@router.get("/{wo_id}", response_model=WorkOrderResponse)
async def get_work_order(
    wo_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
):
    query = (
        select(WorkOrder)
        .where(WorkOrder.id == wo_id, WorkOrder.tenant_id == tenant_id)
        .options(
            selectinload(WorkOrder.checklist_items),
            selectinload(WorkOrder.attachments),
            selectinload(WorkOrder.time_logs),
            selectinload(WorkOrder.comments),
        )
    )
    result = await db.execute(query)
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    return wo


@router.post("", response_model=WorkOrderResponse, status_code=201)
async def create_work_order(
    body: WorkOrderCreate,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:write")),
):
    """Crea una nueva OT. Soporta Idempotency-Key para reintentos seguros."""
    svc = WorkOrderService(db, tenant_id)

    # Idempotency check (Redis)
    if idempotency_key:
        existing = await svc.check_idempotency(idempotency_key)
        if existing:
            raise HTTPException(status_code=409, detail="Duplicate request")

    # Validar que el asset pertenece al center
    if body.asset_id:
        asset = await db.get(Asset, body.asset_id)
        if not asset or asset.center_id != body.center_id:
            raise HTTPException(
                status_code=422,
                detail="asset_id does not belong to the specified center_id"
            )

    wo = await svc.create(body)

    # Calcular SLA automáticamente desde contrato
    if body.contract_id:
        wo.sla_deadline = await SLAService(db).calculate(
            body.contract_id, body.priority
        )

    # Generar código único: OT-25-0342
    wo.code = await svc.generate_code(wo.created_at.year)

    await db.commit()
    await db.refresh(wo)

    # Notificar si hay técnico asignado
    if wo.assigned_to:
        await NotificationService.send_push(
            user_id=wo.assigned_to,
            title="Nueva OT asignada",
            body=f"{wo.code} — {wo.title}",
            data={"wo_id": str(wo.id)}
        )

    # Webhook
    await WebhookService.dispatch(tenant_id, "work_order.created", {
        "id": str(wo.id), "code": wo.code, "priority": wo.priority
    })

    if idempotency_key:
        await svc.store_idempotency(idempotency_key, wo.id)

    return wo


@router.patch("/{wo_id}", response_model=WorkOrderResponse)
async def update_work_order(
    wo_id: uuid.UUID,
    body: dict,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:write")),
):
    """Actualiza campos editables de una OT (no el estado — usar /transition)."""
    wo = await db.get(WorkOrder, wo_id)
    if not wo or wo.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Work order not found")

    editable = {"title", "description", "estimated_cost", "assigned_to",
                "scheduled_for", "metadata", "priority"}
    for key, val in body.items():
        if key in editable:
            setattr(wo, key, val)

    await db.commit()
    await db.refresh(wo)
    return wo


@router.post("/{wo_id}/transition", response_model=WorkOrderResponse)
async def transition_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderTransition,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:write")),
):
    """
    Cambia el estado de una OT siguiendo la state machine.
    Dispara webhooks y notificaciones automáticamente.
    """
    wo = await db.get(WorkOrder, wo_id)
    if not wo or wo.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Work order not found")

    prev_status = wo.status
    new_status = validate_transition(wo.status, body.action)
    wo.status = new_status

    now = datetime.now(timezone.utc)

    # Efectos secundarios por acción
    if body.action == "start":
        wo.started_at = now
    elif body.action in ("close", "complete"):
        wo.closed_at = now
        if body.resolution:
            wo.resolution = body.resolution
        if body.actual_cost is not None:
            wo.actual_cost = body.actual_cost
    elif body.action in ("assign", "reassign") and body.assigned_to:
        wo.assigned_to = body.assigned_to

    # Actualizar MTTR en cierre
    if body.action == "close" and wo.started_at:
        mttr_minutes = (now - wo.started_at).total_seconds() / 60
        wo.metadata["mttr_minutes"] = int(mttr_minutes)

    await db.commit()
    await db.refresh(wo)

    # Webhook
    await WebhookService.dispatch(tenant_id, "work_order.status_changed", {
        "id": str(wo.id), "code": wo.code,
        "prev_status": prev_status, "new_status": new_status,
        "changed_by": "current_user",  # reemplazar con usuario real
        "timestamp": now.isoformat(),
    })

    return wo


@router.patch("/{wo_id}/checklist")
async def update_checklist(
    wo_id: uuid.UUID,
    body: ChecklistBatchUpdate,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:write")),
):
    """Actualización batch de checklist — compatible con sync offline móvil."""
    svc = WorkOrderService(db, tenant_id)
    updated = await svc.batch_update_checklist(wo_id, body.items)
    return {"updated": updated}


@router.post("/{wo_id}/attachments/presign", response_model=PresignResponse)
async def presign_attachment(
    wo_id: uuid.UUID,
    body: PresignRequest,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:write")),
):
    """Genera URL pre-firmada de S3 para upload directo desde cliente/móvil."""
    s3 = S3Service()
    attachment_id = uuid.uuid4()
    s3_key = f"{tenant_id}/work-orders/{wo_id}/attachments/{attachment_id}/{body.filename}"
    upload_url = await s3.presign_put(s3_key, body.content_type)
    # Registrar attachment en BD como "pending"
    await WorkOrderService(db, tenant_id).create_attachment_pending(
        wo_id, attachment_id, s3_key, body
    )
    return PresignResponse(upload_url=upload_url, attachment_id=attachment_id)


@router.post("/{wo_id}/attachments/confirm")
async def confirm_attachment(
    wo_id: uuid.UUID,
    body: AttachmentConfirm,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:write")),
):
    """Confirma que el upload a S3 se completó."""
    await WorkOrderService(db, tenant_id).confirm_attachment(
        wo_id, body.attachment_id, body.type
    )
    return {"confirmed": True}


@router.post("/{wo_id}/time-logs")
async def log_time(
    wo_id: uuid.UUID,
    body: dict,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:write")),
):
    """Registra tiempo trabajado en una OT (para MTTR y facturación)."""
    svc = WorkOrderService(db, tenant_id)
    log = await svc.add_time_log(wo_id, body)
    return {"id": str(log.id), "minutes": log.minutes}
