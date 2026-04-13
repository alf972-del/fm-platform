"""
FM Platform — Servicios de Negocio (Python/FastAPI)
Lógica de Work Orders, SLA engine, PM scheduler y IoT alerts
"""
from __future__ import annotations

import uuid
import hashlib
import hmac
import json
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from enum import Enum

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_, or_, text, func
from sqlalchemy.orm import selectinload

from .models import (
    WorkOrder, WorkOrderStatus, WorkOrderPriority, WorkOrderType,
    WOTransition, WOChecklistItem, PMPlan, Sensor, Asset,
    User, Contract, Tenant, Center
)


# ─────────────────────────────────────────────
# STATE MACHINE — WORK ORDERS
# ─────────────────────────────────────────────

class WorkOrderAction(str, Enum):
    SUBMIT    = "submit"
    APPROVE   = "approve"
    REJECT    = "reject"
    ASSIGN    = "assign"
    REASSIGN  = "reassign"
    START     = "start"
    PAUSE     = "pause"
    RESUME    = "resume"
    COMPLETE  = "complete"
    ESCALATE  = "escalate"
    VERIFY    = "verify"
    REOPEN    = "reopen"
    CLOSE     = "close"
    CANCEL    = "cancel"


# Transiciones válidas por estado actual
VALID_TRANSITIONS: Dict[WorkOrderStatus, Dict[WorkOrderAction, WorkOrderStatus]] = {
    WorkOrderStatus.DRAFT: {
        WorkOrderAction.SUBMIT: WorkOrderStatus.PENDING,
        WorkOrderAction.CANCEL: WorkOrderStatus.CANCELLED,
    },
    WorkOrderStatus.PENDING: {
        WorkOrderAction.APPROVE: WorkOrderStatus.APPROVED,
        WorkOrderAction.REJECT:  WorkOrderStatus.CANCELLED,
        WorkOrderAction.CANCEL:  WorkOrderStatus.CANCELLED,
    },
    WorkOrderStatus.APPROVED: {
        WorkOrderAction.ASSIGN:  WorkOrderStatus.ASSIGNED,
        WorkOrderAction.CANCEL:  WorkOrderStatus.CANCELLED,
    },
    WorkOrderStatus.ASSIGNED: {
        WorkOrderAction.START:    WorkOrderStatus.IN_PROGRESS,
        WorkOrderAction.REASSIGN: WorkOrderStatus.ASSIGNED,
        WorkOrderAction.CANCEL:   WorkOrderStatus.CANCELLED,
    },
    WorkOrderStatus.IN_PROGRESS: {
        WorkOrderAction.PAUSE:    WorkOrderStatus.PAUSED,
        WorkOrderAction.COMPLETE: WorkOrderStatus.COMPLETED,
        WorkOrderAction.ESCALATE: WorkOrderStatus.IN_PROGRESS,  # escala pero no cambia estado
        WorkOrderAction.CANCEL:   WorkOrderStatus.CANCELLED,
    },
    WorkOrderStatus.PAUSED: {
        WorkOrderAction.RESUME:   WorkOrderStatus.IN_PROGRESS,
        WorkOrderAction.CANCEL:   WorkOrderStatus.CANCELLED,
    },
    WorkOrderStatus.COMPLETED: {
        WorkOrderAction.VERIFY:  WorkOrderStatus.VERIFIED,
        WorkOrderAction.REOPEN:  WorkOrderStatus.IN_PROGRESS,
    },
    WorkOrderStatus.VERIFIED: {
        WorkOrderAction.CLOSE:  WorkOrderStatus.CLOSED,
        WorkOrderAction.REOPEN: WorkOrderStatus.IN_PROGRESS,
    },
}

# Roles autorizados por acción
ACTION_ROLES: Dict[WorkOrderAction, List[str]] = {
    WorkOrderAction.SUBMIT:   ["staff", "technician", "tenant_contact"],
    WorkOrderAction.APPROVE:  ["fm_manager", "fm_director", "supervisor"],
    WorkOrderAction.REJECT:   ["fm_manager", "fm_director", "supervisor"],
    WorkOrderAction.ASSIGN:   ["fm_manager", "fm_director", "supervisor"],
    WorkOrderAction.REASSIGN: ["fm_manager", "fm_director", "supervisor"],
    WorkOrderAction.START:    ["technician", "fm_manager", "supervisor"],
    WorkOrderAction.PAUSE:    ["technician", "fm_manager", "supervisor"],
    WorkOrderAction.RESUME:   ["technician", "fm_manager", "supervisor"],
    WorkOrderAction.COMPLETE: ["technician"],
    WorkOrderAction.ESCALATE: ["technician", "fm_manager"],
    WorkOrderAction.VERIFY:   ["fm_manager", "supervisor", "fm_director"],
    WorkOrderAction.REOPEN:   ["fm_manager", "fm_director"],
    WorkOrderAction.CLOSE:    ["fm_manager", "fm_director", "supervisor"],
    WorkOrderAction.CANCEL:   ["fm_manager", "fm_director"],
}


class WorkOrderStateMachineError(Exception):
    pass


class WorkOrderService:
    """
    Servicio central de gestión de OTs.
    Incluye state machine, SLA calculator y generación de código.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── CREAR OT ──────────────────────────────

    async def create(
        self,
        tenant_id: str,
        center_id: str,
        wo_type: WorkOrderType,
        title: str,
        priority: WorkOrderPriority,
        created_by: str,
        asset_id: Optional[str] = None,
        space_id: Optional[str] = None,
        assigned_to: Optional[str] = None,
        contract_id: Optional[str] = None,
        pm_plan_id: Optional[str] = None,
        description: Optional[str] = None,
        estimated_cost: Optional[float] = None,
        metadata: Optional[dict] = None,
    ) -> WorkOrder:
        """Crea una OT nueva y calcula SLA automáticamente."""

        code = await self._generate_code(tenant_id)

        # Calcular SLA deadline según contrato o tipo/prioridad
        sla_deadline = await self._calculate_sla(
            tenant_id=tenant_id,
            center_id=center_id,
            priority=priority,
            contract_id=contract_id,
        )

        wo = WorkOrder(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            center_id=center_id,
            code=code,
            type=wo_type,
            status=WorkOrderStatus.PENDING,  # Auto-approved si no requiere aprobación
            priority=priority,
            title=title,
            description=description,
            asset_id=asset_id,
            space_id=space_id,
            assigned_to=assigned_to,
            created_by=created_by,
            contract_id=contract_id,
            pm_plan_id=pm_plan_id,
            sla_deadline=sla_deadline,
            estimated_cost=estimated_cost,
            metadata=metadata or {},
        )

        # Si hay asignación directa, pasar a ASSIGNED
        if assigned_to:
            wo.status = WorkOrderStatus.ASSIGNED

        self.db.add(wo)

        # Log de transición inicial
        transition = WOTransition(
            id=str(uuid.uuid4()),
            work_order_id=wo.id,
            from_status=None,
            to_status=wo.status,
            action="created",
            triggered_by=created_by,
        )
        self.db.add(transition)

        await self.db.flush()
        await self._dispatch_webhook(tenant_id, "work_order.created", {"id": wo.id, "code": wo.code})
        return wo

    # ── STATE MACHINE ─────────────────────────

    async def transition(
        self,
        work_order_id: str,
        action: WorkOrderAction,
        triggered_by_user_id: str,
        user_role: str,
        comment: Optional[str] = None,
        assigned_to: Optional[str] = None,
        resolution: Optional[str] = None,
        actual_cost: Optional[float] = None,
    ) -> WorkOrder:
        """Aplica una transición de estado validando roles y flujo."""

        wo = await self.db.get(WorkOrder, work_order_id, options=[selectinload(WorkOrder.transitions)])
        if not wo:
            raise WorkOrderStateMachineError(f"WorkOrder {work_order_id} not found")

        # Validar acción permitida desde estado actual
        current_transitions = VALID_TRANSITIONS.get(wo.status, {})
        if action not in current_transitions:
            valid = [a.value for a in current_transitions.keys()]
            raise WorkOrderStateMachineError(
                f"Action '{action}' not valid from status '{wo.status}'. "
                f"Valid actions: {valid}"
            )

        # Validar rol del usuario
        allowed_roles = ACTION_ROLES.get(action, [])
        if user_role not in allowed_roles:
            raise WorkOrderStateMachineError(
                f"Role '{user_role}' not authorized for action '{action}'"
            )

        prev_status = wo.status
        new_status = current_transitions[action]

        # Aplicar cambios específicos por acción
        now = datetime.now(timezone.utc)

        if action == WorkOrderAction.START:
            wo.started_at = now

        elif action in (WorkOrderAction.ASSIGN, WorkOrderAction.REASSIGN):
            if not assigned_to:
                raise WorkOrderStateMachineError("assigned_to required for assign/reassign")
            wo.assigned_to = assigned_to

        elif action == WorkOrderAction.COMPLETE:
            wo.completed_at = now
            if resolution:
                wo.resolution = resolution
            if actual_cost is not None:
                wo.actual_cost = actual_cost
            # Auto-verificar si no requiere aprobación
            # (configurable por tenant)

        elif action == WorkOrderAction.VERIFY:
            wo.verified_at = now

        elif action == WorkOrderAction.CLOSE:
            wo.closed_at = now

        # Actualizar estado
        wo.status = new_status

        # Verificar SLA breach
        if wo.sla_deadline and now > wo.sla_deadline and not wo.sla_breached:
            wo.sla_breached = True
            await self._dispatch_webhook(wo.tenant_id, "work_order.sla_breached", {"id": wo.id})

        # Registrar transición (audit log inmutable)
        log = WOTransition(
            id=str(uuid.uuid4()),
            work_order_id=wo.id,
            from_status=prev_status,
            to_status=new_status,
            action=action,
            triggered_by=triggered_by_user_id,
            comment=comment,
        )
        self.db.add(log)
        await self.db.flush()

        # Webhook de cambio de estado
        await self._dispatch_webhook(wo.tenant_id, "work_order.status_changed", {
            "id": wo.id,
            "code": wo.code,
            "prev_status": prev_status,
            "new_status": new_status,
            "changed_by": triggered_by_user_id,
        })

        return wo

    # ── CHECKLIST BATCH UPDATE ────────────────

    async def update_checklist(
        self,
        work_order_id: str,
        items: List[Dict[str, Any]],
    ) -> List[WOChecklistItem]:
        """
        Actualiza múltiples ítems de checklist en batch.
        Diseñado para sync offline: el dispositivo envía todos los cambios de una vez.
        """
        updated = []
        for item_data in items:
            result = await self.db.execute(
                select(WOChecklistItem).where(
                    WOChecklistItem.id == item_data["id"],
                    WOChecklistItem.work_order_id == work_order_id,
                )
            )
            item = result.scalar_one_or_none()
            if item:
                item.completed = item_data.get("completed", item.completed)
                item.value = item_data.get("value", item.value)
                item.note = item_data.get("note", item.note)
                if item_data.get("completed") and not item.completed_at:
                    # Usar timestamp del dispositivo si se proporciona
                    item.completed_at = item_data.get("completed_at") or datetime.now(timezone.utc)
                updated.append(item)
        await self.db.flush()
        return updated

    # ── SLA CALCULATOR ────────────────────────

    async def _calculate_sla(
        self,
        tenant_id: str,
        center_id: str,
        priority: WorkOrderPriority,
        contract_id: Optional[str] = None,
    ) -> Optional[datetime]:
        """
        Calcula el SLA deadline.
        Orden de prioridad: contrato específico > defaults del sistema.
        """
        # Intentar leer SLA del contrato
        if contract_id:
            contract = await self.db.get(Contract, contract_id)
            if contract and contract.sla_config:
                sla = contract.sla_config.get(priority.value)
                if sla:
                    hours = sla.get("response_hours", 24)
                    return datetime.now(timezone.utc) + timedelta(hours=hours)

        # Defaults del sistema por prioridad
        DEFAULT_SLA_HOURS = {
            WorkOrderPriority.EMERGENCY: 2,
            WorkOrderPriority.HIGH:      8,
            WorkOrderPriority.MEDIUM:    24,
            WorkOrderPriority.LOW:       72,
        }
        hours = DEFAULT_SLA_HOURS.get(priority, 24)
        return datetime.now(timezone.utc) + timedelta(hours=hours)

    # ── CODE GENERATOR ────────────────────────

    async def _generate_code(self, tenant_id: str) -> str:
        """Genera código secuencial único por tenant: OT-25-000341"""
        year = datetime.now().year % 100  # 25
        result = await self.db.execute(
            text("SELECT nextval('wo_sequence_:tenant'::regclass)")
            # En producción: sequence por tenant_id usando una tabla de secuencias
        )
        # Simplificado: usar COUNT + 1
        count_result = await self.db.execute(
            select(func.count()).select_from(WorkOrder).where(
                WorkOrder.tenant_id == tenant_id
            )
        )
        n = (count_result.scalar() or 0) + 1
        return f"OT-{year:02d}-{n:06d}"

    # ── WEBHOOK DISPATCH ──────────────────────

    async def _dispatch_webhook(
        self,
        tenant_id: str,
        event: str,
        data: dict,
    ) -> None:
        """
        Envía webhooks firmados con HMAC-SHA256 a los endpoints configurados.
        En producción esto va a una cola BullMQ/Redis para reintentos.
        """
        # TODO: leer endpoints desde config del tenant
        # TODO: encolar en Redis/BullMQ para reintentos exponenciales
        payload = {
            "event": event,
            "tenant_id": tenant_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        print(f"[WEBHOOK] {event}: {json.dumps(payload, default=str)}")


# ─────────────────────────────────────────────
# PM SCHEDULER SERVICE
# ─────────────────────────────────────────────

class PMSchedulerService:
    """
    Genera OTs preventivas automáticamente.
    Diseñado para ejecutarse vía APScheduler/Celery cada hora.
    """

    def __init__(self, db: AsyncSession, wo_service: WorkOrderService):
        self.db = db
        self.wo_service = wo_service

    async def run(self) -> Dict[str, int]:
        """
        Job principal: procesa todos los planes preventivos vencidos.
        Retorna estadísticas del run.
        """
        now = datetime.now(timezone.utc)
        stats = {"checked": 0, "generated": 0, "errors": 0}

        # Buscar planes vencidos
        result = await self.db.execute(
            select(PMPlan)
            .where(
                PMPlan.active == True,
                or_(
                    PMPlan.next_due_at <= now,
                    PMPlan.next_due_at.is_(None),
                )
            )
            .options(selectinload(PMPlan.asset))
            .limit(500)  # Batch de 500 para no saturar
        )
        plans = result.scalars().all()
        stats["checked"] = len(plans)

        for plan in plans:
            try:
                await self._process_plan(plan, now)
                stats["generated"] += 1
            except Exception as e:
                stats["errors"] += 1
                print(f"[PM SCHEDULER] Error processing plan {plan.id}: {e}")

        await self.db.commit()
        print(f"[PM SCHEDULER] Run complete: {stats}")
        return stats

    async def _process_plan(self, plan: PMPlan, now: datetime) -> WorkOrder:
        """Genera una OT para un plan preventivo y actualiza next_due_at."""
        wo = await self.wo_service.create(
            tenant_id=plan.tenant_id,
            center_id=plan.asset.center_id,
            wo_type=WorkOrderType.PREVENTIVE,
            title=f"[PM] {plan.name}",
            priority=plan.priority,
            created_by="system",  # sistema
            asset_id=plan.asset_id,
            assigned_to=plan.assigned_to,
            pm_plan_id=plan.id,
            metadata={"auto_generated": True, "pm_plan_name": plan.name},
        )

        # Actualizar next_due_at según frecuencia
        plan.last_executed_at = now
        plan.next_due_at = self._calc_next_due(plan, now)
        return wo

    def _calc_next_due(self, plan: PMPlan, from_date: datetime) -> datetime:
        """Calcula la próxima fecha de ejecución según la frecuencia del plan."""
        from .models import PMTriggerType
        freq = plan.frequency

        if plan.trigger_type == PMTriggerType.CALENDAR:
            unit = freq.get("unit", "month")
            every = freq.get("every", 1)
            if unit == "day":
                return from_date + timedelta(days=every)
            elif unit == "week":
                return from_date + timedelta(weeks=every)
            elif unit == "month":
                # Aproximación: 30 días por mes
                return from_date + timedelta(days=30 * every)
            elif unit == "year":
                return from_date + timedelta(days=365 * every)

        # Para usage-based: requiere lectura de sensores; por ahora retorna mismo + 1 mes
        return from_date + timedelta(days=30)


# ─────────────────────────────────────────────
# IoT ALERT SERVICE
# ─────────────────────────────────────────────

class IoTAlertService:
    """
    Procesa lecturas de sensores y evalúa reglas de alerta.
    Crea OTs automáticamente cuando un valor cruza un umbral.
    """

    def __init__(self, db: AsyncSession, wo_service: WorkOrderService):
        self.db = db
        self.wo_service = wo_service

    async def process_readings(
        self,
        readings: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """
        Procesa batch de lecturas IoT.
        Evalúa alert_rules de cada sensor y crea OTs si es necesario.
        """
        stats = {"readings": len(readings), "alerts": 0, "work_orders": 0}

        # Cargar sensores únicos en batch
        sensor_ids = list({r["sensor_id"] for r in readings})
        result = await self.db.execute(
            select(Sensor).where(
                Sensor.id.in_(sensor_ids),
                Sensor.active == True,
            )
        )
        sensors_by_id = {s.id: s for s in result.scalars().all()}

        for reading in readings:
            sensor = sensors_by_id.get(reading["sensor_id"])
            if not sensor:
                continue

            value = float(reading["value"])
            timestamp = reading.get("time", datetime.now(timezone.utc))

            # Actualizar last_value en el sensor
            sensor.last_value = value
            sensor.last_seen_at = timestamp

            # Evaluar reglas de alerta
            alert = self._evaluate_rules(sensor, value)
            if alert:
                stats["alerts"] += 1
                # ¿Ya existe OT abierta para este sensor?
                existing = await self._get_open_iot_wo(sensor.id)
                if not existing:
                    wo = await self._create_iot_work_order(sensor, value, alert, timestamp)
                    stats["work_orders"] += 1

        await self.db.flush()
        return stats

    def _evaluate_rules(
        self,
        sensor: Sensor,
        value: float,
    ) -> Optional[Dict[str, Any]]:
        """Evalúa las alert_rules del sensor contra el valor recibido."""
        rules = sensor.alert_rules
        if not rules:
            return None

        min_val = rules.get("min")
        max_val = rules.get("max")

        if min_val is not None and value < min_val:
            return {
                "type": "below_minimum",
                "threshold": min_val,
                "value": value,
                "action": rules.get("action", "create_work_order"),
                "priority": rules.get("priority", "high"),
            }

        if max_val is not None and value > max_val:
            return {
                "type": "above_maximum",
                "threshold": max_val,
                "value": value,
                "action": rules.get("action", "create_work_order"),
                "priority": rules.get("priority", "high"),
            }

        return None

    async def _get_open_iot_wo(self, sensor_id: str) -> Optional[WorkOrder]:
        """Verifica si ya existe una OT abierta relacionada con este sensor."""
        result = await self.db.execute(
            select(WorkOrder).where(
                WorkOrder.metadata["sensor_id"].astext == sensor_id,
                WorkOrder.status.not_in([WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED]),
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def _create_iot_work_order(
        self,
        sensor: Sensor,
        value: float,
        alert: Dict[str, Any],
        timestamp: datetime,
    ) -> WorkOrder:
        """Crea una OT automáticamente por alerta de sensor IoT."""
        priority_map = {
            "emergency": WorkOrderPriority.EMERGENCY,
            "high": WorkOrderPriority.HIGH,
            "medium": WorkOrderPriority.MEDIUM,
            "low": WorkOrderPriority.LOW,
        }
        priority = priority_map.get(alert.get("priority", "high"), WorkOrderPriority.HIGH)

        title = (
            f"[IoT] {sensor.name}: {alert['type']} "
            f"({value} {sensor.unit}, umbral: {alert['threshold']})"
        )

        return await self.wo_service.create(
            tenant_id=sensor.tenant_id,
            center_id=sensor.center_id,
            wo_type=WorkOrderType.PREDICTIVE,
            title=title,
            priority=priority,
            created_by="system_iot",
            asset_id=sensor.asset_id,
            metadata={
                "sensor_id": sensor.id,
                "sensor_name": sensor.name,
                "metric_type": sensor.metric_type,
                "alert_value": value,
                "alert_threshold": alert["threshold"],
                "alert_type": alert["type"],
                "detected_at": timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp),
                "auto_generated_by_iot": True,
            },
        )


# ─────────────────────────────────────────────
# ANALYTICS SERVICE
# ─────────────────────────────────────────────

class AnalyticsService:
    """Calcula KPIs operativos de FM en tiempo real."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_kpis(
        self,
        tenant_id: str,
        center_id: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Calcula MTTR, MTBF, cumplimiento SLA y costo por m².
        """
        to_date = to_date or datetime.now(timezone.utc)
        from_date = from_date or (to_date - timedelta(days=30))

        filters = [
            WorkOrder.tenant_id == tenant_id,
            WorkOrder.created_at >= from_date,
            WorkOrder.created_at <= to_date,
        ]
        if center_id:
            filters.append(WorkOrder.center_id == center_id)

        # Total OTs
        total_result = await self.db.execute(
            select(func.count()).select_from(WorkOrder).where(*filters)
        )
        total = total_result.scalar() or 0

        # OTs por estado
        status_result = await self.db.execute(
            select(WorkOrder.status, func.count())
            .where(*filters)
            .group_by(WorkOrder.status)
        )
        by_status = {row[0]: row[1] for row in status_result.all()}

        # OTs por tipo
        type_result = await self.db.execute(
            select(WorkOrder.type, func.count())
            .where(*filters)
            .group_by(WorkOrder.type)
        )
        by_type = {row[0]: row[1] for row in type_result.all()}

        # MTTR: promedio de (completed_at - started_at) en horas para OTs cerradas
        mttr_result = await self.db.execute(
            select(
                func.avg(
                    func.extract("epoch", WorkOrder.completed_at - WorkOrder.started_at) / 3600
                )
            )
            .where(
                *filters,
                WorkOrder.started_at.is_not(None),
                WorkOrder.completed_at.is_not(None),
            )
        )
        mttr_hours = round(float(mttr_result.scalar() or 0), 2)

        # SLA compliance: OTs cerradas sin breach / total cerradas
        closed_filters = filters + [WorkOrder.status == WorkOrderStatus.CLOSED]
        total_closed_result = await self.db.execute(
            select(func.count()).select_from(WorkOrder).where(*closed_filters)
        )
        total_closed = total_closed_result.scalar() or 0

        breached_result = await self.db.execute(
            select(func.count()).select_from(WorkOrder).where(
                *closed_filters,
                WorkOrder.sla_breached == True,
            )
        )
        total_breached = breached_result.scalar() or 0

        sla_compliance = round(
            ((total_closed - total_breached) / total_closed * 100) if total_closed > 0 else 100.0, 1
        )

        # Costo total
        cost_result = await self.db.execute(
            select(func.sum(WorkOrder.actual_cost)).where(*filters)
        )
        total_cost = float(cost_result.scalar() or 0)

        # Costo por m² (si hay info del centro)
        cost_per_m2 = None
        if center_id:
            center = await self.db.get(Center, center_id)
            if center and center.total_area_m2:
                cost_per_m2 = round(total_cost / center.total_area_m2, 3)

        return {
            "period": {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
            },
            "work_orders": {
                "total": total,
                "by_status": by_status,
                "by_type": by_type,
                "mttr_hours": mttr_hours,
            },
            "sla": {
                "compliance_pct": sla_compliance,
                "total_closed": total_closed,
                "total_breached": total_breached,
            },
            "cost": {
                "total_eur": round(total_cost, 2),
                "per_m2_eur": cost_per_m2,
            },
        }


# ─────────────────────────────────────────────
# WEBHOOK DELIVERY SERVICE
# ─────────────────────────────────────────────

class WebhookDeliveryService:
    """
    Entrega webhooks firmados con HMAC-SHA256.
    En producción se encola en Redis/BullMQ para reintentos exponenciales.
    """

    SIGNING_ALGORITHM = "sha256"
    MAX_RETRIES = 3
    RETRY_DELAYS = [5, 25, 125]  # segundos (backoff exponencial)

    def __init__(self, secret_key: str):
        self.secret_key = secret_key.encode()

    def sign_payload(self, payload: str) -> str:
        """Genera la firma HMAC-SHA256 del payload."""
        return hmac.new(
            self.secret_key,
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

    async def deliver(
        self,
        endpoint_url: str,
        event: str,
        data: dict,
        tenant_id: str,
    ) -> bool:
        """Envía el webhook con firma y reintentos."""
        payload = json.dumps({
            "event": event,
            "tenant_id": tenant_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }, default=str)

        signature = self.sign_payload(payload)
        headers = {
            "Content-Type": "application/json",
            "X-FM-Signature": f"sha256={signature}",
            "X-FM-Event": event,
            "User-Agent": "FM-Platform-Webhooks/1.0",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            for attempt, delay in enumerate(self.RETRY_DELAYS, 1):
                try:
                    response = await client.post(
                        endpoint_url,
                        content=payload,
                        headers=headers,
                    )
                    if response.status_code < 300:
                        return True
                    print(f"[WEBHOOK] Attempt {attempt} failed: {response.status_code}")
                except httpx.RequestError as e:
                    print(f"[WEBHOOK] Attempt {attempt} error: {e}")

                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(delay)

        print(f"[WEBHOOK] Failed all {self.MAX_RETRIES} attempts for {endpoint_url}")
        return False

    @staticmethod
    def verify_signature(payload: str, signature_header: str, secret: str) -> bool:
        """Verifica la firma de un webhook recibido (para clientes que reciben)."""
        expected = hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        received = signature_header.replace("sha256=", "")
        return hmac.compare_digest(expected, received)


# ─────────────────────────────────────────────
# SLA BREACH MONITOR
# ─────────────────────────────────────────────

class SLAMonitorService:
    """
    Job periódico que detecta OTs con SLA vencido y dispara alertas.
    Ejecutar cada 5 minutos vía APScheduler.
    """

    def __init__(self, db: AsyncSession, webhook_service: WebhookDeliveryService):
        self.db = db
        self.webhook = webhook_service

    async def check_breaches(self) -> int:
        """Detecta y marca OTs con SLA vencido."""
        now = datetime.now(timezone.utc)
        breached_count = 0

        # OTs abiertas con SLA vencido no marcadas aún
        result = await self.db.execute(
            select(WorkOrder).where(
                WorkOrder.sla_deadline <= now,
                WorkOrder.sla_breached == False,
                WorkOrder.status.not_in([WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED]),
            ).limit(200)
        )
        overdue = result.scalars().all()

        for wo in overdue:
            wo.sla_breached = True
            breached_count += 1
            # En producción: encolar webhook en Redis
            print(f"[SLA BREACH] OT {wo.code} - SLA vencido hace {(now - wo.sla_deadline).total_seconds()/60:.0f} min")

        if overdue:
            await self.db.commit()

        return breached_count
