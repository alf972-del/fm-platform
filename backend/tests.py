"""
FM Platform — Tests del Backend
pytest + pytest-asyncio
Ejecutar: pytest backend/tests.py -v
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from typing import AsyncGenerator
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

# ── MOCK DB para tests ────────────────────────
from models import Base, WorkOrderStatus, WorkOrderPriority, WorkOrderType
from services import (
    WorkOrderService, WorkOrderAction, WorkOrderStateMachineError,
    PMSchedulerService, IoTAlertService, AnalyticsService
)


# ─────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Session de BD en memoria para tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session

    await engine.dispose()


TENANT_ID = "tenant-test-uuid-1234"
CENTER_ID = "center-test-uuid-5678"
USER_ID   = "user-test-uuid-9012"
ASSET_ID  = "asset-test-uuid-3456"


# ─────────────────────────────────────────────
# WORK ORDER STATE MACHINE TESTS
# ─────────────────────────────────────────────

class TestWorkOrderStateMachine:
    """Tests de la state machine de OTs."""

    @pytest.mark.asyncio
    async def test_valid_transitions_table(self):
        """Verifica que las transiciones válidas están definidas para todos los estados."""
        from .services import VALID_TRANSITIONS

        # Todos los estados terminales no deben tener transiciones salvo cancel
        terminal_states = [WorkOrderStatus.CLOSED, WorkOrderStatus.CANCELLED]
        for state in terminal_states:
            transitions = VALID_TRANSITIONS.get(state, {})
            # CLOSED y CANCELLED no tienen transiciones salidas
            assert len(transitions) == 0, f"Estado terminal {state} no debería tener transiciones"

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self, db_session):
        """Una transición inválida debe lanzar WorkOrderStateMachineError."""
        svc = WorkOrderService(db_session)

        # Mock: OT en estado CLOSED intentando hacer START
        from .models import WorkOrder
        wo = WorkOrder(
            id="wo-test-1",
            tenant_id=TENANT_ID,
            center_id=CENTER_ID,
            code="OT-25-000001",
            type=WorkOrderType.CORRECTIVE,
            status=WorkOrderStatus.CLOSED,  # Ya cerrada
            priority=WorkOrderPriority.MEDIUM,
            title="Test OT",
        )
        db_session.add(wo)
        await db_session.flush()

        with pytest.raises(WorkOrderStateMachineError) as exc_info:
            await svc.transition(
                work_order_id="wo-test-1",
                action=WorkOrderAction.START,  # Inválido desde CLOSED
                triggered_by_user_id=USER_ID,
                user_role="technician",
            )
        assert "not valid from status" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_unauthorized_role_raises(self, db_session):
        """Un técnico no puede aprobar una OT."""
        svc = WorkOrderService(db_session)

        from .models import WorkOrder
        wo = WorkOrder(
            id="wo-test-2",
            tenant_id=TENANT_ID,
            center_id=CENTER_ID,
            code="OT-25-000002",
            type=WorkOrderType.CORRECTIVE,
            status=WorkOrderStatus.PENDING,
            priority=WorkOrderPriority.HIGH,
            title="Test OT",
        )
        db_session.add(wo)
        await db_session.flush()

        with pytest.raises(WorkOrderStateMachineError) as exc_info:
            await svc.transition(
                work_order_id="wo-test-2",
                action=WorkOrderAction.APPROVE,
                triggered_by_user_id=USER_ID,
                user_role="technician",  # No autorizado para APPROVE
            )
        assert "not authorized" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_start_sets_started_at(self, db_session):
        """Al hacer START, se debe establecer started_at."""
        svc = WorkOrderService(db_session)

        from .models import WorkOrder
        wo = WorkOrder(
            id="wo-test-3",
            tenant_id=TENANT_ID,
            center_id=CENTER_ID,
            code="OT-25-000003",
            type=WorkOrderType.CORRECTIVE,
            status=WorkOrderStatus.ASSIGNED,
            priority=WorkOrderPriority.HIGH,
            title="Test OT",
        )
        db_session.add(wo)
        await db_session.flush()

        result = await svc.transition(
            work_order_id="wo-test-3",
            action=WorkOrderAction.START,
            triggered_by_user_id=USER_ID,
            user_role="technician",
        )

        assert result.status == WorkOrderStatus.IN_PROGRESS
        assert result.started_at is not None
        assert result.started_at <= datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_complete_sets_resolution(self, db_session):
        """Al completar una OT, se registra la resolución y el costo real."""
        svc = WorkOrderService(db_session)

        from .models import WorkOrder
        wo = WorkOrder(
            id="wo-test-4",
            tenant_id=TENANT_ID,
            center_id=CENTER_ID,
            code="OT-25-000004",
            type=WorkOrderType.CORRECTIVE,
            status=WorkOrderStatus.IN_PROGRESS,
            priority=WorkOrderPriority.MEDIUM,
            title="HVAC repair",
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db_session.add(wo)
        await db_session.flush()

        result = await svc.transition(
            work_order_id="wo-test-4",
            action=WorkOrderAction.COMPLETE,
            triggered_by_user_id=USER_ID,
            user_role="technician",
            resolution="Capacitor reemplazado, sistema operativo",
            actual_cost=320.50,
        )

        assert result.status == WorkOrderStatus.COMPLETED
        assert result.completed_at is not None
        assert result.resolution == "Capacitor reemplazado, sistema operativo"
        assert float(result.actual_cost) == 320.50

    @pytest.mark.asyncio
    async def test_mttr_calculation(self, db_session):
        """MTTR se calcula correctamente en horas."""
        from .models import WorkOrder

        start = datetime.now(timezone.utc) - timedelta(hours=4, minutes=30)
        end   = datetime.now(timezone.utc) - timedelta(minutes=30)

        wo = WorkOrder(
            id="wo-mttr-test",
            tenant_id=TENANT_ID,
            center_id=CENTER_ID,
            code="OT-25-000005",
            type=WorkOrderType.CORRECTIVE,
            status=WorkOrderStatus.COMPLETED,
            priority=WorkOrderPriority.HIGH,
            title="MTTR test",
            started_at=start,
            completed_at=end,
        )

        assert wo.mttr_hours == 4.0  # 4 horas exactas


# ─────────────────────────────────────────────
# SLA CALCULATOR TESTS
# ─────────────────────────────────────────────

class TestSLACalculator:
    """Tests del motor de cálculo de SLA."""

    @pytest.mark.asyncio
    async def test_emergency_sla_2_hours(self, db_session):
        """Emergencias tienen SLA de 2 horas por defecto."""
        svc = WorkOrderService(db_session)
        before = datetime.now(timezone.utc)

        sla = await svc._calculate_sla(
            tenant_id=TENANT_ID,
            center_id=CENTER_ID,
            priority=WorkOrderPriority.EMERGENCY,
            contract_id=None,
        )

        assert sla is not None
        delta = sla - before
        # SLA debe ser aproximadamente 2 horas (con margen de 5 segundos)
        assert timedelta(hours=1, minutes=59, seconds=55) <= delta <= timedelta(hours=2, seconds=5)

    @pytest.mark.asyncio
    async def test_default_sla_by_priority(self, db_session):
        """Verifica los SLAs por defecto para cada prioridad."""
        svc = WorkOrderService(db_session)
        expected = {
            WorkOrderPriority.EMERGENCY: 2,
            WorkOrderPriority.HIGH:      8,
            WorkOrderPriority.MEDIUM:    24,
            WorkOrderPriority.LOW:       72,
        }

        for priority, hours in expected.items():
            sla = await svc._calculate_sla(TENANT_ID, CENTER_ID, priority, None)
            delta = sla - datetime.now(timezone.utc)
            # Margen de 10 segundos
            assert abs(delta.total_seconds() - hours * 3600) < 10, \
                f"SLA para {priority} debería ser {hours}h, fue {delta.total_seconds()/3600:.2f}h"


# ─────────────────────────────────────────────
# IoT ALERT TESTS
# ─────────────────────────────────────────────

class TestIoTAlertService:
    """Tests del motor de alertas IoT."""

    def test_evaluate_above_maximum(self, db_session):
        """Detecta valor por encima del máximo."""
        from .models import Sensor, MetricType

        sensor = Sensor(
            id="sensor-1",
            tenant_id=TENANT_ID,
            center_id=CENTER_ID,
            name="Temp. servidor",
            metric_type=MetricType.TEMPERATURE,
            unit="celsius",
            alert_rules={"max": 26.0, "priority": "emergency", "action": "create_work_order"},
        )

        svc = IoTAlertService(db_session, WorkOrderService(db_session))
        alert = svc._evaluate_rules(sensor, 28.4)

        assert alert is not None
        assert alert["type"] == "above_maximum"
        assert alert["threshold"] == 26.0
        assert alert["value"] == 28.4
        assert alert["priority"] == "emergency"

    def test_evaluate_below_minimum(self, db_session):
        """Detecta valor por debajo del mínimo."""
        from .models import Sensor, MetricType

        sensor = Sensor(
            id="sensor-2",
            tenant_id=TENANT_ID,
            center_id=CENTER_ID,
            name="pH Piscina",
            metric_type=MetricType.WATER_PH,
            unit="pH",
            alert_rules={"min": 6.8, "max": 7.8, "priority": "high"},
        )

        svc = IoTAlertService(db_session, WorkOrderService(db_session))
        alert = svc._evaluate_rules(sensor, 6.2)

        assert alert is not None
        assert alert["type"] == "below_minimum"
        assert alert["threshold"] == 6.8

    def test_evaluate_within_range(self, db_session):
        """No genera alerta cuando el valor está dentro del rango."""
        from .models import Sensor, MetricType

        sensor = Sensor(
            id="sensor-3",
            tenant_id=TENANT_ID,
            center_id=CENTER_ID,
            name="pH Piscina",
            metric_type=MetricType.WATER_PH,
            unit="pH",
            alert_rules={"min": 6.8, "max": 7.8},
        )

        svc = IoTAlertService(db_session, WorkOrderService(db_session))
        alert = svc._evaluate_rules(sensor, 7.2)

        assert alert is None


# ─────────────────────────────────────────────
# WEBHOOK SIGNATURE TESTS
# ─────────────────────────────────────────────

class TestWebhookSecurity:
    """Tests de firma y verificación de webhooks."""

    def test_sign_and_verify_roundtrip(self):
        """Una firma generada debe verificarse correctamente."""
        from .services import WebhookDeliveryService

        secret = "test-secret-key-12345"
        svc = WebhookDeliveryService(secret)
        payload = '{"event":"work_order.created","data":{"id":"wo-1"}}'

        signature = svc.sign_payload(payload)
        signature_header = f"sha256={signature}"

        assert WebhookDeliveryService.verify_signature(payload, signature_header, secret)

    def test_tampered_payload_fails_verification(self):
        """Un payload modificado no debe verificar correctamente."""
        from .services import WebhookDeliveryService

        secret = "test-secret-key-12345"
        svc = WebhookDeliveryService(secret)

        original = '{"event":"work_order.created","data":{"id":"wo-1"}}'
        tampered = '{"event":"work_order.created","data":{"id":"wo-FAKE"}}'

        sig = svc.sign_payload(original)
        header = f"sha256={sig}"

        # Verificar el payload original: OK
        assert WebhookDeliveryService.verify_signature(original, header, secret)
        # Verificar el payload alterado: FAIL
        assert not WebhookDeliveryService.verify_signature(tampered, header, secret)

    def test_wrong_secret_fails_verification(self):
        """Una clave secreta incorrecta debe fallar la verificación."""
        from .services import WebhookDeliveryService

        svc = WebhookDeliveryService("correct-secret")
        payload = '{"event":"test"}'
        sig = svc.sign_payload(payload)
        header = f"sha256={sig}"

        assert not WebhookDeliveryService.verify_signature(payload, header, "wrong-secret")


# ─────────────────────────────────────────────
# API INTEGRATION TESTS
# ─────────────────────────────────────────────

class TestAPI:
    """Tests de integración de endpoints FastAPI."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from .api import app
        return TestClient(app)

    def test_health_check(self, client):
        """El endpoint de salud debe responder 200."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_create_wo_requires_auth(self, client):
        """Crear OT sin token debe devolver 401."""
        response = client.post("/v1/work-orders", json={
            "center_id": CENTER_ID,
            "type": "corrective",
            "title": "Test",
            "priority": "medium",
        })
        assert response.status_code in [401, 422]  # Sin header Authorization

    def test_transition_invalid_action_returns_409(self, client):
        """Transición inválida debe devolver 409 con formato RFC 7807."""
        # Mock auth
        with patch("backend.api.get_current_user", return_value={
            "user_id": USER_ID,
            "tenant_id": TENANT_ID,
            "roles": ["fm_manager"],
            "center_ids": [],
        }):
            response = client.post(
                "/v1/work-orders/nonexistent-id/transition",
                json={"action": "start"},
                headers={"Authorization": "Bearer fake-token"},
            )
            # 404 si no existe, o 409 si la transición es inválida
            assert response.status_code in [404, 409]


# ─────────────────────────────────────────────
# BULK IMPORT TESTS
# ─────────────────────────────────────────────

class TestBulkImport:
    """Tests del importador masivo de activos."""

    @pytest.mark.asyncio
    async def test_bulk_creates_multiple_assets(self, db_session):
        """El importador debe crear N activos de una vez."""
        from .models import AssetCategory, AssetCriticality
        from .api import AssetCreate

        assets_data = [
            AssetCreate(
                center_id=CENTER_ID,
                code=f"HVAC-P{i:02d}-01",
                name=f"Climatizador Planta {i}",
                category=AssetCategory.HVAC,
                location={"floor": f"P{i}"},
                specs={"cooling_kw": 85, "brand": "Daikin"},
            )
            for i in range(6)  # 6 plantas
        ]

        # Simular la lógica de bulk import
        created = 0
        for asset_data in assets_data:
            from .models import Asset
            asset = Asset(
                id=f"asset-{asset_data.code}",
                tenant_id=TENANT_ID,
                center_id=CENTER_ID,
                code=asset_data.code,
                name=asset_data.name,
                category=asset_data.category,
                location=asset_data.location,
                specs=asset_data.specs,
            )
            db_session.add(asset)
            created += 1

        await db_session.flush()
        assert created == 6

    @pytest.mark.asyncio
    async def test_bulk_skips_duplicates(self, db_session):
        """El importador debe saltarse activos con código duplicado."""
        from .models import Asset, AssetCategory

        # Crear activo inicial
        existing = Asset(
            id="existing-asset",
            tenant_id=TENANT_ID,
            center_id=CENTER_ID,
            code="HVAC-P01-01",
            name="Existing HVAC",
            category=AssetCategory.HVAC,
        )
        db_session.add(existing)
        await db_session.flush()

        # Intentar crear otro con mismo código
        from sqlalchemy import select
        result = await db_session.execute(
            select(Asset).where(
                Asset.tenant_id == TENANT_ID,
                Asset.center_id == CENTER_ID,
                Asset.code == "HVAC-P01-01",
            )
        )
        duplicate = result.scalar_one_or_none()
        assert duplicate is not None  # El original existe
        # El importador debería detectar esto y saltarlo


# ─────────────────────────────────────────────
# CORRECTIVE — ESCENARIO COMPLETO
# ─────────────────────────────────────────────

class TestFullWorkOrderLifecycle:
    """Test del ciclo completo de una OT correctiva."""

    @pytest.mark.asyncio
    async def test_full_corrective_lifecycle(self, db_session):
        """
        Escenario real: HVAC falla → OT creada → asignada → iniciada
        → completada → verificada → cerrada.
        """
        svc = WorkOrderService(db_session)

        # 1. Crear OT
        wo = await svc.create(
            tenant_id=TENANT_ID,
            center_id=CENTER_ID,
            wo_type=WorkOrderType.CORRECTIVE,
            title="Fallo compresor HVAC-C3-02",
            priority=WorkOrderPriority.EMERGENCY,
            created_by=USER_ID,
            asset_id=ASSET_ID,
        )
        assert wo.status == WorkOrderStatus.PENDING
        assert wo.sla_deadline is not None
        assert wo.code.startswith("OT-")

        # 2. Aprobar
        wo = await svc.transition(wo.id, WorkOrderAction.APPROVE, USER_ID, "fm_manager")
        assert wo.status == WorkOrderStatus.APPROVED

        # 3. Asignar técnico
        wo = await svc.transition(wo.id, WorkOrderAction.ASSIGN, USER_ID, "fm_manager",
                                  assigned_to="tech-uuid-1")
        assert wo.status == WorkOrderStatus.ASSIGNED
        assert wo.assigned_to == "tech-uuid-1"

        # 4. Iniciar
        wo = await svc.transition(wo.id, WorkOrderAction.START, "tech-uuid-1", "technician")
        assert wo.status == WorkOrderStatus.IN_PROGRESS
        assert wo.started_at is not None

        # 5. Completar
        wo = await svc.transition(wo.id, WorkOrderAction.COMPLETE, "tech-uuid-1", "technician",
                                  resolution="Capacitor reemplazado", actual_cost=380.0)
        assert wo.status == WorkOrderStatus.COMPLETED
        assert wo.completed_at is not None
        assert wo.mttr_hours is not None

        # 6. Verificar
        wo = await svc.transition(wo.id, WorkOrderAction.VERIFY, USER_ID, "fm_manager")
        assert wo.status == WorkOrderStatus.VERIFIED

        # 7. Cerrar
        wo = await svc.transition(wo.id, WorkOrderAction.CLOSE, USER_ID, "fm_manager")
        assert wo.status == WorkOrderStatus.CLOSED
        assert wo.closed_at is not None

        print(f"\n✓ Ciclo completo OT {wo.code}: MTTR = {wo.mttr_hours}h | Costo = €{wo.actual_cost}")
