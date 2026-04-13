"""
FM Platform — Modelos de Base de Datos (SQLAlchemy 2.0)
PostgreSQL con Row Level Security multi-tenant
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Date,
    ForeignKey, Enum, Text, JSON, Numeric, UniqueConstraint,
    Index, event, text
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, relationship, Mapped, mapped_column
from sqlalchemy.sql import func


# ─────────────────────────────────────────────
# BASE
# ─────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


def generate_uuid() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class TenantPlan(str, enum.Enum):
    STARTER    = "starter"
    GROWTH     = "growth"
    SCALE      = "scale"
    ENTERPRISE = "enterprise"


class CenterType(str, enum.Enum):
    OFFICE  = "office"
    MALL    = "mall"
    SPORT   = "sport"
    MIXED   = "mixed"


class AssetCategory(str, enum.Enum):
    HVAC             = "hvac"
    ELECTRICAL       = "electrical"
    PLUMBING         = "plumbing"
    ELEVATOR         = "elevator"
    FIRE_SAFETY      = "fire_safety"
    SECURITY         = "security"
    FITNESS          = "fitness_equipment"
    POOL             = "pool"
    LOCKER_ROOM      = "locker_room"
    SPORTS_COURT     = "sports_court"
    WELLNESS         = "wellness"
    TELECOM          = "telecom"
    GENERATION       = "generation"
    SIGNAGE          = "signage"
    LOGISTICS        = "logistics"
    DOORS_SHUTTERS   = "doors_shutters"
    ACCESS_CONTROL   = "access_control"
    OTHER            = "other"


class AssetStatus(str, enum.Enum):
    OPERATIONAL   = "operational"
    MAINTENANCE   = "maintenance"
    OUT_OF_SERVICE = "out_of_service"
    RETIRED       = "retired"


class AssetCriticality(str, enum.Enum):
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


class WorkOrderPriority(str, enum.Enum):
    EMERGENCY = "emergency"
    HIGH      = "high"
    MEDIUM    = "medium"
    LOW       = "low"


class UserType(str, enum.Enum):
    STAFF           = "staff"
    TECHNICIAN      = "technician"
    VENDOR          = "vendor"
    TENANT_CONTACT  = "tenant_contact"


class ServiceType(str, enum.Enum):
    MAINTENANCE  = "maintenance"
    CLEANING     = "cleaning"
    SECURITY     = "security"
    LANDSCAPING  = "landscaping"
    ELEVATOR     = "elevator"
    PEST_CONTROL = "pest_control"
    OTHER        = "other"


class MetricType(str, enum.Enum):
    TEMPERATURE  = "temperature"
    ENERGY_KWH   = "energy_kwh"
    WATER_PH     = "water_ph"
    CHLORINE_PPM = "chlorine_ppm"
    OCCUPANCY    = "occupancy"
    VIBRATION    = "vibration"
    HUMIDITY     = "humidity"
    CO2_PPM      = "co2_ppm"
    WATER_FLOW   = "water_flow"


class PMTriggerType(str, enum.Enum):
    CALENDAR   = "calendar"
    USAGE_HOURS = "usage_hours"
    USAGE_CYCLES = "usage_cycles"
    CONDITION  = "condition"


# ─────────────────────────────────────────────
# TENANT
# ─────────────────────────────────────────────

class Tenant(Base):
    """
    Raíz del árbol multi-tenant.
    Todas las demás entidades tienen tenant_id con FK aquí.
    RLS se activa con SET app.current_tenant = '...'
    """
    __tablename__ = "tenants"

    id         : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    slug       : Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name       : Mapped[str] = mapped_column(String(200), nullable=False)
    plan       : Mapped[TenantPlan] = mapped_column(Enum(TenantPlan), nullable=False, default=TenantPlan.STARTER)
    settings   : Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    active     : Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    centers   : Mapped[List["Center"]] = relationship(back_populates="tenant")
    users     : Mapped[List["User"]]   = relationship(back_populates="tenant")
    vendors   : Mapped[List["Vendor"]] = relationship(back_populates="tenant")

    def __repr__(self) -> str:
        return f"<Tenant {self.slug}>"


# ─────────────────────────────────────────────
# CENTER
# ─────────────────────────────────────────────

class Center(Base):
    """
    Instalación física gestionada.
    El campo 'type' activa módulos específicos (piscinas, locales, etc.)
    """
    __tablename__ = "centers"

    id           : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    tenant_id    : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    name         : Mapped[str] = mapped_column(String(200), nullable=False)
    type         : Mapped[CenterType] = mapped_column(Enum(CenterType), nullable=False)
    address      : Mapped[Optional[str]] = mapped_column(Text)
    city         : Mapped[Optional[str]] = mapped_column(String(100))
    country      : Mapped[str] = mapped_column(String(2), nullable=False, default="ES")
    latitude     : Mapped[Optional[float]] = mapped_column(Float)
    longitude    : Mapped[Optional[float]] = mapped_column(Float)
    total_area_m2: Mapped[Optional[float]] = mapped_column(Float)
    floors       : Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config       : Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # config example: {"modules": ["soft_fm", "iot", "tenants"], "sla_defaults": {...}, "timezone": "Europe/Madrid"}
    active       : Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at   : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    tenant      : Mapped["Tenant"]            = relationship(back_populates="centers")
    assets      : Mapped[List["Asset"]]       = relationship(back_populates="center")
    spaces      : Mapped[List["Space"]]       = relationship(back_populates="center")
    work_orders : Mapped[List["WorkOrder"]]   = relationship(back_populates="center")
    contracts   : Mapped[List["Contract"]]    = relationship(back_populates="center")
    sensors     : Mapped[List["Sensor"]]      = relationship(back_populates="center")

    __table_args__ = (
        Index("idx_centers_tenant", "tenant_id"),
    )

    def __repr__(self) -> str:
        return f"<Center {self.name} ({self.type})>"


# ─────────────────────────────────────────────
# ASSET
# ─────────────────────────────────────────────

class Asset(Base):
    """
    Activo físico con árbol jerárquico self-referencial.
    Centro → Planta → Zona → Activo → Sub-componente (profundidad ilimitada).
    specs JSONB es flexible por categoría:
      - HVAC: {cooling_kw, refrigerant, brand, model}
      - pool: {volume_m3, target_ph, target_chlorine}
      - fitness: {brand, usage_hours, belt_condition}
    """
    __tablename__ = "assets"

    id                  : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    tenant_id           : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    center_id           : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("centers.id"), nullable=False)
    parent_id           : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"))
    code                : Mapped[str] = mapped_column(String(100), nullable=False)
    name                : Mapped[str] = mapped_column(String(300), nullable=False)
    category            : Mapped[AssetCategory] = mapped_column(Enum(AssetCategory), nullable=False)
    status              : Mapped[AssetStatus] = mapped_column(Enum(AssetStatus), nullable=False, default=AssetStatus.OPERATIONAL)
    criticality         : Mapped[AssetCriticality] = mapped_column(Enum(AssetCriticality), nullable=False, default=AssetCriticality.MEDIUM)
    location            : Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # location: {"floor": "P3", "zone": "Norte", "room": "Cuarto técnico"}
    specs               : Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    purchase_date       : Mapped[Optional[datetime]] = mapped_column(Date)
    warranty_until      : Mapped[Optional[datetime]] = mapped_column(Date)
    expected_life_years : Mapped[Optional[int]] = mapped_column(Integer)
    purchase_value      : Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    serial_number       : Mapped[Optional[str]] = mapped_column(String(200))
    manufacturer        : Mapped[Optional[str]] = mapped_column(String(200))
    model               : Mapped[Optional[str]] = mapped_column(String(200))
    qr_code             : Mapped[Optional[str]] = mapped_column(String(500))  # URL imagen QR
    notes               : Mapped[Optional[str]] = mapped_column(Text)
    active              : Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at          : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at          : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Self-referential tree
    parent   : Mapped[Optional["Asset"]]  = relationship("Asset", remote_side="Asset.id", back_populates="children")
    children : Mapped[List["Asset"]]      = relationship("Asset", back_populates="parent")

    # Relationships
    center      : Mapped["Center"]          = relationship(back_populates="assets")
    work_orders : Mapped[List["WorkOrder"]] = relationship(back_populates="asset")
    pm_plans    : Mapped[List["PMPlan"]]    = relationship(back_populates="asset")
    sensors     : Mapped[List["Sensor"]]    = relationship(back_populates="asset")

    __table_args__ = (
        UniqueConstraint("tenant_id", "center_id", "code", name="uq_asset_code"),
        # Índice principal para listado por centro
        Index("idx_assets_center_cat", "tenant_id", "center_id", "category", "status"),
        # Índice para árbol jerárquico
        Index("idx_assets_parent", "parent_id", "center_id"),
        # Índice para búsqueda por código (QR scan)
        Index("idx_assets_code", "code", "center_id"),
    )

    def __repr__(self) -> str:
        return f"<Asset {self.code} - {self.name}>"


# ─────────────────────────────────────────────
# SPACE
# ─────────────────────────────────────────────

class Space(Base):
    """
    Espacio físico reservable: sala de reuniones, pista deportiva, local comercial.
    """
    __tablename__ = "spaces"

    id          : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    tenant_id   : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    center_id   : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("centers.id"), nullable=False)
    name        : Mapped[str] = mapped_column(String(200), nullable=False)
    floor       : Mapped[Optional[str]] = mapped_column(String(20))
    area_m2     : Mapped[Optional[float]] = mapped_column(Float)
    capacity    : Mapped[Optional[int]] = mapped_column(Integer)
    amenities   : Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # amenities: ["projector", "videoconf", "whiteboard"]
    bookable    : Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active      : Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at  : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    center      : Mapped["Center"]              = relationship(back_populates="spaces")
    bookings    : Mapped[List["SpaceBooking"]]  = relationship(back_populates="space")


# ─────────────────────────────────────────────
# USER
# ─────────────────────────────────────────────

class User(Base):
    """
    Cualquier persona con acceso al sistema.
    id = keycloak_user_id (sincronizado desde Keycloak).
    Roles y permisos por centro en user_center_roles.
    """
    __tablename__ = "users"

    id              : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)  # = Keycloak ID
    tenant_id       : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    email           : Mapped[str] = mapped_column(String(300), nullable=False)
    name            : Mapped[str] = mapped_column(String(300), nullable=False)
    phone           : Mapped[Optional[str]] = mapped_column(String(30))
    user_type       : Mapped[UserType] = mapped_column(Enum(UserType), nullable=False)
    specializations : Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # specializations: ["hvac", "electrical", "plumbing", "cleaning"]
    push_token      : Mapped[Optional[str]] = mapped_column(String(500))  # Expo push token
    active          : Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at      : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant          : Mapped["Tenant"]                  = relationship(back_populates="users")
    center_roles    : Mapped[List["UserCenterRole"]]    = relationship(back_populates="user")
    work_orders_assigned : Mapped[List["WorkOrder"]]    = relationship(back_populates="assigned_to_user",
                                                                        foreign_keys="WorkOrder.assigned_to")

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_user_email"),
        Index("idx_users_tenant", "tenant_id"),
    )


class UserCenterRole(Base):
    """Rol del usuario en un centro específico (N:M con roles)."""
    __tablename__ = "user_center_roles"

    id        : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    user_id   : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    center_id : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("centers.id"), nullable=False)
    role      : Mapped[str] = mapped_column(String(50), nullable=False)
    # role: "fm_director" | "fm_manager" | "technician" | "supervisor" | "viewer"

    user      : Mapped["User"]   = relationship(back_populates="center_roles")
    center    : Mapped["Center"] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", "center_id", name="uq_user_center"),
    )


# ─────────────────────────────────────────────
# VENDOR
# ─────────────────────────────────────────────

class Vendor(Base):
    """Empresa proveedora de servicios de mantenimiento o Soft FM."""
    __tablename__ = "vendors"

    id          : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    tenant_id   : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    name        : Mapped[str] = mapped_column(String(300), nullable=False)
    tax_id      : Mapped[Optional[str]] = mapped_column(String(50))
    email       : Mapped[Optional[str]] = mapped_column(String(300))
    phone       : Mapped[Optional[str]] = mapped_column(String(30))
    services    : Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    rating      : Mapped[Optional[float]] = mapped_column(Float)
    active      : Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at  : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant      : Mapped["Tenant"]          = relationship(back_populates="vendors")
    contracts   : Mapped[List["Contract"]]  = relationship(back_populates="vendor")


# ─────────────────────────────────────────────
# CONTRACT
# ─────────────────────────────────────────────

class Contract(Base):
    """
    Contrato con proveedor. Define SLAs y penalizaciones automáticas.
    sla_config JSONB:
      {"emergency": {"response_hours": 2, "resolution_hours": 8, "penalty_pct": 5}, ...}
    """
    __tablename__ = "contracts"

    id           : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    tenant_id    : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    center_id    : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("centers.id"), nullable=False)
    vendor_id    : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("vendors.id"), nullable=False)
    service_type : Mapped[ServiceType] = mapped_column(Enum(ServiceType), nullable=False)
    reference    : Mapped[Optional[str]] = mapped_column(String(100))
    start_date   : Mapped[datetime] = mapped_column(Date, nullable=False)
    end_date     : Mapped[datetime] = mapped_column(Date, nullable=False)
    amount       : Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    currency     : Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    sla_config   : Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    penalty_rules: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    auto_renew   : Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status       : Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    notes        : Mapped[Optional[str]] = mapped_column(Text)
    created_at   : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    center      : Mapped["Center"]          = relationship(back_populates="contracts")
    vendor      : Mapped["Vendor"]          = relationship(back_populates="contracts")
    work_orders : Mapped[List["WorkOrder"]] = relationship(back_populates="contract")

    __table_args__ = (
        # Alerta de vencimiento
        Index("idx_contracts_expiry", "tenant_id", "end_date", postgresql_where=text("status = 'active'")),
    )


# ─────────────────────────────────────────────
# WORK ORDER
# ─────────────────────────────────────────────

class WorkOrder(Base):
    """
    Entidad central del sistema.
    Gestiona Hard FM (correctivo, preventivo, predictivo)
    y Soft FM (limpieza, seguridad, jardinería) en la misma tabla.
    El campo 'type' diferencia el flujo de trabajo.
    """
    __tablename__ = "work_orders"

    id             : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    tenant_id      : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    center_id      : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("centers.id"), nullable=False)
    code           : Mapped[str] = mapped_column(String(50), nullable=False)  # OT-25-0341
    type           : Mapped[WorkOrderType] = mapped_column(Enum(WorkOrderType), nullable=False)
    status         : Mapped[WorkOrderStatus] = mapped_column(Enum(WorkOrderStatus), nullable=False, default=WorkOrderStatus.DRAFT)
    priority       : Mapped[WorkOrderPriority] = mapped_column(Enum(WorkOrderPriority), nullable=False, default=WorkOrderPriority.MEDIUM)
    title          : Mapped[str] = mapped_column(String(500), nullable=False)
    description    : Mapped[Optional[str]] = mapped_column(Text)
    # Foreign keys (opcionales según tipo)
    asset_id       : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"))
    space_id       : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("spaces.id"))
    assigned_to    : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_by     : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"))
    contract_id    : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("contracts.id"))
    pm_plan_id     : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("pm_plans.id"))
    # SLA
    sla_deadline   : Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sla_breached   : Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Tiempos operativos
    started_at     : Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at   : Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    verified_at    : Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at      : Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Costos
    estimated_cost : Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    actual_cost    : Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    # Resolución
    resolution     : Mapped[Optional[str]] = mapped_column(Text)
    # Campos custom por tipo de centro
    metadata       : Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at     : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at     : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    center             : Mapped["Center"]                   = relationship(back_populates="work_orders")
    asset              : Mapped[Optional["Asset"]]          = relationship(back_populates="work_orders")
    contract           : Mapped[Optional["Contract"]]       = relationship(back_populates="work_orders")
    assigned_to_user   : Mapped[Optional["User"]]          = relationship(back_populates="work_orders_assigned", foreign_keys=[assigned_to])
    pm_plan            : Mapped[Optional["PMPlan"]]        = relationship(back_populates="work_orders")
    checklist_items    : Mapped[List["WOChecklistItem"]]   = relationship(back_populates="work_order", cascade="all, delete-orphan")
    attachments        : Mapped[List["WOAttachment"]]      = relationship(back_populates="work_order", cascade="all, delete-orphan")
    time_logs          : Mapped[List["WOTimeLog"]]         = relationship(back_populates="work_order", cascade="all, delete-orphan")
    comments           : Mapped[List["WOComment"]]         = relationship(back_populates="work_order", cascade="all, delete-orphan")
    transitions        : Mapped[List["WOTransition"]]      = relationship(back_populates="work_order", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_wo_code"),
        # Índice principal dashboard: OTs abiertas por SLA
        Index("idx_wo_dashboard", "tenant_id", "center_id", "status", "sla_deadline",
              postgresql_where=text("status NOT IN ('closed', 'cancelled')")),
        # Historial por activo
        Index("idx_wo_asset_history", "asset_id", "created_at"),
        # Por técnico asignado
        Index("idx_wo_assigned", "assigned_to", "status"),
    )

    def __repr__(self) -> str:
        return f"<WorkOrder {self.code} [{self.status}]>"

    @property
    def mttr_hours(self) -> Optional[float]:
        """Mean Time To Repair en horas."""
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            return round(delta.total_seconds() / 3600, 2)
        return None


class WOChecklistItem(Base):
    """Ítem de checklist asociado a una OT."""
    __tablename__ = "wo_checklist_items"

    id           : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    work_order_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id"), nullable=False)
    order        : Mapped[int] = mapped_column(Integer, nullable=False)
    description  : Mapped[str] = mapped_column(Text, nullable=False)
    required     : Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed    : Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    value        : Mapped[Optional[float]] = mapped_column(Float)  # Para ítems numéricos (pH, presión)
    unit         : Mapped[Optional[str]] = mapped_column(String(20))
    note         : Mapped[Optional[str]] = mapped_column(Text)
    photo_url    : Mapped[Optional[str]] = mapped_column(String(500))
    completed_at : Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_by : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"))

    work_order   : Mapped["WorkOrder"] = relationship(back_populates="checklist_items")


class WOAttachment(Base):
    """Foto, documento o archivo adjunto a una OT."""
    __tablename__ = "wo_attachments"

    id            : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    work_order_id : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id"), nullable=False)
    type          : Mapped[str] = mapped_column(String(50), nullable=False)
    # type: "evidence_photo" | "before_photo" | "after_photo" | "document" | "signature"
    filename      : Mapped[str] = mapped_column(String(300), nullable=False)
    s3_key        : Mapped[str] = mapped_column(String(500), nullable=False)
    url           : Mapped[Optional[str]] = mapped_column(String(500))
    size_bytes    : Mapped[Optional[int]] = mapped_column(Integer)
    uploaded_by   : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at    : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    work_order    : Mapped["WorkOrder"] = relationship(back_populates="attachments")


class WOTimeLog(Base):
    """Registro de tiempo trabajado en una OT."""
    __tablename__ = "wo_time_logs"

    id            : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    work_order_id : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id"), nullable=False)
    user_id       : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    started_at    : Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at      : Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_min  : Mapped[Optional[int]] = mapped_column(Integer)
    notes         : Mapped[Optional[str]] = mapped_column(Text)

    work_order    : Mapped["WorkOrder"] = relationship(back_populates="time_logs")


class WOComment(Base):
    """Comentario / nota en una OT."""
    __tablename__ = "wo_comments"

    id            : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    work_order_id : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id"), nullable=False)
    author_id     : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    text          : Mapped[str] = mapped_column(Text, nullable=False)
    is_internal   : Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at    : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    work_order    : Mapped["WorkOrder"] = relationship(back_populates="comments")


class WOTransition(Base):
    """Audit trail de cambios de estado en una OT (immutable log)."""
    __tablename__ = "wo_transitions"

    id            : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    work_order_id : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id"), nullable=False)
    from_status   : Mapped[Optional[str]] = mapped_column(String(30))
    to_status     : Mapped[str] = mapped_column(String(30), nullable=False)
    action        : Mapped[str] = mapped_column(String(50), nullable=False)
    triggered_by  : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"))
    comment       : Mapped[Optional[str]] = mapped_column(Text)
    metadata      : Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at    : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    work_order    : Mapped["WorkOrder"] = relationship(back_populates="transitions")


# ─────────────────────────────────────────────
# PM PLAN (Mantenimiento Preventivo)
# ─────────────────────────────────────────────

class ChecklistTemplate(Base):
    """Plantilla de checklist reutilizable para planes preventivos."""
    __tablename__ = "checklist_templates"

    id          : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    tenant_id   : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    name        : Mapped[str] = mapped_column(String(200), nullable=False)
    category    : Mapped[Optional[AssetCategory]] = mapped_column(Enum(AssetCategory))
    items       : Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # items: [{"order": 1, "description": "Verificar fusibles", "required": true, "type": "boolean"}]
    created_at  : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PMPlan(Base):
    """
    Plan de mantenimiento preventivo.
    Genera OTs automáticamente según trigger_type y frequency.
    """
    __tablename__ = "pm_plans"

    id                    : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    tenant_id             : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    asset_id              : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"), nullable=False)
    name                  : Mapped[str] = mapped_column(String(300), nullable=False)
    trigger_type          : Mapped[PMTriggerType] = mapped_column(Enum(PMTriggerType), nullable=False)
    frequency             : Mapped[dict] = mapped_column(JSONB, nullable=False)
    # calendar: {"every": 1, "unit": "month"}
    # usage:    {"hours": 500} o {"cycles": 1000}
    checklist_template_id : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("checklist_templates.id"))
    priority              : Mapped[WorkOrderPriority] = mapped_column(Enum(WorkOrderPriority), nullable=False, default=WorkOrderPriority.MEDIUM)
    estimated_duration_min: Mapped[Optional[int]] = mapped_column(Integer)
    assigned_to           : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"))
    next_due_at           : Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_executed_at      : Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    active                : Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at            : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    asset       : Mapped["Asset"]           = relationship(back_populates="pm_plans")
    work_orders : Mapped[List["WorkOrder"]] = relationship(back_populates="pm_plan")

    __table_args__ = (
        Index("idx_pm_due", "tenant_id", "next_due_at",
              postgresql_where=text("active = true")),
    )


# ─────────────────────────────────────────────
# IOT / SENSORES
# ─────────────────────────────────────────────

class Sensor(Base):
    """
    Dispositivo IoT físico. Las lecturas van a TimescaleDB (sensor_readings).
    alert_rules JSONB: {"min": 6.8, "max": 7.8, "action": "create_work_order", "priority": "emergency"}
    """
    __tablename__ = "sensors"

    id          : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    tenant_id   : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    center_id   : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("centers.id"), nullable=False)
    asset_id    : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("assets.id"))
    name        : Mapped[str] = mapped_column(String(200), nullable=False)
    metric_type : Mapped[MetricType] = mapped_column(Enum(MetricType), nullable=False)
    unit        : Mapped[str] = mapped_column(String(20), nullable=False)
    device_id   : Mapped[Optional[str]] = mapped_column(String(200))  # MAC / IMEI del dispositivo
    protocol    : Mapped[str] = mapped_column(String(20), nullable=False, default="mqtt")
    alert_rules : Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_value  : Mapped[Optional[float]] = mapped_column(Float)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    active      : Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at  : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    center      : Mapped["Center"]          = relationship(back_populates="sensors")
    asset       : Mapped[Optional["Asset"]] = relationship(back_populates="sensors")


# NOTA: SensorReading va en TimescaleDB como hypertable
# CREATE TABLE sensor_readings (
#   time        TIMESTAMPTZ NOT NULL,
#   sensor_id   UUID        NOT NULL REFERENCES sensors(id),
#   tenant_id   UUID        NOT NULL,
#   value       NUMERIC     NOT NULL,
#   unit        TEXT        NOT NULL,
#   quality     TEXT        DEFAULT 'good'
# );
# SELECT create_hypertable('sensor_readings', 'time', chunk_time_interval => INTERVAL '7 days');
# ALTER TABLE sensor_readings SET (timescaledb.compress, timescaledb.compress_segmentby = 'sensor_id');
# SELECT add_compression_policy('sensor_readings', INTERVAL '30 days');


# ─────────────────────────────────────────────
# SPACE BOOKING
# ─────────────────────────────────────────────

class SpaceBooking(Base):
    """Reserva de espacio por inquilino o usuario interno."""
    __tablename__ = "space_bookings"

    id          : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    tenant_id   : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    space_id    : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("spaces.id"), nullable=False)
    booked_by   : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    title       : Mapped[str] = mapped_column(String(300), nullable=False)
    starts_at   : Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at     : Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attendees   : Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amenities   : Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    notes       : Mapped[Optional[str]] = mapped_column(Text)
    status      : Mapped[str] = mapped_column(String(20), nullable=False, default="confirmed")
    created_at  : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    space       : Mapped["Space"] = relationship(back_populates="bookings")

    __table_args__ = (
        Index("idx_bookings_space_time", "space_id", "starts_at", "ends_at"),
    )


# ─────────────────────────────────────────────
# TENANT REQUEST (Portal inquilinos)
# ─────────────────────────────────────────────

class TenantRequest(Base):
    """Solicitud de servicio creada por un inquilino o locatario desde el portal."""
    __tablename__ = "tenant_requests"

    id              : Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    tenant_id       : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    center_id       : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("centers.id"), nullable=False)
    requester_id    : Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    work_order_id   : Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id"))
    category        : Mapped[str] = mapped_column(String(50), nullable=False)
    urgency         : Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    title           : Mapped[str] = mapped_column(String(500), nullable=False)
    description     : Mapped[Optional[str]] = mapped_column(Text)
    location_detail : Mapped[Optional[str]] = mapped_column(String(200))
    access_schedule : Mapped[Optional[str]] = mapped_column(String(100))
    status          : Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    nps_score       : Mapped[Optional[int]] = mapped_column(Integer)
    nps_comment     : Mapped[Optional[str]] = mapped_column(Text)
    created_at      : Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at     : Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
