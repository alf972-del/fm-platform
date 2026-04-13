"""
api/work_orders.py — Router de Órdenes de Trabajo
===================================================
Endpoints completos para el ciclo de vida de OTs:
  GET    /work-orders          → lista paginada con filtros
  POST   /work-orders          → crear OT (con idempotency)
  GET    /work-orders/{id}     → detalle
  PATCH  /work-orders/{id}     → actualizar campos
  POST   /work-orders/{id}/transition        → cambiar estado (state machine)
  POST   /work-orders/{id}/attachments/presign   → URL pre-firmada S3
  POST   /work-orders/{id}/attachments/confirm   → confirmar upload
  PATCH  /work-orders/{id}/checklist             → batch update checklist
  POST   /work-orders/{id}/time-logs             → registrar tiempo trabajado
  GET    /work-orders/{id}/history               → historial de estados
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, text, desc
from sqlalchemy.orm import selectinload
from typing import Optional, List
from datetime import datetime, timedelta
import uuid
import hashlib
import hmac

from database import get_db
from models import WorkOrder, WorkOrderStatus, WorkOrderType, Priority, Asset, User, Contract
from schemas.work_orders import (
    WorkOrderCreate, WorkOrderUpdate, WorkOrderResponse, WorkOrderListResponse,
    WorkOrderTransition, WorkOrderTransitionResponse,
    ChecklistBatchUpdate, AttachmentPresign, AttachmentConfirm,
    TimeLogCreate, TimeLogResponse,
)
from services.work_orders import WorkOrderService
from services.sla import SLACalculator
from services.notifications import NotificationService
from services.webhooks import WebhookService
from middleware.auth import get_current_user, require_scope
from utils.pagination import CursorPagination

router = APIRouter()


# ── STATE MACHINE ─────────────────────────────────────
# Define transiciones válidas y roles que pueden ejecutarlas
VALID_TRANSITIONS = {
    WorkOrderStatus.DRAFT: {
        "submit": {"next": WorkOrderStatus.PENDING, "roles": ["staff", "technician", "tenant_contact"]},
    },
    WorkOrderStatus.PENDING: {
        "approve": {"next": WorkOrderStatus.APPROVED,  "roles": ["staff", "admin"]},
        "reject":  {"next": WorkOrderStatus.CANCELLED, "roles": ["staff", "admin"]},
    },
    WorkOrderStatus.APPROVED: {
        "assign": {"next": WorkOrderStatus.ASSIGNED, "roles": ["staff", "admin"]},
    },
    WorkOrderStatus.ASSIGNED: {
        "start":    {"next": WorkOrderStatus.IN_PROGRESS, "roles": ["technician", "staff", "admin"]},
        "reassign": {"next": WorkOrderStatus.ASSIGNED,    "roles": ["staff", "admin"]},
    },
    WorkOrderStatus.IN_PROGRESS: {
        "pause":    {"next": WorkOrderStatus.PAUSED,    "roles": ["technician", "staff", "admin"]},
        "complete": {"next": WorkOrderStatus.COMPLETED, "roles": ["technician", "staff", "admin"]},
        "escalate": {"next": WorkOrderStatus.PENDING,   "roles": ["technician", "staff", "admin"]},
    },
    WorkOrderStatus.PAUSED: {
        "resume":   {"next": WorkOrderStatus.IN_PROGRESS, "roles": ["technician", "staff", "admin"]},
        "cancel":   {"next": WorkOrderStatus.CANCELLED,   "roles": ["staff", "admin"]},
    },
    WorkOrderStatus.COMPLETED: {
        "verify": {"next": WorkOrderStatus.VERIFIED, "roles": ["staff", "admin"]},
        "reopen": {"next": WorkOrderStatus.IN_PROGRESS, "roles": ["staff", "admin"]},
    },
    WorkOrderStatus.VERIFIED: {
        "close": {"next": WorkOrderStatus.CLOSED, "roles": ["staff", "admin"]},
    },
}
# "cancel" está disponible desde cualquier estado (solo staff/admin)
UNIVERSAL_TRANSITIONS = {
    "cancel": {"next": WorkOrderStatus.CANCELLED, "roles": ["staff", "admin"]}
}


# ── ENDPOINTS ─────────────────────────────────────────

@router.get("", response_model=WorkOrderListResponse)
async def list_work_orders(
    # Filtros
    status: Optional[str]        = Query(None, description="Estado de la OT"),
    type: Optional[str]          = Query(None, description="Tipo de OT"),
    priority: Optional[str]      = Query(None, description="Prioridad"),
    center_id: Optional[uuid.UUID] = Query(None),
    asset_id: Optional[uuid.UUID]  = Query(None),
    assigned_to: Optional[uuid.UUID] = Query(None),
    sla_overdue: Optional[bool]  = Query(None, description="Solo OTs con SLA vencido"),
    created_after: Optional[datetime] = Query(None),
    created_before: Optional[datetime] = Query(None),
    # Paginación
    cursor: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    sort: str = Query("-sla_deadline", description="Campo de ordenación. Prefijo - para desc"),
    # Auth
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Lista OTs del tenant con filtros, paginación cursor y ordenación.
    RLS garantiza aislamiento por tenant automáticamente.
    """
    service = WorkOrderService(db, current_user.tenant_id)
    
    filters = {
        "status": status,
        "type": type,
        "priority": priority,
        "center_id": center_id,
        "asset_id": asset_id,
        "assigned_to": assigned_to,
        "sla_overdue": sla_overdue,
        "created_after": created_after,
        "created_before": created_before,
    }
    
    result = await service.list(
        filters={k: v for k, v in filters.items() if v is not None},
        cursor=cursor,
        limit=limit,
        sort=sort,
    )
    
    return result


@router.post("", response_model=WorkOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_work_order(
    data: WorkOrderCreate,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Crea una nueva OT.
    - Calcula SLA automáticamente desde el contrato o tipo/prioridad.
    - Soporta Idempotency-Key para prevenir duplicados.
    - Dispara webhook work_order.created.
    """
    service = WorkOrderService(db, current_user.tenant_id)
    
    # Verificar idempotency key si se proporcionó
    if idempotency_key:
        existing = await service.find_by_idempotency_key(idempotency_key)
        if existing:
            return existing
    
    # Calcular SLA
    sla_calculator = SLACalculator(db)
    sla_deadline = await sla_calculator.calculate(
        tenant_id=current_user.tenant_id,
        priority=data.priority,
        contract_id=data.contract_id,
    )
    
    # Crear OT
    work_order = await service.create(
        data=data,
        created_by=current_user.id,
        sla_deadline=sla_deadline,
        idempotency_key=idempotency_key,
    )
    
    # Notificar
    notif = NotificationService()
    if work_order.assigned_to:
        await notif.push_to_user(
            user_id=work_order.assigned_to,
            title="Nueva OT asignada",
            body=f"{work_order.code}: {work_order.title}",
            data={"work_order_id": str(work_order.id)},
        )
    
    # Webhook
    webhook_svc = WebhookService(db)
    await webhook_svc.dispatch(
        tenant_id=current_user.tenant_id,
        event="work_order.created",
        payload={"work_order_id": str(work_order.id), "code": work_order.code},
    )
    
    return work_order


@router.get("/{work_order_id}", response_model=WorkOrderResponse)
async def get_work_order(
    work_order_id: uuid.UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Detalle completo de una OT con checklist, adjuntos e historial."""
    service = WorkOrderService(db, current_user.tenant_id)
    work_order = await service.get_by_id(work_order_id)
    if not work_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "type": "https://api.fmplatform.io/errors/not-found",
                "title": "Work Order Not Found",
                "status": 404,
                "detail": f"Work order {work_order_id} not found",
            }
        )
    return work_order


@router.patch("/{work_order_id}", response_model=WorkOrderResponse)
async def update_work_order(
    work_order_id: uuid.UUID,
    data: WorkOrderUpdate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Actualiza campos de una OT (no cambia estado — usar /transition)."""
    service = WorkOrderService(db, current_user.tenant_id)
    return await service.update(work_order_id, data)


@router.post("/{work_order_id}/transition", response_model=WorkOrderTransitionResponse)
async def transition_work_order(
    work_order_id: uuid.UUID,
    data: WorkOrderTransition,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cambia el estado de una OT mediante la state machine.
    
    Valida:
    - La transición es válida desde el estado actual
    - El usuario tiene el rol necesario para ejecutar la acción
    - Los campos requeridos para la acción están presentes
    
    Efectos secundarios:
    - Actualiza timestamps (started_at, completed_at, closed_at)
    - Calcula MTTR si se cierra
    - Dispara webhook work_order.status_changed
    - Envía notificación push si se asigna
    - Verifica SLA breach
    """
    service = WorkOrderService(db, current_user.tenant_id)
    work_order = await service.get_by_id(work_order_id)
    
    if not work_order:
        raise HTTPException(status_code=404, detail="Work order not found")
    
    # Validar transición
    action = data.action
    current_status = work_order.status
    
    # Verificar si es acción universal (cancel)
    if action in UNIVERSAL_TRANSITIONS:
        transition = UNIVERSAL_TRANSITIONS[action]
    elif current_status in VALID_TRANSITIONS and action in VALID_TRANSITIONS[current_status]:
        transition = VALID_TRANSITIONS[current_status][action]
    else:
        valid_actions = list(VALID_TRANSITIONS.get(current_status, {}).keys())
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "type": "https://api.fmplatform.io/errors/invalid-transition",
                "title": "Invalid State Transition",
                "status": 409,
                "detail": f"Cannot execute '{action}' from status '{current_status}'. Valid actions: {valid_actions}",
                "errors": [{"field": "action", "message": f"'{action}' is not valid from '{current_status}'"}]
            }
        )
    
    # Verificar rol
    if current_user.user_type not in transition["roles"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "type": "https://api.fmplatform.io/errors/forbidden",
                "title": "Forbidden",
                "status": 403,
                "detail": f"Role '{current_user.user_type}' cannot execute '{action}'",
            }
        )
    
    # Validar campos requeridos por acción
    if action == "complete" and not data.resolution:
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"field": "resolution", "message": "Required when completing a work order"}]}
        )
    if action in ("assign", "reassign") and not data.assigned_to:
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"field": "assigned_to", "message": "Required when assigning"}]}
        )
    
    # Ejecutar transición
    updated = await service.transition(
        work_order=work_order,
        next_status=transition["next"],
        action=action,
        comment=data.comment,
        assigned_to=data.assigned_to,
        resolution=data.resolution,
        actual_cost=data.actual_cost,
        executed_by=current_user.id,
    )
    
    # Verificar SLA breach
    if updated.sla_overdue and action not in ("close", "cancel"):
        webhook_svc = WebhookService(db)
        await webhook_svc.dispatch(
            tenant_id=current_user.tenant_id,
            event="work_order.sla_breached",
            payload={"work_order_id": str(updated.id), "code": updated.code},
        )
    
    # Webhook status changed
    webhook_svc = WebhookService(db)
    await webhook_svc.dispatch(
        tenant_id=current_user.tenant_id,
        event="work_order.status_changed",
        payload={
            "work_order_id": str(updated.id),
            "code": updated.code,
            "prev_status": current_status,
            "new_status": updated.status,
            "changed_by": str(current_user.id),
        },
    )
    
    return WorkOrderTransitionResponse(
        work_order=updated,
        action_performed=action,
        previous_status=current_status,
        new_status=updated.status,
    )


@router.post("/{work_order_id}/attachments/presign")
async def presign_attachment(
    work_order_id: uuid.UUID,
    data: AttachmentPresign,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Genera URL pre-firmada de S3 para subir un adjunto directamente.
    
    Flujo:
    1. Cliente llama a este endpoint → recibe upload_url y attachment_id
    2. Cliente hace PUT a upload_url con el archivo
    3. Cliente llama a /confirm para registrar el upload
    """
    from services.storage import StorageService
    
    service = WorkOrderService(db, current_user.tenant_id)
    work_order = await service.get_by_id(work_order_id)
    if not work_order:
        raise HTTPException(status_code=404, detail="Work order not found")
    
    storage = StorageService()
    attachment_id = uuid.uuid4()
    s3_key = f"tenants/{current_user.tenant_id}/work-orders/{work_order_id}/attachments/{attachment_id}/{data.filename}"
    
    upload_url = await storage.generate_presigned_put_url(
        key=s3_key,
        content_type=data.content_type,
        expiry_seconds=900,  # 15 minutos
    )
    
    return {
        "upload_url": upload_url,
        "attachment_id": str(attachment_id),
        "expires_in": 900,
    }


@router.post("/{work_order_id}/attachments/confirm")
async def confirm_attachment(
    work_order_id: uuid.UUID,
    data: AttachmentConfirm,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirma que el upload a S3 fue exitoso y registra el adjunto en BD."""
    service = WorkOrderService(db, current_user.tenant_id)
    attachment = await service.confirm_attachment(
        work_order_id=work_order_id,
        attachment_id=data.attachment_id,
        attachment_type=data.type,
        uploaded_by=current_user.id,
    )
    
    # Webhook
    webhook_svc = WebhookService(db)
    await webhook_svc.dispatch(
        tenant_id=current_user.tenant_id,
        event="checklist.completed",
        payload={"work_order_id": str(work_order_id), "attachment_id": str(data.attachment_id)},
    )
    
    return {"status": "confirmed", "attachment_id": str(data.attachment_id)}


@router.patch("/{work_order_id}/checklist")
async def update_checklist(
    work_order_id: uuid.UUID,
    data: ChecklistBatchUpdate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Batch update de ítems del checklist.
    Diseñado para offline-first: el móvil envía todos los cambios
    acumulados offline en una sola llamada al reconectar.
    
    El timestamp completed_at del dispositivo se usa para resolver
    conflictos (last-write-wins por ítem).
    """
    service = WorkOrderService(db, current_user.tenant_id)
    
    result = await service.batch_update_checklist(
        work_order_id=work_order_id,
        items=data.items,
        updated_by=current_user.id,
    )
    
    return {
        "updated": result["updated"],
        "skipped": result["skipped"],
        "work_order_id": str(work_order_id),
    }


@router.post("/{work_order_id}/time-logs", response_model=TimeLogResponse)
async def create_time_log(
    work_order_id: uuid.UUID,
    data: TimeLogCreate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Registra tiempo trabajado por un técnico en una OT."""
    service = WorkOrderService(db, current_user.tenant_id)
    
    time_log = await service.create_time_log(
        work_order_id=work_order_id,
        user_id=current_user.id,
        started_at=data.started_at,
        ended_at=data.ended_at,
        notes=data.notes,
    )
    
    return time_log


@router.get("/{work_order_id}/history")
async def get_work_order_history(
    work_order_id: uuid.UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Historial completo de transiciones de estado de una OT."""
    service = WorkOrderService(db, current_user.tenant_id)
    history = await service.get_history(work_order_id)
    return {"data": history, "work_order_id": str(work_order_id)}
