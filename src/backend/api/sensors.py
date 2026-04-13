"""
api/sensors.py — Router de IoT / Sensores
==========================================
  GET    /sensors                   → lista de sensores del tenant
  POST   /sensors                   → registrar nuevo sensor
  GET    /sensors/{id}              → detalle + last reading
  GET    /sensors/{id}/readings     → serie temporal (TimescaleDB)
  POST   /sensors/ingest            → ingestión batch de lecturas (10K rpm)
  POST   /sensors/{id}/test-alert   → probar reglas de alerta
"""

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from typing import Optional, List
from datetime import datetime, timedelta
import uuid

from database import get_db
from models import Sensor, SensorReading, MetricType
from schemas.sensors import (
    SensorCreate, SensorResponse, SensorListResponse,
    SensorReadingResponse, ReadingsQuery,
    BatchIngestRequest, BatchIngestResponse,
    AlertRule,
)
from services.sensors import SensorService
from services.work_orders import WorkOrderService
from services.notifications import NotificationService
from services.webhooks import WebhookService
from middleware.auth import get_current_user

router = APIRouter()


@router.get("", response_model=SensorListResponse)
async def list_sensors(
    center_id: Optional[uuid.UUID] = Query(None),
    asset_id: Optional[uuid.UUID] = Query(None),
    metric_type: Optional[MetricType] = Query(None),
    in_alert: Optional[bool] = Query(None, description="Solo sensores en estado de alerta"),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista sensores con su último valor y estado de alerta."""
    service = SensorService(db, current_user.tenant_id)
    return await service.list(
        center_id=center_id,
        asset_id=asset_id,
        metric_type=metric_type,
        in_alert=in_alert,
    )


@router.post("", response_model=SensorResponse, status_code=status.HTTP_201_CREATED)
async def create_sensor(
    data: SensorCreate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Registra un nuevo sensor IoT.
    Las alert_rules definen cuándo generar OTs automáticamente.
    
    Ejemplo de alert_rules:
    {
        "min": 6.8,
        "max": 7.8,
        "action": "create_work_order",
        "priority": "high",
        "title_template": "pH fuera de rango: {value} {unit}"
    }
    """
    service = SensorService(db, current_user.tenant_id)
    return await service.create(data)


@router.get("/{sensor_id}", response_model=SensorResponse)
async def get_sensor(
    sensor_id: uuid.UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Detalle del sensor con última lectura y estado de alertas."""
    service = SensorService(db, current_user.tenant_id)
    sensor = await service.get_by_id(sensor_id)
    if not sensor:
        raise HTTPException(status_code=404, detail="Sensor not found")
    return sensor


@router.get("/{sensor_id}/readings", response_model=List[SensorReadingResponse])
async def get_sensor_readings(
    sensor_id: uuid.UUID,
    from_dt: datetime = Query(..., alias="from"),
    to_dt: datetime = Query(..., alias="to"),
    bucket: Optional[str] = Query(None, description="Agregación: 1m|5m|1h|1d"),
    aggregate: Optional[str] = Query(None, description="avg|min|max|sum (requiere bucket)"),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Serie temporal de lecturas de un sensor.
    
    Con bucket+aggregate usa TimescaleDB time_bucket() para agregación eficiente.
    Sin bucket devuelve lecturas raw (max 10.000 puntos).
    
    Ejemplos:
    - Últimas 24h cada 5 minutos: bucket=5m&aggregate=avg
    - Último mes diario: bucket=1d&aggregate=avg
    - Raw últimas 2 horas: (sin bucket)
    
    Rangos máximos:
    - Raw: 48 horas
    - Bucket 1m: 7 días
    - Bucket 1h: 90 días
    - Bucket 1d: sin límite
    """
    # Validar rango máximo
    max_days = {None: 2, "1m": 7, "5m": 30, "1h": 90, "1d": 365}
    days_diff = (to_dt - from_dt).days
    
    if bucket not in max_days:
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"field": "bucket", "message": "Valid values: 1m, 5m, 1h, 1d"}]}
        )
    
    if days_diff > max_days.get(bucket, 2):
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"field": "from/to", "message": f"Max range for bucket={bucket}: {max_days.get(bucket)} days"}]}
        )
    
    service = SensorService(db, current_user.tenant_id)
    
    if bucket and aggregate:
        # TimescaleDB time_bucket aggregation
        readings = await service.get_aggregated_readings(
            sensor_id=sensor_id,
            from_dt=from_dt,
            to_dt=to_dt,
            bucket=bucket,
            aggregate=aggregate,
        )
    else:
        # Raw readings
        readings = await service.get_raw_readings(
            sensor_id=sensor_id,
            from_dt=from_dt,
            to_dt=to_dt,
            limit=10000,
        )
    
    return readings


@router.post("/ingest", response_model=BatchIngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_readings(
    data: BatchIngestRequest,
    background_tasks: BackgroundTasks,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ingestión batch de lecturas IoT.
    
    - Acepta hasta 500 lecturas por request
    - Responde 202 Accepted inmediatamente (procesamiento async)
    - Evalúa alert_rules de cada sensor en background
    - Si el valor cruza un umbral → crea OT automáticamente
    
    Rate limit especial: 10.000 req/min (vs 500 del resto de la API)
    
    El procesamiento en background incluye:
    1. INSERT batch en sensor_readings (TimescaleDB)
    2. Evaluar alert_rules para cada sensor
    3. Si alerta → crear WorkOrder
    4. Si alerta → enviar push notification a FM Managers
    5. Si alerta → disparar webhook sensor.alert_triggered
    6. Actualizar sensor.last_value y sensor.in_alert
    """
    if len(data.readings) > 500:
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"field": "readings", "message": "Max 500 readings per request"}]}
        )
    
    service = SensorService(db, current_user.tenant_id)
    
    # Inserción rápida en BD (sin evaluar alertas)
    accepted, rejected = await service.batch_insert_readings(data.readings)
    
    # Evaluación de alertas en background (no bloquea la respuesta)
    background_tasks.add_task(
        _process_sensor_alerts,
        tenant_id=current_user.tenant_id,
        readings=data.readings,
    )
    
    return BatchIngestResponse(
        accepted=accepted,
        rejected=rejected,
        alerts_pending=True,  # Se procesan en background
    )


async def _process_sensor_alerts(tenant_id: uuid.UUID, readings: list):
    """
    Tarea de background: evalúa alert_rules y crea OTs si necesario.
    Se ejecuta después de responder al cliente para no bloquear.
    """
    from database import RLSContext
    
    alerts_triggered = 0
    work_orders_created = 0
    
    async with RLSContext(str(tenant_id)) as db:
        sensor_service = SensorService(db, tenant_id)
        wo_service = WorkOrderService(db, tenant_id)
        notif_service = NotificationService()
        webhook_service = WebhookService(db)
        
        # Agrupar lecturas por sensor
        by_sensor: dict = {}
        for r in readings:
            by_sensor.setdefault(str(r.sensor_id), []).append(r)
        
        for sensor_id_str, sensor_readings in by_sensor.items():
            sensor = await sensor_service.get_by_id(uuid.UUID(sensor_id_str))
            if not sensor or not sensor.alert_rules:
                continue
            
            # Evaluar la lectura más reciente
            latest = max(sensor_readings, key=lambda r: r.time)
            value = latest.value
            rules = sensor.alert_rules
            
            in_alert = False
            if "min" in rules and value < rules["min"]:
                in_alert = True
            if "max" in rules and value > rules["max"]:
                in_alert = True
            
            # Actualizar estado del sensor
            await sensor_service.update_alert_state(sensor.id, in_alert, value)
            
            if in_alert and not sensor.in_alert:  # Nueva alerta (no estaba en alerta)
                alerts_triggered += 1
                
                # Crear OT automática
                if rules.get("action") == "create_work_order" and sensor.asset_id:
                    title_template = rules.get(
                        "title_template",
                        "Alerta sensor {metric_type}: {value} {unit}"
                    )
                    title = title_template.format(
                        metric_type=sensor.metric_type,
                        value=round(value, 2),
                        unit=sensor.unit,
                    )
                    
                    from schemas.work_orders import WorkOrderCreate
                    from models import WorkOrderType
                    
                    wo = await wo_service.create(
                        data=WorkOrderCreate(
                            center_id=sensor.asset.center_id,
                            asset_id=sensor.asset_id,
                            type=WorkOrderType.PREDICTIVE,
                            priority=rules.get("priority", "high"),
                            title=title,
                            description=f"Alerta IoT automática. Sensor: {sensor.name}. Valor: {value} {sensor.unit}",
                            metadata={"sensor_id": str(sensor.id), "trigger_value": value},
                        ),
                        created_by=None,  # Sistema
                        sla_deadline=None,
                    )
                    work_orders_created += 1
                    
                    # Notificar a FM Managers del centro
                    await notif_service.notify_fm_managers(
                        tenant_id=tenant_id,
                        center_id=sensor.asset.center_id,
                        title=f"🚨 Alerta IoT: {sensor.name}",
                        body=title,
                    )
                    
                    # Webhook
                    await webhook_service.dispatch(
                        tenant_id=tenant_id,
                        event="sensor.alert_triggered",
                        payload={
                            "sensor_id": str(sensor.id),
                            "sensor_name": sensor.name,
                            "value": value,
                            "unit": sensor.unit,
                            "work_order_id": str(wo.id) if wo else None,
                        }
                    )
            
            elif not in_alert and sensor.in_alert:  # Alerta resuelta
                await webhook_service.dispatch(
                    tenant_id=tenant_id,
                    event="sensor.alert_resolved",
                    payload={"sensor_id": str(sensor.id), "value": value},
                )
    
    print(f"Sensor alerts processed: {alerts_triggered} triggered, {work_orders_created} work orders created")
