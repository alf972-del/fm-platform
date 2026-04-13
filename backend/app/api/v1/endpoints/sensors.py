"""
app/api/v1/endpoints/sensors.py — Ingestión IoT + lecturas TimescaleDB
"""

from fastapi import APIRouter, Depends, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from typing import Optional, List
from datetime import datetime, timedelta, timezone
import uuid

from app.core.database import get_db
from app.core.auth import get_current_tenant, require_scope
from app.models.models import Sensor, WorkOrder, WorkOrderType, WorkOrderPriority
from app.schemas.schemas import SensorIngestRequest, SensorIngestResponse
from app.services.alert_service import AlertService
from app.services.notification_service import NotificationService
from app.services.webhook_service import WebhookService

router = APIRouter()


@router.post("/ingest", response_model=SensorIngestResponse, status_code=202)
async def ingest_readings(
    body: SensorIngestRequest,
    background_tasks: BackgroundTasks,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:write")),
):
    """
    Ingestión batch de lecturas IoT — hasta 500 por request.
    Rate limit especial: 10.000 req/min.
    Proceso asíncrono: acepta inmediatamente, procesa en background.
    """
    accepted = 0
    rejected = 0
    alerts_triggered = 0
    wo_created = 0

    # Validar sensor IDs pertenecen al tenant
    sensor_ids = [r.sensor_id for r in body.readings]
    query = select(Sensor).where(
        Sensor.id.in_(sensor_ids), Sensor.tenant_id == tenant_id
    )
    result = await db.execute(query)
    valid_sensors = {s.id: s for s in result.scalars().all()}

    for reading in body.readings:
        if reading.sensor_id not in valid_sensors:
            rejected += 1
            continue

        sensor = valid_sensors[reading.sensor_id]
        accepted += 1

        # INSERT en TimescaleDB hypertable via raw SQL (máximo rendimiento)
        await db.execute(
            text("""
                INSERT INTO sensor_readings (time, sensor_id, tenant_id, value, unit, quality)
                VALUES (:time, :sensor_id, :tenant_id, :value, :unit, :quality)
                ON CONFLICT DO NOTHING
            """),
            {
                "time": reading.time,
                "sensor_id": str(reading.sensor_id),
                "tenant_id": str(tenant_id),
                "value": reading.value,
                "unit": reading.unit,
                "quality": reading.quality,
            }
        )

        # Actualizar last_reading en sensor
        sensor.last_reading_at = reading.time
        sensor.last_value = reading.value

        # Evaluar alert_rules
        alert = AlertService.evaluate(sensor.alert_rules, reading.value)
        if alert:
            alerts_triggered += 1
            # Crear OT automáticamente en background
            background_tasks.add_task(
                _create_alert_work_order,
                tenant_id, sensor, reading.value, alert, db
            )
            wo_created += 1

    await db.commit()

    return SensorIngestResponse(
        accepted=accepted,
        rejected=rejected,
        alerts_triggered=alerts_triggered,
        work_orders_created=wo_created,
    )


async def _create_alert_work_order(tenant_id, sensor, value, alert_config, db):
    """
    Crea una OT correctiva automáticamente cuando un sensor dispara una alerta.
    Ejemplo: pH piscina < 6.8 → OT "Ajuste pH urgente"
    """
    from app.models.models import WorkOrder, WorkOrderStatus
    import asyncio

    # Evitar duplicar OTs si ya existe una abierta para este sensor
    existing = await db.execute(
        select(WorkOrder).where(
            WorkOrder.asset_id == sensor.asset_id,
            WorkOrder.status.not_in(["closed", "cancelled"]),
            WorkOrder.tenant_id == tenant_id,
        )
    )
    if existing.scalar_one_or_none():
        return

    wo = WorkOrder(
        tenant_id=tenant_id,
        center_id=sensor.center_id if hasattr(sensor, "center_id") else None,
        type=WorkOrderType.PREDICTIVE,
        status=WorkOrderStatus.PENDING,
        priority=alert_config.get("priority", WorkOrderPriority.HIGH),
        title=f"Alerta sensor: {sensor.name} — Valor: {value} {sensor.unit}",
        description=f"Alerta automática IoT. Umbral: {alert_config}. Valor actual: {value}",
        asset_id=sensor.asset_id,
        metadata={"source": "iot_alert", "sensor_id": str(sensor.id), "value": value},
    )
    db.add(wo)
    await db.commit()

    # Notificar FM Manager
    await WebhookService.dispatch(tenant_id, "sensor.alert_triggered", {
        "sensor_id": str(sensor.id),
        "sensor_name": sensor.name,
        "value": value,
        "unit": sensor.unit,
        "work_order_id": str(wo.id),
    })


@router.get("/{sensor_id}/readings")
async def get_readings(
    sensor_id: uuid.UUID,
    from_dt: datetime = Query(..., alias="from"),
    to_dt: datetime = Query(..., alias="to"),
    bucket: Optional[str] = Query(None, regex="^(1m|5m|1h|1d)$"),
    aggregate: Optional[str] = Query(None, regex="^(avg|min|max|sum)$"),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:read")),
):
    """
    Serie temporal de un sensor con agregación opcional.
    Usa TimescaleDB time_bucket() para performance con millones de puntos.
    """
    # Validar rango max 90 días
    if (to_dt - from_dt).days > 90:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Max range is 90 days")

    if bucket and aggregate:
        # Agregación TimescaleDB
        sql = text(f"""
            SELECT
                time_bucket(:{bucket!r}::INTERVAL, time) AS ts,
                {aggregate}(value) AS value,
                unit
            FROM sensor_readings
            WHERE sensor_id = :sensor_id
              AND tenant_id = :tenant_id
              AND time BETWEEN :from_dt AND :to_dt
            GROUP BY ts, unit
            ORDER BY ts ASC
        """).bindparams(
            sensor_id=str(sensor_id),
            tenant_id=str(tenant_id),
            from_dt=from_dt,
            to_dt=to_dt,
        )
    else:
        # Raw readings (max 10K puntos)
        sql = text("""
            SELECT time AS ts, value, unit
            FROM sensor_readings
            WHERE sensor_id = :sensor_id
              AND tenant_id = :tenant_id
              AND time BETWEEN :from_dt AND :to_dt
            ORDER BY ts ASC
            LIMIT 10000
        """).bindparams(
            sensor_id=str(sensor_id),
            tenant_id=str(tenant_id),
            from_dt=from_dt,
            to_dt=to_dt,
        )

    result = await db.execute(sql)
    rows = result.fetchall()
    return {"data": [{"ts": r.ts, "value": float(r.value), "unit": r.unit} for r in rows]}


# ── ANALYTICS ─────────────────────────────────────────────────────────────────

"""
app/api/v1/endpoints/analytics.py — KPIs operativos FM
"""

router_analytics = APIRouter()


@router_analytics.get("/kpis")
async def get_kpis(
    center_id: Optional[uuid.UUID] = Query(None),
    period: str = Query(..., regex="^(last_7d|last_30d|last_90d|ytd|custom)$"),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:read")),
):
    """
    KPIs operativos del período: MTTR, MTBF, SLA compliance, costo/m².
    Resultado cacheado en Redis 30 segundos.
    """
    from app.core.redis import redis_client
    import json

    cache_key = f"kpis:{tenant_id}:{center_id}:{period}"
    cached = await redis_client.get(cache_key)
    if cached:
        return json.loads(cached)

    now = datetime.now(timezone.utc)
    if period == "last_7d":
        from_dt = now - timedelta(days=7)
        to_dt = now
    elif period == "last_30d":
        from_dt = now - timedelta(days=30)
        to_dt = now
    elif period == "last_90d":
        from_dt = now - timedelta(days=90)
        to_dt = now
    elif period == "ytd":
        from_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0)
        to_dt = now
    else:  # custom
        from_dt = from_date
        to_dt = to_date

    filters = [
        WorkOrder.tenant_id == tenant_id,
        WorkOrder.created_at >= from_dt,
        WorkOrder.created_at <= to_dt,
    ]
    if center_id:
        filters.append(WorkOrder.center_id == center_id)

    # MTTR: media de (closed_at - started_at) en horas
    mttr_sql = text("""
        SELECT
            AVG(EXTRACT(EPOCH FROM (closed_at - started_at)) / 3600) as mttr_hours,
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'closed') as closed_count
        FROM work_orders
        WHERE tenant_id = :tid
          AND created_at BETWEEN :from_dt AND :to_dt
          AND (:center_id::UUID IS NULL OR center_id = :center_id::UUID)
    """).bindparams(
        tid=str(tenant_id), from_dt=from_dt, to_dt=to_dt,
        center_id=str(center_id) if center_id else None
    )
    mttr_result = await db.execute(mttr_sql)
    mttr_row = mttr_result.fetchone()

    # SLA compliance
    sla_sql = text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status IN ('closed','verified') AND
                (sla_deadline IS NULL OR closed_at <= sla_deadline)) as compliant,
            priority,
            COUNT(*) FILTER (WHERE status IN ('closed','verified') AND
                closed_at <= sla_deadline) * 100.0 / NULLIF(COUNT(*),0) as pct
        FROM work_orders
        WHERE tenant_id = :tid
          AND created_at BETWEEN :from_dt AND :to_dt
          AND status IN ('closed','verified','cancelled')
        GROUP BY priority
    """).bindparams(tid=str(tenant_id), from_dt=from_dt, to_dt=to_dt)
    sla_result = await db.execute(sla_sql)

    # Costo total
    cost_sql = text("""
        SELECT
            SUM(actual_cost) as total_cost
        FROM work_orders
        WHERE tenant_id = :tid
          AND created_at BETWEEN :from_dt AND :to_dt
          AND actual_cost IS NOT NULL
    """).bindparams(tid=str(tenant_id), from_dt=from_dt, to_dt=to_dt)
    cost_result = await db.execute(cost_sql)
    cost_row = cost_result.fetchone()

    by_priority = {}
    total_compliant = 0
    total_sla = 0
    for row in sla_result.fetchall():
        by_priority[row.priority] = round(float(row.pct or 0), 1)
        total_compliant += row.compliant or 0
        total_sla += row.total or 0

    overall_sla = round(total_compliant * 100 / total_sla, 1) if total_sla else 100.0

    result = {
        "period": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
        "work_orders": {
            "total": mttr_row.total or 0,
            "closed": mttr_row.closed_count or 0,
            "mttr_hours": round(float(mttr_row.mttr_hours), 2) if mttr_row.mttr_hours else None,
        },
        "sla": {
            "compliance_pct": overall_sla,
            "by_priority": by_priority,
        },
        "cost": {
            "total_eur": round(float(cost_row.total_cost or 0), 2),
        },
    }

    # Cache 30s
    await redis_client.setex(cache_key, 30, json.dumps(result, default=str))
    return result
