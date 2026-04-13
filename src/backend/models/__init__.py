"""
models/__init__.py — Modelos SQLAlchemy ORM completos
======================================================
Entidades core del sistema FM Platform:
  - Tenant          → Organización cliente (raíz multi-tenant)
  - Center          → Instalación gestionada
  - Asset           → Activo físico (árbol jerárquico)
  - WorkOrder       → Orden de trabajo (entidad central)
  - PMPlan          → Plan de mantenimiento preventivo
  - Sensor          → Dispositivo IoT
  - SensorReading   → Lectura de sensor (TimescaleDB hypertable)
  - User            → Usuario del sistema
  - Contract        → Contrato con proveedor
  - Space           → Espacio reservable
  - ServiceRoute    → Ruta de Soft FM (limpieza, seguridad)
  - Invoice         → Factura de gastos comunes
  - Webhook         → Configuración de webhook saliente
"""

import uuid
from datetime import datetime
from typing import Optional, List, Any
from sqlalchemy import (
    String, Boolean, Integer, Numeric, DateTime, Date, Text, JSON,
    ForeignKey, Enum as SQLEnum, Index, UniqueConstraint, CheckConstraint,
    func, text
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from database import Base


# ── ENUMS ─────────────────────────────────────────────

class CenterType(str, enum.Enum):
    OFFICE  = "office"
    MALL    = "mall"
    SPORT   = "sport"
    MIXED   = "mixed"
    HOTEL   = "hotel"
    HOSPITAL = "hospital"

class AssetCategory(str, enum.Enum):
    HVAC              = "hvac"
    ELECTRICAL        = "electrical"
    PLUMBING          = "plumbing"
    VERTICAL_TRANSPORT = "vertical_transport"   # ascensores, escaleras
    FIRE_SAFETY       = "fire_safety"
    SECURITY          = "security"
    TELECOM           = "telecom"
    GENERATION        = "generation"            # grupos electrógenos, UPS
    FITNESS_EQUIPMENT = "fitness_equipment"
    POOL              = "pool"
    SPORTS_COURT      = "sports_court"
    LOCKER_ROOM       = "locker_room"
    WELLNESS          = "wellness"
    DOORS_SHUTTERS    = "doors_shutters"
    SIGNAGE           = "signage"
    LOGISTICS         = "logistics"
    ACCESS_CONTROL    = "access_control"
    OTHER             = "other"

class AssetStatus(str, enum.Enum):
    OPERATIONAL    = "operational"
    MAINTENANCE    = "maintenance"
    OUT_OF_SERVICE = "out_of_service"
    RETIRED        = "retired"

class Criticality(str, enum.Enum):
    CRITICAL = "critical"
    HIGH     = "high"
    MEDIUM   = "medium"
    LOW      = "low"

class WorkOrderType(str, enum.Enum):
    CORRECTIVE   = "corrective"
    PREVENTIVE   = "preventive"
    PREDICTIVE   = "predictive"
    SOFT_SERVICE = "soft_service"
    INSPECTION   = "inspection"

class WorkOrderStatus(str, enum.Enum):
    DRAFT       = "draft"
    PENDING     = "pending"
    APPROVED    = "approved"
    ASSIGNED    = "assigned"
    IN_PROGRESS = "in_progress"
    PAUSED      = "paused"
    COMPLETED   = "completed"
    VERIFIED    = "verified"
    CLOSED      = "closed"
    CANCELLED   = "cancelled"

class Priority(str, enum.Enum):
    EMERGENCY = "emergency"
    HIGH      = "high"
    MEDIUM    = "medium"
    LOW       = "low"

class PMTriggerType(str, enum.Enum):
    CALENDAR  = "calendar"
    USAGE_HOURS  = "usage_hours"
    USAGE_CYCLES = "usage_cycles"
    CONDITION = "condition"

class MetricType(str, enum.Enum):
    TEMPERATURE  = "temperature"
    ENERGY_KWH   = "energy_kwh"
    WATER_PH     = "water_ph"
    CHLORINE     = "chlorine"
    OCCUPANCY    = "occupancy"
    VIBRATION    = "vibration"
    HUMIDITY     = "humidity"
    CO2_PPM      = "co2_ppm"
    PRESSURE_BAR = "pressure_bar"

class UserType(str, enum.Enum):
    STAFF           = "staff"
    TECHNICIAN      = "technician"
    VENDOR          = "vendor"
    TENANT_CONTACT  = "tenant_contact"
    ADMIN           = "admin"

class ContractServiceType(str, enum.Enum):
    MAINTENANCE  = "maintenance"
    CLEANING     = "cleaning"
    SECURITY     = "security"
    LANDSCAPING  = "landscaping"
    ELEVATOR     = "elevator"
    PEST_CONTROL = "pest_control"
    WASTE        = "waste"
    ENERGY       = "energy"
    OTHER        = "other"

class SoftFMServiceType(str, enum.Enum):
    CLEANING    = "cleaning"
    SECURITY    = "security"
    LANDSCAPING = "landscaping"
    RECEPTION   = "reception"
    WASTE       = "waste"

class WebhookEvent(str, enum.Enum):
    WO_CREATED        = "work_order.created"
    WO_ASSIGNED       = "work_order.assigned"
    WO_STATUS_CHANGED = "work_order.status_changed"
    WO_SLA_BREACHED   = "work_order.sla_breached"
    WO_CLOSED         = "work_order.closed"
    ASSET_STATUS_CHANGED = "asset.status_changed"
    SENSOR_ALERT_TRIGGERED = "sensor.alert_triggered"
    SENSOR_ALERT_RESOLVED  = "sensor.alert_resolved"
    PM_WO_GENERATED   = "pm_plan.work_order_generated"
    CONTRACT_EXPIRING = "contract.expiring_soon"
    CHECKLIST_COMPLETED = "checklist.completed"
    USER_CREATED      = "user.created"


# ── MIXINS ────────────────────────────────────────────

class TimestampMixin:
    """Añade created_at y updated_at a cualquier modelo."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )

class TenantMixin:
    """Añade tenant_id para multi-tenancy con RLS."""
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )


# ── MODELOS ───────────────────────────────────────────

class Tenant(Base, TimestampMixin):
    """Organización cliente. Raíz del árbol multi-tenant."""
    __tablename__ = "tenants"
    
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str]    = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str]    = mapped_column(String(255), nullable=False)
    plan: Mapped[str]    = mapped_column(String(50), default="starter", nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    active: Mapped[bool]   = mapped_column(Boolean, default=True, nullable=False)
    
    # Relationships
    centers:   Mapped[List["Center"]]   = relationship(back_populates="tenant")
    users:     Mapped[List["User"]]     = relationship(back_populates="tenant")
    contracts: Mapped[List["Contract"]] = relationship(back_populates="tenant")
    webhooks:  Mapped[List["Webhook"]]  = relationship(back_populates="tenant")


class Center(Base, TimestampMixin, TenantMixin):
    """Instalación gestionada — oficinas, comercial, deportivo."""
    __tablename__ = "centers"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str]     = mapped_column(String(255), nullable=False)
    type: Mapped[CenterType] = mapped_column(SQLEnum(CenterType), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]]    = mapped_column(String(100))
    country: Mapped[str]           = mapped_column(String(2), default="ES")
    total_area_m2: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    floors_count: Mapped[int]      = mapped_column(Integer, default=1)
    # JSONB: módulos activos, SLAs por defecto, horarios, configuración específica
    config: Mapped[dict]           = mapped_column(JSONB, default=dict)
    active: Mapped[bool]           = mapped_column(Boolean, default=True)
    
    # Relationships
    tenant:     Mapped["Tenant"]      = relationship(back_populates="centers")
    assets:     Mapped[List["Asset"]] = relationship(back_populates="center")
    spaces:     Mapped[List["Space"]] = relationship(back_populates="center")
    
    __table_args__ = (
        Index("idx_centers_tenant", "tenant_id"),
    )


class Asset(Base, TimestampMixin, TenantMixin):
    """
    Activo físico con árbol jerárquico self-referencial.
    Centro → Planta → Zona → Activo → Sub-componente (N niveles).
    """
    __tablename__ = "assets"
    
    id: Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True
    )
    code: Mapped[str]      = mapped_column(String(100), nullable=False)
    name: Mapped[str]      = mapped_column(String(255), nullable=False)
    category: Mapped[AssetCategory]  = mapped_column(SQLEnum(AssetCategory), nullable=False)
    status: Mapped[AssetStatus]      = mapped_column(SQLEnum(AssetStatus), default=AssetStatus.OPERATIONAL)
    criticality: Mapped[Criticality] = mapped_column(SQLEnum(Criticality), default=Criticality.MEDIUM)
    
    # Ubicación física
    floor: Mapped[Optional[str]]   = mapped_column(String(20))    # "P3", "Sótano", "Cubierta"
    zone: Mapped[Optional[str]]    = mapped_column(String(100))   # "Zona Norte", "Sala técnica"
    location_notes: Mapped[Optional[str]] = mapped_column(Text)
    
    # Datos de adquisición y ciclo de vida
    serial_number: Mapped[Optional[str]]   = mapped_column(String(100))
    purchase_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    warranty_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expected_life_years: Mapped[Optional[int]] = mapped_column(Integer)
    purchase_cost: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    
    # Especificaciones técnicas — JSONB flexible por categoría
    # HVAC: {cooling_kw, refrigerant, brand, model, filter_type}
    # Pool: {volume_m3, target_ph, target_chlorine, lanes}
    # Fitness: {brand, model, usage_hours, belt_condition}
    specs: Mapped[dict] = mapped_column(JSONB, default=dict)
    
    # QR code URL (generado automáticamente al crear)
    qr_code_url: Mapped[Optional[str]] = mapped_column(String(500))
    
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Relationships
    center:      Mapped["Center"]         = relationship(back_populates="assets")
    parent:      Mapped[Optional["Asset"]] = relationship(remote_side="Asset.id", back_populates="children")
    children:    Mapped[List["Asset"]]     = relationship(back_populates="parent")
    work_orders: Mapped[List["WorkOrder"]] = relationship(back_populates="asset")
    pm_plans:    Mapped[List["PMPlan"]]    = relationship(back_populates="asset")
    sensors:     Mapped[List["Sensor"]]    = relationship(back_populates="asset")
    
    __table_args__ = (
        # Índice principal para lista de activos por centro
        Index("idx_assets_center_cat", "tenant_id", "center_id", "category", "status"),
        # Índice para árbol jerárquico
        Index("idx_assets_parent", "parent_id", "center_id"),
        # Código único por tenant
        UniqueConstraint("tenant_id", "code", name="uq_assets_tenant_code"),
    )


class WorkOrder(Base, TimestampMixin, TenantMixin):
    """
    Orden de Trabajo — entidad central del sistema FM.
    Cubre Hard FM (correctivo, preventivo, predictivo)
    y Soft FM (limpieza, seguridad, jardinería).
    """
    __tablename__ = "work_orders"
    
    id: Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False)
    
    # Identificador legible: OT-2025-00341
    code: Mapped[str]   = mapped_column(String(50), nullable=False)
    title: Mapped[str]  = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    
    type: Mapped[WorkOrderType]   = mapped_column(SQLEnum(WorkOrderType), nullable=False)
    status: Mapped[WorkOrderStatus] = mapped_column(SQLEnum(WorkOrderStatus), default=WorkOrderStatus.DRAFT)
    priority: Mapped[Priority]    = mapped_column(SQLEnum(Priority), default=Priority.MEDIUM)
    
    # Vínculos opcionales
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
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    
    # SLA y timing
    sla_deadline: Mapped[Optional[datetime]]  = mapped_column(DateTime(timezone=True))
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[Optional[datetime]]    = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]]  = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]]     = mapped_column(DateTime(timezone=True))
    
    # Costos
    estimated_cost: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    actual_cost: Mapped[Optional[float]]    = mapped_column(Numeric(12, 2))
    
    # Resolución
    resolution: Mapped[Optional[str]] = mapped_column(Text)
    
    # Campos custom por tipo de OT o centro
    metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    
    # Idempotency para POST
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100))
    
    # Relationships
    asset:      Mapped[Optional["Asset"]] = relationship(back_populates="work_orders")
    space:      Mapped[Optional["Space"]] = relationship(back_populates="work_orders")
    technician: Mapped[Optional["User"]]  = relationship(foreign_keys=[assigned_to])
    pm_plan:    Mapped[Optional["PMPlan"]] = relationship(back_populates="work_orders")
    checklist_items: Mapped[List["WOChecklistItem"]] = relationship(back_populates="work_order", cascade="all, delete-orphan")
    attachments: Mapped[List["WOAttachment"]] = relationship(back_populates="work_order", cascade="all, delete-orphan")
    time_logs:   Mapped[List["WOTimeLog"]]    = relationship(back_populates="work_order", cascade="all, delete-orphan")
    
    __table_args__ = (
        # Índice dashboard: OTs abiertas ordenadas por urgencia SLA
        Index(
            "idx_wo_status_sla",
            "tenant_id", "center_id", "status", "sla_deadline",
            postgresql_where=text("status NOT IN ('closed', 'cancelled')")
        ),
        # Índice historial por activo para MTTR/MTBF
        Index("idx_wo_asset_history", "asset_id", "created_at"),
        # Código único por tenant
        UniqueConstraint("tenant_id", "code", name="uq_wo_tenant_code"),
        # Idempotency key única
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_wo_idempotency"),
    )
    
    @property
    def sla_overdue(self) -> bool:
        """True si el SLA está vencido y la OT no está cerrada."""
        if not self.sla_deadline:
            return False
        if self.status in (WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED):
            return False
        return datetime.utcnow() > self.sla_deadline.replace(tzinfo=None)
    
    @property
    def mttr_hours(self) -> Optional[float]:
        """Mean Time To Repair en horas."""
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            return round(delta.total_seconds() / 3600, 2)
        return None


class WOChecklistItem(Base, TenantMixin):
    """Ítem de checklist de una OT."""
    __tablename__ = "wo_checklist_items"
    
    id: Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False)
    order_index: Mapped[int]  = mapped_column(Integer, nullable=False)
    title: Mapped[str]        = mapped_column(String(500), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Respuesta
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    value: Mapped[Optional[float]] = mapped_column(Numeric(10, 3))  # Para ítems numéricos
    note: Mapped[Optional[str]]    = mapped_column(Text)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    
    work_order: Mapped["WorkOrder"] = relationship(back_populates="checklist_items")


class WOAttachment(Base, TenantMixin):
    """Adjunto (foto, documento) de una OT."""
    __tablename__ = "wo_attachments"
    
    id: Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False)
    filename: Mapped[str]      = mapped_column(String(255), nullable=False)
    content_type: Mapped[str]  = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int]    = mapped_column(Integer, nullable=False)
    s3_key: Mapped[str]        = mapped_column(String(500), nullable=False)
    attachment_type: Mapped[str] = mapped_column(String(50), default="evidence_photo")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    
    work_order: Mapped["WorkOrder"] = relationship(back_populates="attachments")


class WOTimeLog(Base, TenantMixin):
    """Registro de tiempo trabajado en una OT."""
    __tablename__ = "wo_time_logs"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False)
    user_id: Mapped[uuid.UUID]       = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime]     = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]]     = mapped_column(Text)
    
    work_order: Mapped["WorkOrder"] = relationship(back_populates="time_logs")


class PMPlan(Base, TimestampMixin, TenantMixin):
    """Plan de mantenimiento preventivo — genera OTs automáticamente."""
    __tablename__ = "pm_plans"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    
    name: Mapped[str]               = mapped_column(String(255), nullable=False)
    trigger_type: Mapped[PMTriggerType] = mapped_column(SQLEnum(PMTriggerType), nullable=False)
    priority: Mapped[Priority]      = mapped_column(SQLEnum(Priority), default=Priority.MEDIUM)
    
    # Frecuencia — JSONB flexible:
    # calendar:      {"every": 1, "unit": "month", "day_of_month": 1}
    # usage_hours:   {"hours": 500}
    # usage_cycles:  {"cycles": 1000}
    frequency: Mapped[dict]         = mapped_column(JSONB, nullable=False)
    
    checklist_template_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    estimated_duration_min: Mapped[Optional[int]]      = mapped_column(Integer)
    estimated_cost: Mapped[Optional[float]]            = mapped_column(Numeric(10, 2))
    
    # Scheduling
    next_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    asset:       Mapped["Asset"]         = relationship(back_populates="pm_plans")
    work_orders: Mapped[List["WorkOrder"]] = relationship(back_populates="pm_plan")
    
    __table_args__ = (
        # Índice para el scheduler (verifica cada hora qué planes vencen)
        Index(
            "idx_pm_plans_due",
            "tenant_id", "next_due_at",
            postgresql_where=text("active = true")
        ),
    )


class Sensor(Base, TimestampMixin, TenantMixin):
    """Dispositivo IoT físico vinculado a un activo o espacio."""
    __tablename__ = "sensors"
    
    id: Mapped[uuid.UUID]    = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"))
    space_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("spaces.id"))
    
    name: Mapped[str]       = mapped_column(String(255), nullable=False)
    device_id: Mapped[str]  = mapped_column(String(255), nullable=False)  # ID del hardware
    metric_type: Mapped[MetricType] = mapped_column(SQLEnum(MetricType), nullable=False)
    unit: Mapped[str]       = mapped_column(String(20), nullable=False)   # "°C", "kWh", "pH"
    
    # Reglas de alerta — JSONB:
    # {"min": 6.8, "max": 7.8, "action": "create_work_order", "priority": "high"}
    alert_rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    
    # Estado actual (desnormalizado para performance)
    last_value: Mapped[Optional[float]]    = mapped_column(Numeric(10, 4))
    last_reading_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    in_alert: Mapped[bool] = mapped_column(Boolean, default=False)
    
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    asset: Mapped[Optional["Asset"]] = relationship(back_populates="sensors")
    readings: Mapped[List["SensorReading"]] = relationship(back_populates="sensor")


class SensorReading(Base):
    """
    Lectura individual de sensor — TimescaleDB Hypertable.
    
    NOTA: Esta tabla se convierte en hypertable con:
        SELECT create_hypertable('sensor_readings', 'time',
            chunk_time_interval => INTERVAL '7 days');
    
    No incluye TenantMixin porque el tenant se deriva del sensor.
    Los índices son gestionados por TimescaleDB automáticamente.
    """
    __tablename__ = "sensor_readings"
    
    # Clave compuesta (time, sensor_id) — requerida por TimescaleDB
    time: Mapped[datetime]  = mapped_column(DateTime(timezone=True), nullable=False, primary_key=True)
    sensor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sensors.id"), nullable=False, primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    
    value: Mapped[float]  = mapped_column(Numeric(10, 4), nullable=False)
    unit: Mapped[str]     = mapped_column(String(20), nullable=False)
    quality: Mapped[str]  = mapped_column(String(20), default="good")  # good, suspect, bad
    
    sensor: Mapped["Sensor"] = relationship(back_populates="readings")
    
    __table_args__ = (
        # Índice para queries de series temporales por sensor
        Index("idx_sensor_readings_sensor_time", "sensor_id", "time"),
        # Índice para queries por tenant y rango temporal
        Index("idx_sensor_readings_tenant_time", "tenant_id", "time"),
    )


class User(Base, TimestampMixin, TenantMixin):
    """Usuario del sistema — sincronizado con Keycloak."""
    __tablename__ = "users"
    
    # ID = keycloak_user_id para sincronización directa
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str]    = mapped_column(String(255), nullable=False)
    name: Mapped[str]     = mapped_column(String(255), nullable=False)
    user_type: Mapped[UserType] = mapped_column(SQLEnum(UserType), nullable=False)
    
    # Especializaciones del técnico para asignación automática de OTs
    specializations: Mapped[List[str]] = mapped_column(ARRAY(String), default=list)
    
    # Token para push notifications (Expo)
    push_token: Mapped[Optional[str]] = mapped_column(String(500))
    
    # Centros accesibles (relación N:M gestionada por Keycloak + tabla de roles)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index("idx_users_tenant_type", "tenant_id", "user_type"),
    )


class Contract(Base, TimestampMixin, TenantMixin):
    """Contrato con proveedor — define SLAs operativos."""
    __tablename__ = "contracts"
    
    id: Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    name: Mapped[str]               = mapped_column(String(255), nullable=False)
    service_type: Mapped[ContractServiceType] = mapped_column(SQLEnum(ContractServiceType), nullable=False)
    reference_number: Mapped[Optional[str]]   = mapped_column(String(100))
    
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime]   = mapped_column(DateTime(timezone=True), nullable=False)
    
    amount: Mapped[Optional[float]]   = mapped_column(Numeric(12, 2))
    currency: Mapped[str]             = mapped_column(String(3), default="EUR")
    
    # SLA config — JSONB:
    # {
    #   "emergency": {"response_hours": 2, "resolution_hours": 8, "penalty_pct": 5},
    #   "high":      {"response_hours": 8, "resolution_hours": 24, "penalty_pct": 3},
    #   ...
    # }
    sla_config: Mapped[dict]    = mapped_column(JSONB, default=dict)
    penalty_rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    
    notes: Mapped[Optional[str]] = mapped_column(Text)
    document_s3_key: Mapped[Optional[str]] = mapped_column(String(500))
    
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    tenant: Mapped["Tenant"] = relationship(back_populates="contracts")
    work_orders: Mapped[List["WorkOrder"]] = relationship(back_populates="contract")
    
    __table_args__ = (
        # Índice para alertas de vencimiento (job diario)
        Index(
            "idx_contracts_expiry",
            "tenant_id", "end_date",
            postgresql_where=text("active = true")
        ),
    )


class Space(Base, TimestampMixin, TenantMixin):
    """Espacio reservable — salas de reuniones, pistas deportivas, locales."""
    __tablename__ = "spaces"
    
    id: Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False)
    
    name: Mapped[str]           = mapped_column(String(255), nullable=False)
    space_type: Mapped[str]     = mapped_column(String(50))   # meeting_room, sports_court, retail_unit, desk
    floor: Mapped[Optional[str]] = mapped_column(String(20))
    area_m2: Mapped[Optional[float]] = mapped_column(Numeric(8, 2))
    capacity: Mapped[Optional[int]]  = mapped_column(Integer)
    
    # Equipamiento disponible
    amenities: Mapped[List[str]] = mapped_column(ARRAY(String), default=list)
    
    # Tarifa si aplica (locales comerciales, pistas deportivas)
    hourly_rate: Mapped[Optional[float]] = mapped_column(Numeric(8, 2))
    
    # Para retail: estado de ocupación del local
    vacancy_status: Mapped[Optional[str]] = mapped_column(String(20))  # occupied, available, reserved
    
    bookable: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool]   = mapped_column(Boolean, default=True)
    
    center:      Mapped["Center"]         = relationship(back_populates="spaces")
    work_orders: Mapped[List["WorkOrder"]] = relationship(back_populates="space")
    bookings:    Mapped[List["SpaceBooking"]] = relationship(back_populates="space")


class SpaceBooking(Base, TimestampMixin, TenantMixin):
    """Reserva de espacio por un inquilino o usuario interno."""
    __tablename__ = "space_bookings"
    
    id: Mapped[uuid.UUID]    = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("spaces.id"), nullable=False)
    user_id: Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    title: Mapped[str]        = mapped_column(String(255), nullable=False)
    attendees_count: Mapped[int] = mapped_column(Integer, default=1)
    
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime]   = mapped_column(DateTime(timezone=True), nullable=False)
    
    amenities_requested: Mapped[List[str]] = mapped_column(ARRAY(String), default=list)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str]          = mapped_column(String(20), default="confirmed")
    
    space: Mapped["Space"] = relationship(back_populates="bookings")
    
    __table_args__ = (
        Index("idx_bookings_space_time", "space_id", "start_time", "end_time"),
    )


class ServiceRoute(Base, TimestampMixin, TenantMixin):
    """Ruta de Soft FM — limpieza, seguridad, jardinería."""
    __tablename__ = "service_routes"
    
    id: Mapped[uuid.UUID]    = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False)
    
    name: Mapped[str]               = mapped_column(String(255), nullable=False)
    service_type: Mapped[SoftFMServiceType] = mapped_column(SQLEnum(SoftFMServiceType), nullable=False)
    
    # Zonas de la ruta con NFC tags
    # [{"zone": "Lobby PB", "nfc_tag_id": "uuid", "order": 1, "expected_duration_min": 15}]
    zones: Mapped[List[dict]] = mapped_column(JSONB, default=list)
    
    # Frecuencia de ejecución
    frequency: Mapped[dict]   = mapped_column(JSONB, default=dict)
    
    assigned_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    active: Mapped[bool]      = mapped_column(Boolean, default=True)
    
    executions: Mapped[List["RouteExecution"]] = relationship(back_populates="route")


class RouteExecution(Base, TenantMixin):
    """Ejecución de una ruta de Soft FM con check-ins NFC."""
    __tablename__ = "route_executions"
    
    id: Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    route_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("service_routes.id"), nullable=False)
    user_id: Mapped[uuid.UUID]  = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    started_at: Mapped[datetime]              = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]]  = mapped_column(DateTime(timezone=True))
    
    # Check-ins por zona: [{"zone_id": "...", "checked_in_at": "...", "lat": ..., "lng": ...}]
    check_ins: Mapped[List[dict]] = mapped_column(JSONB, default=list)
    
    # Incidencias detectadas durante la ruta
    incidents: Mapped[List[dict]] = mapped_column(JSONB, default=list)
    
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    
    route: Mapped["ServiceRoute"] = relationship(back_populates="executions")


class Invoice(Base, TimestampMixin, TenantMixin):
    """Factura de gastos comunes para inquilinos."""
    __tablename__ = "invoices"
    
    id: Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    center_id: Mapped[uuid.UUID]   = mapped_column(UUID(as_uuid=True), ForeignKey("centers.id"), nullable=False)
    tenant_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    period_month: Mapped[int]    = mapped_column(Integer, nullable=False)  # 1-12
    period_year: Mapped[int]     = mapped_column(Integer, nullable=False)
    
    amount: Mapped[float]        = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str]        = mapped_column(String(3), default="EUR")
    
    due_date: Mapped[datetime]   = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    status: Mapped[str]          = mapped_column(String(20), default="pending")
    s3_key: Mapped[Optional[str]] = mapped_column(String(500))  # PDF
    
    line_items: Mapped[List[dict]] = mapped_column(JSONB, default=list)


class Webhook(Base, TimestampMixin, TenantMixin):
    """Configuración de webhook saliente para un tenant."""
    __tablename__ = "webhooks"
    
    id: Mapped[uuid.UUID]   = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url: Mapped[str]        = mapped_column(String(500), nullable=False)
    events: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False)
    secret: Mapped[str]     = mapped_column(String(255), nullable=False)
    active: Mapped[bool]    = mapped_column(Boolean, default=True)
    
    # Stats de entregas
    last_delivery_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_delivery_status: Mapped[Optional[int]]  = mapped_column(Integer)
    failure_count: Mapped[int]                   = mapped_column(Integer, default=0)
    
    tenant: Mapped["Tenant"] = relationship(back_populates="webhooks")
