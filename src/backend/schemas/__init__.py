"""
schemas/ — Pydantic schemas para validación request/response
=============================================================
Separados de los modelos ORM para desacoplar BD de la API.
Todos los schemas de respuesta excluyen datos sensibles.
"""

import uuid
from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field, field_validator
from enum import Enum


# ── COMMON ────────────────────────────────────────────

class PaginationMeta(BaseModel):
    cursor: Optional[str] = None
    has_more: bool = False
    count: int = 0
    total_count: Optional[int] = None


class ErrorDetail(BaseModel):
    field: Optional[str] = None
    message: str


class ProblemDetail(BaseModel):
    """RFC 7807 Problem Details."""
    type: str
    title: str
    status: int
    detail: str
    instance: Optional[str] = None
    request_id: Optional[str] = None
    errors: Optional[List[ErrorDetail]] = None


# ── ASSETS ────────────────────────────────────────────

class AssetLocation(BaseModel):
    floor: Optional[str] = None
    zone: Optional[str] = None
    room: Optional[str] = None


class AssetCreate(BaseModel):
    center_id: uuid.UUID
    parent_id: Optional[uuid.UUID] = None
    code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    category: str
    criticality: str = "medium"
    floor: Optional[str] = Field(None, max_length=20)
    zone: Optional[str] = Field(None, max_length=100)
    serial_number: Optional[str] = None
    purchase_date: Optional[datetime] = None
    warranty_until: Optional[datetime] = None
    expected_life_years: Optional[int] = Field(None, ge=1, le=100)
    purchase_cost: Optional[float] = Field(None, ge=0)
    specs: dict = Field(default_factory=dict)

    @field_validator("category")
    @classmethod
    def validate_category(cls, v):
        valid = [
            "hvac", "electrical", "plumbing", "vertical_transport",
            "fire_safety", "security", "telecom", "generation",
            "fitness_equipment", "pool", "sports_court", "locker_room",
            "wellness", "doors_shutters", "signage", "logistics",
            "access_control", "other",
        ]
        if v not in valid:
            raise ValueError(f"Categoría inválida. Válidas: {valid}")
        return v


class AssetUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    status: Optional[str] = None
    criticality: Optional[str] = None
    floor: Optional[str] = None
    zone: Optional[str] = None
    warranty_until: Optional[datetime] = None
    specs: Optional[dict] = None


class AssetResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    category: str
    status: str
    criticality: str
    floor: Optional[str]
    zone: Optional[str]
    specs: dict
    qr_code_url: Optional[str]
    warranty_until: Optional[datetime]
    open_work_orders: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AssetTreeNode(AssetResponse):
    depth: int = 0
    children: List["AssetTreeNode"] = []


class AssetListResponse(BaseModel):
    data: List[AssetResponse]
    pagination: Optional[PaginationMeta]


class BulkImportResponse(BaseModel):
    imported: int
    errors: List[dict]
    skipped: int
    total_rows: int


class BulkAssignPMRequest(BaseModel):
    plan_template_id: uuid.UUID
    center_id: Optional[uuid.UUID] = None
    category: Optional[str] = None
    floor: Optional[str] = None
    asset_ids: Optional[List[uuid.UUID]] = None


# ── WORK ORDERS ───────────────────────────────────────

class WorkOrderCreate(BaseModel):
    center_id: uuid.UUID
    type: str
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    priority: str = "medium"
    asset_id: Optional[uuid.UUID] = None
    space_id: Optional[uuid.UUID] = None
    assigned_to: Optional[uuid.UUID] = None
    contract_id: Optional[uuid.UUID] = None
    estimated_cost: Optional[float] = Field(None, ge=0)
    scheduled_for: Optional[datetime] = None
    checklist_template_id: Optional[uuid.UUID] = None
    metadata: dict = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        valid = ["corrective", "preventive", "predictive", "soft_service", "inspection"]
        if v not in valid:
            raise ValueError(f"Tipo inválido. Válidos: {valid}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v):
        valid = ["emergency", "high", "medium", "low"]
        if v not in valid:
            raise ValueError(f"Prioridad inválida. Válidas: {valid}")
        return v


class WorkOrderTransition(BaseModel):
    action: str = Field(..., description="submit|approve|reject|assign|start|complete|close|cancel...")
    comment: Optional[str] = None
    assigned_to: Optional[uuid.UUID] = None
    resolution: Optional[str] = None
    actual_cost: Optional[float] = None


class ChecklistItemUpdate(BaseModel):
    id: str
    completed: Optional[bool] = None
    value: Optional[float] = None
    note: Optional[str] = None
    completed_at: Optional[str] = None  # ISO 8601 timestamp del dispositivo


class ChecklistBatchUpdate(BaseModel):
    items: List[ChecklistItemUpdate]


class WorkOrderResponse(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    type: str
    status: str
    priority: str
    asset_id: Optional[uuid.UUID]
    assigned_to: Optional[uuid.UUID]
    sla_deadline: Optional[datetime]
    sla_overdue: bool = False
    sla_minutes_remaining: Optional[int]
    estimated_cost: Optional[float]
    actual_cost: Optional[float]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class WorkOrderListResponse(BaseModel):
    data: List[WorkOrderResponse]
    pagination: PaginationMeta


# ── SENSORS ───────────────────────────────────────────

class SensorReadingInput(BaseModel):
    sensor_id: str
    time: str = Field(..., description="ISO 8601 timestamp")
    value: float
    unit: str = Field(..., max_length=20)
    quality: str = "good"

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, v):
        if v not in ("good", "suspect", "bad"):
            raise ValueError("quality debe ser good|suspect|bad")
        return v


class SensorIngestRequest(BaseModel):
    readings: List[SensorReadingInput] = Field(..., max_length=500)


class SensorIngestResponse(BaseModel):
    accepted: int
    rejected: int
    alerts_triggered: int
    work_orders_created: int


# ── PM PLANS ──────────────────────────────────────────

class PMPlanCreate(BaseModel):
    asset_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    trigger_type: str
    priority: str = "medium"
    frequency: dict = Field(..., description='{"every":1,"unit":"month"} | {"hours":500}')
    checklist_template_id: Optional[uuid.UUID] = None
    estimated_duration_min: Optional[int] = Field(None, ge=5)
    estimated_cost: Optional[float] = None

    @field_validator("trigger_type")
    @classmethod
    def validate_trigger(cls, v):
        valid = ["calendar", "usage_hours", "usage_cycles", "condition"]
        if v not in valid:
            raise ValueError(f"trigger_type inválido. Válidos: {valid}")
        return v


class PMPlanResponse(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    name: str
    trigger_type: str
    priority: str
    frequency: dict
    next_due_at: Optional[datetime]
    last_executed_at: Optional[datetime]
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── ANALYTICS ─────────────────────────────────────────

class KPIResponse(BaseModel):
    period: dict
    work_orders: dict
    sla: dict
    cost: dict
    nps: Optional[float] = None


# ── WEBHOOKS ──────────────────────────────────────────

class WebhookCreate(BaseModel):
    url: str = Field(..., description="HTTPS endpoint para recibir eventos")
    events: List[str] = Field(..., min_length=1)
    secret: str = Field(..., min_length=16, description="Secret para firma HMAC-SHA256")


class WebhookResponse(BaseModel):
    id: uuid.UUID
    url: str
    events: List[str]
    active: bool
    last_delivery_at: Optional[datetime]
    last_delivery_status: Optional[int]
    failure_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── AUTH / USERS ──────────────────────────────────────

class UserCreate(BaseModel):
    email: str
    name: str
    user_type: str
    specializations: List[str] = Field(default_factory=list)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    user_type: str
    specializations: List[str]
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
