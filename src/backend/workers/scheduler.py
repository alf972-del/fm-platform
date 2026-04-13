"""
workers/scheduler.py — Scheduler de Mantenimiento Preventivo
=============================================================
Worker que se ejecuta periódicamente para:

1. Generar OTs preventivas cuando pm_plans.next_due_at ha vencido
2. Enviar alertas de vencimiento de contratos (30/60/90 días)
3. Verificar SLAs vencidos y disparar webhooks
4. Recalcular next_due_at después de generar cada OT
5. Comprimir datos IoT antiguos en TimescaleDB

Ejecución:
  python -m workers.scheduler  (modo daemon)
  
  O con el runner de RQ:
  rq worker fm-platform-scheduler --url redis://localhost:6379

Frecuencia:
  - check_pm_plans:         cada hora
  - check_contract_alerts:  cada 24 horas
  - check_sla_breaches:     cada 15 minutos
  - compress_old_sensor_data: cada 24 horas (TimescaleDB)
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import List
import logging

from database import RLSContext, AsyncSessionLocal
from models import (
    PMPlan, WorkOrder, WorkOrderStatus, WorkOrderType, Priority,
    Contract, Sensor, Tenant,
)
from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── PM PLANS ──────────────────────────────────────────

async def check_pm_plans_all_tenants():
    """
    Punto de entrada principal del scheduler.
    Itera todos los tenants activos y procesa sus planes vencidos.
    """
    logger.info("🔄 Iniciando check de planes preventivos...")
    
    async with AsyncSessionLocal() as db:
        # Obtener todos los tenants activos
        result = await db.execute(
            select(Tenant.id).where(Tenant.active == True)
        )
        tenant_ids = [row[0] for row in result.fetchall()]
    
    processed = 0
    for tenant_id in tenant_ids:
        try:
            count = await check_pm_plans_for_tenant(tenant_id)
            processed += count
        except Exception as e:
            logger.error(f"Error procesando PM plans para tenant {tenant_id}: {e}")
    
    logger.info(f"✅ PM Plans check completo: {processed} OTs generadas en {len(tenant_ids)} tenants")
    return processed


async def check_pm_plans_for_tenant(tenant_id: uuid.UUID) -> int:
    """
    Genera OTs preventivas para un tenant específico.
    Usa RLSContext para garantizar aislamiento de datos.
    """
    now = datetime.utcnow()
    ots_created = 0
    
    async with RLSContext(str(tenant_id)) as db:
        # Obtener planes vencidos — índice idx_pm_plans_due hace esto muy eficiente
        result = await db.execute(
            select(PMPlan)
            .where(
                and_(
                    PMPlan.tenant_id == tenant_id,
                    PMPlan.active == True,
                    PMPlan.next_due_at <= now,
                )
            )
            .limit(500)  # Procesar máximo 500 planes por ejecución
        )
        due_plans: List[PMPlan] = result.scalars().all()
        
        if not due_plans:
            return 0
        
        logger.info(f"Tenant {tenant_id}: {len(due_plans)} planes vencidos")
        
        for plan in due_plans:
            try:
                await _generate_wo_from_plan(db, plan, tenant_id)
                ots_created += 1
            except Exception as e:
                logger.error(f"Error generando OT para plan {plan.id}: {e}")
    
    return ots_created


async def _generate_wo_from_plan(db: AsyncSession, plan: PMPlan, tenant_id: uuid.UUID):
    """
    Genera una OT a partir de un plan preventivo y recalcula next_due_at.
    """
    # Obtener el activo para el código del centro
    asset = await db.get(type(plan.asset), plan.asset_id)
    if not asset or not asset.active:
        plan.active = False
        await db.flush()
        return
    
    # Generar código de OT
    code = await _generate_wo_code(db, tenant_id)
    
    # Calcular SLA según prioridad del plan
    sla_hours = {"emergency": 2, "high": 8, "medium": 48, "low": 120}
    sla_deadline = datetime.utcnow() + timedelta(hours=sla_hours.get(plan.priority, 48))
    
    # Crear OT preventiva
    wo = WorkOrder(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        center_id=asset.center_id,
        asset_id=plan.asset_id,
        pm_plan_id=plan.id,
        code=code,
        title=f"[PM] {plan.name} — {asset.name}",
        description=f"Mantenimiento preventivo generado automáticamente por el plan: {plan.name}",
        type=WorkOrderType.PREVENTIVE,
        status=WorkOrderStatus.PENDING,
        priority=Priority(plan.priority) if plan.priority else Priority.MEDIUM,
        sla_deadline=sla_deadline,
        scheduled_for=datetime.utcnow(),
        estimated_cost=plan.estimated_cost,
        metadata={"auto_generated": True, "pm_plan_id": str(plan.id)},
    )
    db.add(wo)
    
    # Clonar checklist si hay template
    if plan.checklist_template_id:
        await _clone_checklist_to_wo(db, plan.checklist_template_id, wo.id, tenant_id)
    
    # Recalcular next_due_at
    plan.last_executed_at = datetime.utcnow()
    plan.next_due_at = _calculate_next_due(plan)
    
    await db.flush()
    
    logger.debug(f"OT generada: {code} para activo {asset.code}")
    
    # Disparar webhook en background (sin bloquear el loop)
    asyncio.create_task(
        _dispatch_pm_webhook(tenant_id=tenant_id, wo_id=wo.id, code=code)
    )


def _calculate_next_due(plan: PMPlan) -> datetime:
    """
    Calcula la próxima fecha de vencimiento según el trigger_type y frequency.
    
    calendar:     {"every": 1, "unit": "month"}  → +1 mes
    calendar:     {"every": 3, "unit": "month"}  → +3 meses
    calendar:     {"every": 1, "unit": "week"}   → +1 semana
    usage_hours:  No se puede calcular aquí (requiere telemetría del activo)
    """
    now = datetime.utcnow()
    freq = plan.frequency or {}
    
    if plan.trigger_type == "calendar":
        every = freq.get("every", 1)
        unit = freq.get("unit", "month")
        
        if unit == "day":
            return now + timedelta(days=every)
        elif unit == "week":
            return now + timedelta(weeks=every)
        elif unit == "month":
            # Aproximación: 30.44 días por mes
            return now + timedelta(days=every * 30.44)
        elif unit == "year":
            return now + timedelta(days=every * 365.25)
    
    elif plan.trigger_type in ("usage_hours", "usage_cycles", "condition"):
        # Para estos tipos el scheduler no puede calcular la fecha automáticamente.
        # Se requiere telemetría del activo (sensor de horas de uso).
        # Por ahora desactivamos el plan hasta que el técnico lo reactive.
        plan.active = False
        logger.warning(f"Plan {plan.id} desactivado: trigger_type={plan.trigger_type} requiere telemetría")
        return now
    
    # Fallback: en 1 mes
    return now + timedelta(days=30)


async def _generate_wo_code(db: AsyncSession, tenant_id: uuid.UUID) -> str:
    """Genera código secuencial único por tenant: OT-2025-00341"""
    year = datetime.utcnow().year
    result = await db.execute(
        text("""
            SELECT COUNT(*) FROM work_orders
            WHERE tenant_id = :tenant_id
            AND EXTRACT(YEAR FROM created_at) = :year
        """),
        {"tenant_id": str(tenant_id), "year": year}
    )
    count = result.scalar() + 1
    return f"OT-{year}-{count:05d}"


async def _clone_checklist_to_wo(
    db: AsyncSession,
    template_id: uuid.UUID,
    wo_id: uuid.UUID,
    tenant_id: uuid.UUID
):
    """Clona los ítems de un template de checklist a la nueva OT."""
    from models import WOChecklistItem
    
    result = await db.execute(
        select(text("*")).select_from(
            text(f"checklist_template_items WHERE template_id = '{template_id}'")
        )
    )
    items = result.fetchall()
    
    for i, item in enumerate(items):
        db.add(WOChecklistItem(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            work_order_id=wo_id,
            order_index=i,
            title=item.title if hasattr(item, "title") else f"Ítem {i+1}",
            is_required=True,
        ))


async def _dispatch_pm_webhook(tenant_id: uuid.UUID, wo_id: uuid.UUID, code: str):
    """Dispara webhook pm_plan.work_order_generated (fire and forget)."""
    try:
        async with RLSContext(str(tenant_id)) as db:
            from services.webhooks import WebhookService
            await WebhookService(db).dispatch(
                tenant_id=tenant_id,
                event="pm_plan.work_order_generated",
                payload={"work_order_id": str(wo_id), "code": code},
            )
    except Exception as e:
        logger.error(f"Error dispatching PM webhook: {e}")


# ── CONTRACT ALERTS ───────────────────────────────────

async def check_contract_expiry_all_tenants():
    """
    Verifica contratos que vencen en los próximos 30/60/90 días.
    Envía alertas a los FM Managers correspondientes.
    """
    logger.info("🔄 Verificando vencimientos de contratos...")
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Tenant.id).where(Tenant.active == True))
        tenant_ids = [row[0] for row in result.fetchall()]
    
    for tenant_id in tenant_ids:
        try:
            await _check_contracts_for_tenant(tenant_id)
        except Exception as e:
            logger.error(f"Error verificando contratos para tenant {tenant_id}: {e}")


async def _check_contracts_for_tenant(tenant_id: uuid.UUID):
    """Verifica y alerta sobre contratos próximos a vencer."""
    thresholds = [90, 60, 30]  # días de anticipación
    now = datetime.utcnow()
    
    async with RLSContext(str(tenant_id)) as db:
        from services.webhooks import WebhookService
        from services.notifications import NotificationService
        
        for days in thresholds:
            target_date = now + timedelta(days=days)
            window_start = target_date - timedelta(days=1)
            window_end = target_date + timedelta(days=1)
            
            result = await db.execute(
                select(Contract)
                .where(
                    and_(
                        Contract.tenant_id == tenant_id,
                        Contract.active == True,
                        Contract.end_date >= window_start,
                        Contract.end_date <= window_end,
                    )
                )
            )
            contracts = result.scalars().all()
            
            for contract in contracts:
                logger.info(f"Contrato {contract.id} vence en {days} días")
                
                await WebhookService(db).dispatch(
                    tenant_id=tenant_id,
                    event="contract.expiring_soon",
                    payload={
                        "contract_id": str(contract.id),
                        "name": contract.name,
                        "end_date": contract.end_date.isoformat(),
                        "days_remaining": days,
                    }
                )


# ── SLA BREACHES ──────────────────────────────────────

async def check_sla_breaches_all_tenants():
    """
    Detecta OTs con SLA vencido que no han sido notificadas.
    Se ejecuta cada 15 minutos.
    """
    logger.info("⏱️ Verificando SLAs vencidos...")
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Tenant.id).where(Tenant.active == True))
        tenant_ids = [row[0] for row in result.fetchall()]
    
    total_breaches = 0
    for tenant_id in tenant_ids:
        try:
            count = await _check_sla_for_tenant(tenant_id)
            total_breaches += count
        except Exception as e:
            logger.error(f"Error verificando SLA para tenant {tenant_id}: {e}")
    
    if total_breaches:
        logger.warning(f"⚠️ {total_breaches} SLAs vencidos detectados")


async def _check_sla_for_tenant(tenant_id: uuid.UUID) -> int:
    """Detecta y notifica SLAs vencidos para un tenant."""
    now = datetime.utcnow()
    breaches = 0
    
    async with RLSContext(str(tenant_id)) as db:
        from services.webhooks import WebhookService
        from services.notifications import NotificationService
        
        # OTs con SLA vencido y que no están cerradas
        # El metadata["sla_breach_notified"] evita notificar dos veces
        result = await db.execute(
            select(WorkOrder)
            .where(
                and_(
                    WorkOrder.tenant_id == tenant_id,
                    WorkOrder.sla_deadline < now,
                    WorkOrder.status.not_in([
                        WorkOrderStatus.CLOSED,
                        WorkOrderStatus.CANCELLED,
                        WorkOrderStatus.VERIFIED,
                    ]),
                    # Excluir las ya notificadas usando JSONB
                    text("(metadata->>'sla_breach_notified') IS NULL"),
                )
            )
        )
        overdue_wos = result.scalars().all()
        
        for wo in overdue_wos:
            logger.warning(f"SLA breach: {wo.code} — vencida hace {(now - wo.sla_deadline.replace(tzinfo=None)).seconds // 60} min")
            
            # Marcar como notificada
            wo.metadata = {**wo.metadata, "sla_breach_notified": now.isoformat()}
            
            # Webhook
            await WebhookService(db).dispatch(
                tenant_id=tenant_id,
                event="work_order.sla_breached",
                payload={
                    "work_order_id": str(wo.id),
                    "code": wo.code,
                    "priority": wo.priority,
                    "minutes_overdue": int((now - wo.sla_deadline.replace(tzinfo=None)).total_seconds() / 60),
                }
            )
            
            breaches += 1
        
        await db.flush()
    
    return breaches


# ── TIMESCALEDB COMPRESSION ───────────────────────────

async def compress_old_sensor_data():
    """
    Fuerza la compresión de chunks de sensor_readings antiguos (>30 días).
    TimescaleDB normalmente lo hace automáticamente, pero esta tarea
    lo ejecuta de forma controlada en horas de baja carga.
    """
    logger.info("🗜️ Comprimiendo datos históricos de sensores...")
    
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                text("""
                    SELECT compress_chunk(i) 
                    FROM show_chunks(
                        'sensor_readings',
                        older_than => INTERVAL '30 days'
                    ) i
                    WHERE NOT is_compressed
                """)
            )
            compressed = result.rowcount
            await db.commit()
            logger.info(f"✅ {compressed} chunks comprimidos")
        except Exception as e:
            logger.warning(f"TimescaleDB compression: {e} (normal si no está instalado)")


# ── RUNNER ────────────────────────────────────────────

async def run_scheduler():
    """
    Loop principal del scheduler.
    En producción esto se ejecuta como proceso separado.
    En desarrollo usa asyncio.sleep para simular cron.
    """
    import asyncio
    
    last_daily = None
    last_sla_check = None
    
    logger.info("🚀 Scheduler FM Platform iniciado")
    
    while True:
        now = datetime.utcnow()
        
        # Cada hora: planes preventivos
        await check_pm_plans_all_tenants()
        
        # Cada 15 minutos: SLA breaches
        if not last_sla_check or (now - last_sla_check).seconds >= 900:
            await check_sla_breaches_all_tenants()
            last_sla_check = now
        
        # Cada 24 horas: contratos + compresión IoT
        if not last_daily or (now - last_daily).days >= 1:
            await check_contract_expiry_all_tenants()
            await compress_old_sensor_data()
            last_daily = now
        
        # Esperar 1 hora antes del próximo ciclo
        logger.info("💤 Scheduler durmiendo 3600s...")
        await asyncio.sleep(3600)


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    asyncio.run(run_scheduler())
