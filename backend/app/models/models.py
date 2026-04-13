"""
app/models/ — Modelos SQLAlchemy para todas las entidades FM
Cada modelo incluye tenant_id para multi-tenant con RLS.
"""

import uuid
from datetime import datetime
from typing import Optional, Any
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, String, Boolean, DateTime, Numeric, Integer,
    ForeignKey, Text, Enum, JSON, func, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.core.database import Base


# ── MIXINS ────────────────────────────────────────────────────────────────────

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )


class TenantMixin:
    """Mixin que añade tenant_id a todas las tablas con RLS."""
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )


# ── ENUMS ─────────────────────────────────────────────────────────────────────

class TenantPlan(str, PyEnum):
    STARTER = "starter"
    GROWTH = "growth"
    SCALE = "scale"
    ENTERPRISE = "enterprise"


class CenterType(str, PyEnum):
    OFFICE = "office"
    MALL = "mall"
    SPORT = "sport"
    MIXED = "mixed"


class AssetCategory(str, PyEnum):
    HVAC = "hvac"
    ELECTRICAL = "electrical"
    PLUMBING = "plumbing"
    VERTICAL_TRANSPORT = "vertical_transport"
    FITNESS_EQUIPMENT = "fitness_equipment"
    POOL = "pool"
    FIRE_SAFETY = "fire_safety"
    SECURITY = "security"
    TELECOM = "telecom"
    GENERATION = "generation"
    DOORS_SHUTTERS = "doors_shutters"
    LOCKER_ROOM = "locker_room"
    SPORTS_COURT = "sports_court"
    WELLNESS = "wellness"
    ACCESS_CONTROL = "access_control"
    SIGNAGE = "signage"
    LOGISTICS = "logistics"
    OTHER = "other"


class AssetStatus(str, PyEnum):
    OPERATIONAL = "operational"
    MAINTENANCE = "maintenance"
    OUT_OF_SERVICE = "out_of_service"
    RETIRED = "retired"


class AssetCriticality(str, PyEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class WorkOrderType(str, PyEnum):
    CORRECTIVE = "corrective"
    PREVENTIVE = "preventive"
    PREDICTIVE = "predictive"
    SOFT_SERVICE = "soft_service"
    INSPECTION = "inspection"


class WorkOrderStatus(str, PyEnum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    VERIFIED = "verified"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class WorkOrderPriority(str, PyEnum):
    EMERGENCY = "emergency"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class UserType(str, PyEnum):
    STAFF = "staff"
    TECHNICIAN = "technician"
    VENDOR = "vendor"
    TENANT_CONTACT = "tenant_contact"


class SensorMetric(str, PyEnum):
    TEMPERATURE = "temperature"
    ENERGY_KWH = "energy_kwh"
    WATER_PH = "water_ph"
    CHLORINE = "chlorine"
    OCCUPANCY = "occupancy"
    VIBRATION = "vibration"
    CO2 = "co2"
    PRESSURE = "pressure"
    FLOW = "flow"


# ── TENANT ────────────────────────────────────────────────────────────────────

class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    plan: Mapped[TenantPlan] = mapped_column(
        Enum(TenantPlan), nullable=False, default=TenantPlan.STARTER
    )
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    centers = relationship("Center", back_populates="tenant")
    users = relationship("User", back_populates="tenant")


# ── CENTER ────────────────────────────────────────────────────────────────────

class Center(Base, TenantMixin, TimestampMixin):
    __tablename__ = "centers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[CenterType] = mapped_column(Enum(CenterType), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(3), default="ESP")
    total_area_m2: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    floors_count: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="centers")
    assets = relationship("Asset", back_populates="center")
    work_orders = relationship("WorkOrder", back_populates="center")
    spaces = relationship("Space", back_populates="center")

    __table_args__ = (
        Index("idx_centers_tenant", "tenant_id"),
    )


# ── ASSET ─────────────────────────────────────────────────────────────────────

class Asset(Base, TenantMixin, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    center_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False
    )
    # Self-referential para árbol jerárquico: Centro → Planta → Zona → Activo
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[AssetCategory] = mapped_column(Enum(AssetCategory), nullable=False)
    status: Mapped[AssetStatus] = mapped_column(
        Enum(AssetStatus), nullable=False, default=AssetStatus.OPERATIONAL
    )
    criticality: Mapped[AssetCriticality] = mapped_column(
        Enum(AssetCriticality), nullable=False, default=AssetCriticality.MEDIUM
    )
    floor: Mapped[Optional[str]] = mapped_column(String(20))   # "P3", "PB", "S1"
    zone: Mapped[Optional[str]] = mapped_column(String(100))
    room: Mapped[Optional[str]] = mapped_column(String(100))
    purchase_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    warranty_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    expected_life_years: Mapped[Optional[int]] = mapped_column(Integer)
    # JSONB flexible: specs técnicas según categoría
    # HVAC: {cooling_kw, refrigerant, brand, model}
    # Pool: {volume_m3, target_ph, target_chlorine, lanes}
    # Fitness: {brand, model, usage_hours, belt_condition}
    specs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    qr_code_url: Mapped[Optional[str]] = mapped_column(String(500))
    nfc_tag_id: Mapped[Optional[str]] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    tenant = relationship("Tenant")
    center = relationship("Center", back_populates="assets")
    parent = relationship("Asset", remote_side=[id], back_populates="children")
    children = relationship("Asset", back_populates="parent")
    work_orders = relationship("WorkOrder", back_populates="asset")
    sensors = relationship("Sensor", back_populates="asset")
    pm_plans = relationship("PMPlan", back_populates="asset")

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_asset_code"),
        Index("idx_assets_center_cat", "tenant_id", "center_id", "category", "status"),
        Index("idx_assets_parent", "parent_id", "center_id"),
        Index("idx_assets_qr", "qr_code_url"),
    )


# ── WORK ORDER ────────────────────────────────────────────────────────────────

class WorkOrder(Base, TenantMixin, TimestampMixin):
    __tablename__ = "work_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    center_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)  # OT-25-0341
    type: Mapped[WorkOrderType] = mapped_column(Enum(WorkOrderType), nullable=False)
    status: Mapped[WorkOrderStatus] = mapped_column(
        Enum(WorkOrderStatus), nullable=False, default=WorkOrderStatus.PENDING
    )
    priority: Mapped[WorkOrderPriority] = mapped_column(
        Enum(WorkOrderPriority), nullable=False, default=WorkOrderPriority.MEDIUM
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    resolution: Mapped[Optional[str]] = mapped_column(Text)

    # Relaciones opcionales
    asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True
    )
    space_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("spaces.id"), nullable=True
    )
    assigned_to: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    contract_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=True
    )
    pm_plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pm_plans.id"), nullable=True
    )

    # SLA y tiempos
    sla_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sla_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Costos
    estimated_cost: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    actual_cost: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))

    # Campos custom por tipo de centro
    metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Relationships
    center = relationship("Center", back_populates="work_orders")
    asset = relationship("Asset", back_populates="work_orders")
    technician = relationship("User", foreign_keys=[assigned_to])
    checklist_items = relationship("ChecklistItem", back_populates="work_order",
                                   cascade="all, delete-orphan")
    attachments = relationship("WorkOrderAttachment", back_populates="work_order",
                                cascade="all, delete-orphan")
    time_logs = relationship("WorkOrderTimeLog", back_populates="work_order",
                              cascade="all, delete-orphan")
    comments = relationship("WorkOrderComment", back_populates="work_order",
                             cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_wo_code"),
        # Índice crítico para dashboard
        Index(
            "idx_wo_dashboard",
            "tenant_id", "center_id", "status", "sla_deadline",
            postgresql_where=~status.in_(["closed", "cancelled"])
        ),
        Index("idx_wo_asset_history", "asset_id", "created_at"),
        Index("idx_wo_assigned", "assigned_to", "status"),
    )


# ── PM PLAN (Mantenimiento Preventivo) ────────────────────────────────────────

class PMPlan(Base, TenantMixin, TimestampMixin):
    __tablename__ = "pm_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # {"every": 1, "unit": "month"} o {"hours": 500} o {"condition": "sensor_threshold"}
    frequency: Mapped[dict] = mapped_column(JSONB, nullable=False)
    checklist_template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("checklist_templates.id"), nullable=True
    )
    priority: Mapped[WorkOrderPriority] = mapped_column(
        Enum(WorkOrderPriority), default=WorkOrderPriority.MEDIUM
    )
    next_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    asset = relationship("Asset", back_populates="pm_plans")
    work_orders = relationship("WorkOrder", back_populates="pm_plan")

    __table_args__ = (
        Index("idx_pm_plans_due", "tenant_id", "next_due_at",
              postgresql_where=(active == True)),
    )


# ── SENSOR + READINGS ─────────────────────────────────────────────────────────

class Sensor(Base, TenantMixin, TimestampMixin):
    __tablename__ = "sensors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True
    )
    center_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    device_id: Mapped[str] = mapped_column(String(200), nullable=False)
    metric_type: Mapped[SensorMetric] = mapped_column(Enum(SensorMetric), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)  # "°C", "kWh", "pH"
    # {"min": 6.8, "max": 7.8, "action": "create_work_order", "priority": "emergency"}
    alert_rules: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_reading_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_value: Mapped[Optional[float]] = mapped_column(Numeric(12, 4))

    asset = relationship("Asset", back_populates="sensors")

    __table_args__ = (
        UniqueConstraint("tenant_id", "device_id", name="uq_sensor_device"),
    )


# NOTA: SensorReading se almacena en TimescaleDB hypertable — ver migrations/timescale.sql
# La tabla sensor_readings NO usa SQLAlchemy ORM para máximo rendimiento de escritura.


# ── USER ──────────────────────────────────────────────────────────────────────

class User(Base, TenantMixin, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )  # = keycloak_user_id
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    user_type: Mapped[UserType] = mapped_column(Enum(UserType), nullable=False)
    specializations: Mapped[list] = mapped_column(JSONB, default=list)
    push_token: Mapped[Optional[str]] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    tenant = relationship("Tenant", back_populates="users")

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_user_email"),
        Index("idx_users_tenant_type", "tenant_id", "user_type"),
    )


# ── CONTRACT ──────────────────────────────────────────────────────────────────

class Contract(Base, TenantMixin, TimestampMixin):
    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    service_type: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    amount: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    # {"emergency": {"response_hours": 2, "resolution_hours": 8, "penalty_pct": 5}, ...}
    sla_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # {"pct_discount_per_breach": 1, "max_penalty_pct": 15}
    penalty_rules: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(50), default="active")
    document_url: Mapped[Optional[str]] = mapped_column(String(500))

    __table_args__ = (
        Index("idx_contracts_expiry", "tenant_id", "end_date",
              postgresql_where=(status == "active")),
    )


# ── SPACE ─────────────────────────────────────────────────────────────────────

class Space(Base, TenantMixin, TimestampMixin):
    __tablename__ = "spaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    center_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    type: Mapped[str] = mapped_column(String(100), nullable=False)  # meeting_room, court, etc.
    floor: Mapped[Optional[str]] = mapped_column(String(20))
    capacity: Mapped[Optional[int]] = mapped_column(Integer)
    area_m2: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    amenities: Mapped[list] = mapped_column(JSONB, default=list)
    bookable: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    center = relationship("Center", back_populates="spaces")


# ── CHECKLIST ─────────────────────────────────────────────────────────────────

class ChecklistTemplate(Base, TenantMixin, TimestampMixin):
    __tablename__ = "checklist_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100))
    items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class ChecklistItem(Base, TimestampMixin):
    __tablename__ = "checklist_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    value: Mapped[Optional[float]] = mapped_column(Numeric(12, 4))
    note: Mapped[Optional[str]] = mapped_column(Text)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    work_order = relationship("WorkOrder", back_populates="checklist_items")


# ── WORK ORDER ATTACHMENT ─────────────────────────────────────────────────────

class WorkOrderAttachment(Base, TimestampMixin):
    __tablename__ = "wo_attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    attachment_type: Mapped[str] = mapped_column(String(50), default="evidence_photo")

    work_order = relationship("WorkOrder", back_populates="attachments")


# ── WORK ORDER TIME LOG ───────────────────────────────────────────────────────

class WorkOrderTimeLog(Base, TimestampMixin):
    __tablename__ = "wo_time_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    minutes: Mapped[Optional[int]] = mapped_column(Integer)
    note: Mapped[Optional[str]] = mapped_column(Text)

    work_order = relationship("WorkOrder", back_populates="time_logs")


# ── WORK ORDER COMMENT ────────────────────────────────────────────────────────

class WorkOrderComment(Base, TimestampMixin):
    __tablename__ = "wo_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)

    work_order = relationship("WorkOrder", back_populates="comments")


# ── VENDOR ────────────────────────────────────────────────────────────────────

class Vendor(Base, TenantMixin, TimestampMixin):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    tax_id: Mapped[Optional[str]] = mapped_column(String(50))
    email: Mapped[Optional[str]] = mapped_column(String(320))
    phone: Mapped[Optional[str]] = mapped_column(String(30))
    specialties: Mapped[list] = mapped_column(JSONB, default=list)
    portal_access: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
