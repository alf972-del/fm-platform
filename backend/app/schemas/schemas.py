"""
app/schemas/ — Schemas Pydantic para validación y serialización.
Separados en Request (entrada) y Response (salida).
"""

from pydantic import BaseModel, Field, validator, UUID4
from typing import Optional, Any, List
from datetime import datetime
from enum import Enum

from app.models.models import (
    WorkOrderType, WorkOrderStatus, WorkOrderPriority,
    AssetCategory, AssetStatus, AssetCriticality,
    CenterType, UserType, SensorMetric
)


# ── PAGINATION ────────────────────────────────────────────────────────────────

class PaginationMeta(BaseModel):
    cursor: Optional[str] = None
    has_more: bool = False
    total_count: Optional[int] = None


class PaginatedResponse(BaseModel):
    data: List[Any]
    pagination: PaginationMeta


# ── WORK ORDER SCHEMAS ────────────────────────────────────────────────────────

class WorkOrderCreate(BaseModel):
    center_id: UUID4
    type: WorkOrderType
    title: str = Field(..., min_length=3, max_length=500)
    priority: WorkOrderPriority = WorkOrderPriority.MEDIUM
    description: Optional[str] = None
    asset_id: Optional[UUID4] = None
    space_id: Optional[UUID4] = None
    assigned_to: Optional[UUID4] = None
    contract_id: Optional[UUID4] = None
    estimated_cost: Optional[float] = Field(None, ge=0)
    scheduled_for: Optional[datetime] = None
    checklist_template_id: Optional[UUID4] = None
    metadata: dict = {}

    class Config:
        use_enum_values = True


class WorkOrderTransition(BaseModel):
    action: str = Field(..., description="Ver state machine en documentación")
    comment: Optional[str] = None
    assigned_to: Optional[UUID4] = None
    resolution: Optional[str] = None
    actual_cost: Optional[float] = Field(None, ge=0)

    @validator("action")
    def validate_action(cls, v):
        valid = {"submit", "approve", "reject", "assign", "reassign",
                 "start", "pause", "resume", "complete", "escalate",
                 "verify", "reopen", "close", "cancel"}
        if v not in valid:
            raise ValueError(f"Invalid action '{v}'. Valid: {valid}")
        return v


class WorkOrderResponse(BaseModel):
    id: UUID4
    code: str
    type: WorkOrderType
    status: WorkOrderStatus
    priority: WorkOrderPriority
    title: str
    description: Optional[str]
    center_id: UUID4
    asset_id: Optional[UUID4]
    assigned_to: Optional[UUID4]
    sla_deadline: Optional[datetime]
    sla_overdue: bool
    sla_minutes_remaining: Optional[int]
    estimated_cost: Optional[float]
    actual_cost: Optional[float]
    started_at: Optional[datetime]
    closed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChecklistItemUpdate(BaseModel):
    id: str
    completed: bool
    value: Optional[float] = None
    note: Optional[str] = None
    completed_at: Optional[datetime] = None


class ChecklistBatchUpdate(BaseModel):
    items: List[ChecklistItemUpdate]


class PresignRequest(BaseModel):
    filename: str
    content_type: str
    size_bytes: int = Field(..., gt=0, le=52_428_800)  # max 50MB


class PresignResponse(BaseModel):
    upload_url: str
    attachment_id: UUID4


class AttachmentConfirm(BaseModel):
    attachment_id: UUID4
    type: str = "evidence_photo"


# ── ASSET SCHEMAS ─────────────────────────────────────────────────────────────

class AssetCreate(BaseModel):
    center_id: UUID4
    parent_id: Optional[UUID4] = None
    name: str = Field(..., min_length=2, max_length=300)
    category: AssetCategory
    criticality: AssetCriticality = AssetCriticality.MEDIUM
    floor: Optional[str] = None
    zone: Optional[str] = None
    room: Optional[str] = None
    purchase_date: Optional[datetime] = None
    warranty_until: Optional[datetime] = None
    expected_life_years: Optional[int] = Field(None, gt=0, lt=100)
    specs: dict = {}

    class Config:
        use_enum_values = True


class AssetResponse(BaseModel):
    id: UUID4
    code: str
    name: str
    category: AssetCategory
    status: AssetStatus
    criticality: AssetCriticality
    center_id: UUID4
    parent_id: Optional[UUID4]
    floor: Optional[str]
    zone: Optional[str]
    specs: dict
    warranty_until: Optional[datetime]
    qr_code_url: Optional[str]
    open_work_orders: int = 0
    last_maintenance_at: Optional[datetime]
    children: List["AssetResponse"] = []
    created_at: datetime

    class Config:
        from_attributes = True


AssetResponse.model_rebuild()


class AssetImportRow(BaseModel):
    """Para importación masiva CSV — 600 activos en un archivo."""
    name: str
    category: str
    floor: Optional[str] = None
    zone: Optional[str] = None
    room: Optional[str] = None
    criticality: str = "medium"
    brand: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    purchase_date: Optional[str] = None
    warranty_until: Optional[str] = None


# ── SENSOR SCHEMAS ────────────────────────────────────────────────────────────

class SensorReading(BaseModel):
    sensor_id: UUID4
    time: datetime
    value: float
    unit: str
    quality: str = "good"


class SensorIngestRequest(BaseModel):
    readings: List[SensorReading] = Field(..., max_items=500)


class SensorIngestResponse(BaseModel):
    accepted: int
    rejected: int
    alerts_triggered: int
    work_orders_created: int


# ── ANALYTICS SCHEMAS ─────────────────────────────────────────────────────────

class KPIRequest(BaseModel):
    center_id: Optional[UUID4] = None
    period: str = Field(..., pattern="^(last_7d|last_30d|last_90d|ytd|custom)$")
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None


class WorkOrderKPIs(BaseModel):
    total: int
    by_status: dict
    by_type: dict
    mttr_hours: Optional[float]
    mtbf_hours: Optional[float]


class SLAKPIs(BaseModel):
    compliance_pct: float
    by_priority: dict


class CostKPIs(BaseModel):
    total_eur: float
    per_m2_eur: Optional[float]
    vs_budget_pct: Optional[float]


class KPIResponse(BaseModel):
    period: dict
    work_orders: WorkOrderKPIs
    sla: SLAKPIs
    cost: CostKPIs
    nps: Optional[float] = None


# ── PM PLAN SCHEMAS ───────────────────────────────────────────────────────────

class PMPlanCreate(BaseModel):
    asset_id: UUID4
    name: str
    trigger_type: str = Field(..., pattern="^(calendar|usage_hours|usage_cycles|condition)$")
    frequency: dict  # {"every": 1, "unit": "month"} o {"hours": 500}
    checklist_template_id: Optional[UUID4] = None
    priority: WorkOrderPriority = WorkOrderPriority.MEDIUM

    class Config:
        use_enum_values = True


class PMPlanBulkAssign(BaseModel):
    """Asignación masiva: todos los HVAC de un centro."""
    asset_ids: List[UUID4]
    plan: PMPlanCreate


# ── WEBHOOK SCHEMAS ───────────────────────────────────────────────────────────

class WebhookEvent(BaseModel):
    event: str
    data: dict
    tenant_id: UUID4
    timestamp: datetime
    request_id: str


class WebhookConfigCreate(BaseModel):
    url: str
    events: List[str]
    secret: str
    active: bool = True
