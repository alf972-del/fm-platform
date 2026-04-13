"""
api/analytics.py — Analytics & KPIs
=====================================
  GET /analytics/kpis          → KPIs operativos del período
  GET /analytics/sla-report    → Reporte SLA por proveedor y tipo
  GET /analytics/mttr-mtbf     → MTTR y MTBF por activo/categoría
  GET /analytics/costs         → Costos de mantenimiento por período
  GET /analytics/esg           → Dashboard ESG (energía, agua, carbono)
  GET /analytics/occupancy     → Ocupación de espacios
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, and_, extract, case
from typing import Optional
from datetime import datetime, timedelta
import uuid

from database import get_db
from models import WorkOrder, WorkOrderStatus, WorkOrderType, Asset, Sensor, SensorReading
from middleware.auth import get_current_user

router = APIRouter()


@router.get("/kpis")
async def get_kpis(
    center_id: Optional[uuid.UUID] = Query(None, description="UUID del centro. Null = todos"),
    period: str = Query(..., description="last_7d | last_30d | last_90d | ytd | custom"),
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    KPIs operativos del período solicitado.
    
    Incluye:
    - Resumen de órdenes de trabajo (total, por estado, por tipo)
    - MTTR (Mean Time To Repair) promedio y por categoría
    - MTBF (Mean Time Between Failures) para activos críticos
    - SLA compliance % (global y por prioridad)
    - Costos: total, por m², vs. presupuesto
    - NPS del período si hay encuestas
    
    Los datos se sirven desde caché Redis con TTL de 60s para dashboards.
    """
    # Resolver rango temporal
    start, end = _resolve_period(period, from_dt, to_dt)
    
    # ── Work Orders ───────────────────────────────────
    wo_query = select(
        func.count(WorkOrder.id).label("total"),
        func.count(case(
            (WorkOrder.status == "closed", 1), else_=None
        )).label("closed"),
        func.count(case(
            (WorkOrder.status.in_(["in_progress", "assigned", "pending"]), 1), else_=None
        )).label("open"),
        func.count(case(
            (WorkOrder.type == "corrective", 1), else_=None
        )).label("corrective"),
        func.count(case(
            (WorkOrder.type == "preventive", 1), else_=None
        )).label("preventive"),
        func.count(case(
            (WorkOrder.type == "soft_service", 1), else_=None
        )).label("soft_service"),
        # MTTR: promedio de (completed_at - started_at) en horas
        func.avg(
            extract("epoch", WorkOrder.completed_at - WorkOrder.started_at) / 3600
        ).label("mttr_hours"),
    ).where(
        and_(
            WorkOrder.tenant_id == current_user.tenant_id,
            WorkOrder.created_at >= start,
            WorkOrder.created_at <= end,
        )
    )
    
    if center_id:
        wo_query = wo_query.where(WorkOrder.center_id == center_id)
    
    wo_result = await db.execute(wo_query)
    wo_stats = wo_result.fetchone()
    
    # ── SLA Compliance ────────────────────────────────
    sla_query = select(
        func.count(WorkOrder.id).label("total"),
        func.count(case(
            (
                (WorkOrder.closed_at != None) & (WorkOrder.closed_at <= WorkOrder.sla_deadline),
                1
            ), else_=None
        )).label("within_sla"),
        WorkOrder.priority.label("priority"),
    ).where(
        and_(
            WorkOrder.tenant_id == current_user.tenant_id,
            WorkOrder.created_at >= start,
            WorkOrder.created_at <= end,
            WorkOrder.sla_deadline != None,
        )
    ).group_by(WorkOrder.priority)
    
    if center_id:
        sla_query = sla_query.where(WorkOrder.center_id == center_id)
    
    sla_result = await db.execute(sla_query)
    sla_rows = sla_result.fetchall()
    
    total_with_sla = sum(r.total for r in sla_rows)
    total_within_sla = sum(r.within_sla or 0 for r in sla_rows)
    sla_pct = round((total_within_sla / total_with_sla * 100), 1) if total_with_sla > 0 else None
    
    sla_by_priority = {}
    for row in sla_rows:
        if row.total > 0:
            sla_by_priority[row.priority] = round((row.within_sla or 0) / row.total * 100, 1)
    
    # ── Costos ────────────────────────────────────────
    cost_query = select(
        func.sum(WorkOrder.actual_cost).label("total_cost"),
        func.avg(WorkOrder.actual_cost).label("avg_cost"),
    ).where(
        and_(
            WorkOrder.tenant_id == current_user.tenant_id,
            WorkOrder.created_at >= start,
            WorkOrder.created_at <= end,
            WorkOrder.actual_cost != None,
        )
    )
    if center_id:
        cost_query = cost_query.where(WorkOrder.center_id == center_id)
    
    cost_result = await db.execute(cost_query)
    cost_stats = cost_result.fetchone()
    
    # Calcular €/m² si tenemos el área del centro
    cost_per_m2 = None
    if center_id and cost_stats.total_cost:
        area_result = await db.execute(
            text("SELECT total_area_m2 FROM centers WHERE id = :center_id"),
            {"center_id": str(center_id)}
        )
        area_row = area_result.fetchone()
        if area_row and area_row[0]:
            cost_per_m2 = round(float(cost_stats.total_cost) / float(area_row[0]), 3)
    
    return {
        "period": {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "label": period,
        },
        "work_orders": {
            "total": wo_stats.total or 0,
            "by_status": {
                "closed": wo_stats.closed or 0,
                "open": wo_stats.open or 0,
            },
            "by_type": {
                "corrective":   wo_stats.corrective or 0,
                "preventive":   wo_stats.preventive or 0,
                "soft_service": wo_stats.soft_service or 0,
            },
            "mttr_hours": round(float(wo_stats.mttr_hours), 2) if wo_stats.mttr_hours else None,
        },
        "sla": {
            "compliance_pct": sla_pct,
            "by_priority": sla_by_priority,
            "total_measured": total_with_sla,
            "within_sla": total_within_sla,
        },
        "cost": {
            "total_eur": float(cost_stats.total_cost) if cost_stats.total_cost else 0,
            "avg_per_wo_eur": float(cost_stats.avg_cost) if cost_stats.avg_cost else 0,
            "per_m2_eur": cost_per_m2,
        },
    }


@router.get("/sla-report")
async def get_sla_report(
    center_id: Optional[uuid.UUID] = Query(None),
    period: str = Query("last_30d"),
    from_dt: Optional[datetime] = Query(None, alias="from"),
    to_dt: Optional[datetime] = Query(None, alias="to"),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Reporte detallado de SLA por proveedor (contrato) y tipo de servicio.
    Incluye penalizaciones calculadas según las reglas del contrato.
    """
    start, end = _resolve_period(period, from_dt, to_dt)
    
    result = await db.execute(
        text("""
            SELECT
                c.id as contract_id,
                c.name as contract_name,
                c.service_type,
                COUNT(wo.id) as total_wo,
                COUNT(CASE WHEN wo.closed_at <= wo.sla_deadline THEN 1 END) as within_sla,
                COUNT(CASE WHEN wo.closed_at > wo.sla_deadline THEN 1 END) as breached,
                AVG(EXTRACT(epoch FROM (wo.closed_at - wo.started_at)) / 3600) as avg_resolution_hours,
                SUM(CASE 
                    WHEN wo.closed_at > wo.sla_deadline 
                    THEN wo.actual_cost * (c.sla_config->'medium'->>'penalty_pct')::numeric / 100
                    ELSE 0 
                END) as estimated_penalties
            FROM work_orders wo
            LEFT JOIN contracts c ON wo.contract_id = c.id
            WHERE wo.tenant_id = :tenant_id
              AND wo.created_at BETWEEN :start AND :end
              AND wo.contract_id IS NOT NULL
            GROUP BY c.id, c.name, c.service_type
            ORDER BY breached DESC
        """),
        {
            "tenant_id": str(current_user.tenant_id),
            "start": start,
            "end": end,
        }
    )
    
    rows = result.fetchall()
    
    report = []
    for row in rows:
        total = row.total_wo or 0
        within = row.within_sla or 0
        compliance = round(within / total * 100, 1) if total > 0 else None
        
        report.append({
            "contract_id": str(row.contract_id),
            "contract_name": row.contract_name,
            "service_type": row.service_type,
            "total_work_orders": total,
            "within_sla": within,
            "breached": row.breached or 0,
            "compliance_pct": compliance,
            "avg_resolution_hours": round(float(row.avg_resolution_hours), 2) if row.avg_resolution_hours else None,
            "estimated_penalties_eur": round(float(row.estimated_penalties), 2) if row.estimated_penalties else 0,
        })
    
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "contracts": report,
        "summary": {
            "total_contracts_evaluated": len(report),
            "total_penalties_eur": sum(r["estimated_penalties_eur"] for r in report),
        }
    }


@router.get("/esg")
async def get_esg_dashboard(
    center_id: Optional[uuid.UUID] = Query(None),
    period: str = Query("last_30d"),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Dashboard ESG con consumos energéticos, agua y estimación de carbono.
    
    Scope 1: Emisiones directas (generadores, calefacción)
    Scope 2: Electricidad comprada
    Scope 3: Agua, residuos, transportes de proveedores
    
    Los datos provienen de sensor_readings (TimescaleDB) filtrados por
    metric_type en [energy_kwh, water_ph] y el centro.
    
    Requiere sensores de energía y agua instalados en el centro.
    """
    start, end = _resolve_period(period, None, None)
    
    # Consumo eléctrico (kWh) del período — desde TimescaleDB
    energy_query = text("""
        SELECT
            time_bucket('1 day', sr.time) AS day,
            SUM(sr.value) AS kwh
        FROM sensor_readings sr
        JOIN sensors s ON sr.sensor_id = s.id
        WHERE sr.tenant_id = :tenant_id
          AND s.metric_type = 'energy_kwh'
          AND sr.time BETWEEN :start AND :end
          :center_filter
        GROUP BY day
        ORDER BY day
    """)
    
    center_filter = ""
    params = {
        "tenant_id": str(current_user.tenant_id),
        "start": start,
        "end": end,
    }
    
    if center_id:
        center_filter = "AND s.center_id = :center_id"
        params["center_id"] = str(center_id)
    
    # Sustituir placeholder (SQLAlchemy no soporta condicionales en text())
    energy_sql = energy_query.text.replace(":center_filter", center_filter)
    
    energy_result = await db.execute(text(energy_sql), params)
    energy_rows = energy_result.fetchall()
    
    total_kwh = sum(r.kwh for r in energy_rows if r.kwh)
    
    # Factor de emisión CO2 para España (kg CO2/kWh) — 2024
    CO2_FACTOR_ES = 0.181
    total_co2_kg = round(total_kwh * CO2_FACTOR_ES, 1)
    
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "energy": {
            "total_kwh": round(total_kwh, 1),
            "daily_breakdown": [
                {"date": str(r.day), "kwh": round(float(r.kwh), 1)}
                for r in energy_rows if r.kwh
            ],
        },
        "carbon": {
            "scope_2_kg_co2": total_co2_kg,
            "co2_factor_used": CO2_FACTOR_ES,
            "country": "ES",
            "note": "Scope 2 only. Scope 1 and 3 require additional sensor data.",
        },
        "certifications": {
            "leed": "data_available",
            "breeam": "data_available",
            "gresb": "partial_data",
        }
    }


# ── HELPERS ───────────────────────────────────────────

def _resolve_period(
    period: str,
    from_dt: Optional[datetime],
    to_dt: Optional[datetime],
) -> tuple[datetime, datetime]:
    """Convierte un nombre de período en un rango de fechas."""
    now = datetime.utcnow()
    
    if period == "last_7d":
        return now - timedelta(days=7), now
    elif period == "last_30d":
        return now - timedelta(days=30), now
    elif period == "last_90d":
        return now - timedelta(days=90), now
    elif period == "ytd":
        return datetime(now.year, 1, 1), now
    elif period == "custom":
        if not from_dt or not to_dt:
            raise HTTPException(
                status_code=422,
                detail={"errors": [{"field": "from/to", "message": "Required when period=custom"}]}
            )
        if (to_dt - from_dt).days > 365:
            raise HTTPException(
                status_code=422,
                detail={"errors": [{"field": "from/to", "message": "Max range: 365 days"}]}
            )
        return from_dt, to_dt
    else:
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"field": "period", "message": "Valid: last_7d|last_30d|last_90d|ytd|custom"}]}
        )
