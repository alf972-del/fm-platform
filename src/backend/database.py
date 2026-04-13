"""
database.py — Conexión PostgreSQL con Row Level Security
=========================================================
- AsyncPG + SQLAlchemy 2.0 async
- RLS automático: SET app.current_tenant en cada conexión
- Pool de conexiones configurado para multi-tenant
- Helpers para transacciones y queries
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text, event
from typing import AsyncGenerator
import uuid

from config import settings


# ── Engine async ──────────────────────────────────────
engine: AsyncEngine = create_async_engine(
    str(settings.DATABASE_URL),
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,         # Verifica conexión antes de usarla
    pool_recycle=3600,           # Recicla conexiones cada hora
    echo=settings.DEBUG,
    connect_args={
        "server_settings": {
            "application_name": "fm-platform-api",
        }
    },
)

# ── Session factory ────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ── Base declarativa ──────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dependency: sesión con RLS ─────────────────────────
async def get_db(tenant_id: str | None = None) -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency que provee una sesión con RLS configurado.
    
    El tenant_id se inyecta desde el middleware de autenticación.
    PostgreSQL RLS usa esta variable para filtrar automáticamente
    todas las queries por tenant.
    
    Uso:
        @router.get("/assets")
        async def list_assets(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        if tenant_id:
            # CRÍTICO: Establecer tenant para RLS antes de cualquier query
            await session.execute(
                text("SET LOCAL app.current_tenant = :tenant_id"),
                {"tenant_id": str(tenant_id)}
            )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db_no_rls() -> AsyncGenerator[AsyncSession, None]:
    """
    Sesión sin RLS — solo para operaciones administrativas internas.
    ⚠️  USAR CON EXTREMO CUIDADO. No exponer en endpoints de usuario.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Helpers ───────────────────────────────────────────
class RLSContext:
    """
    Context manager para ejecutar queries con RLS de un tenant específico.
    
    Útil en workers y tareas asíncronas donde no hay request HTTP.
    
    Uso:
        async with RLSContext(tenant_id="uuid") as db:
            assets = await db.execute(select(Asset))
    """
    
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.session: AsyncSession | None = None
    
    async def __aenter__(self) -> AsyncSession:
        self.session = AsyncSessionLocal()
        await self.session.execute(
            text("SET LOCAL app.current_tenant = :tenant_id"),
            {"tenant_id": self.tenant_id}
        )
        return self.session
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            await self.session.rollback()
        else:
            await self.session.commit()
        await self.session.close()


async def check_db_connection() -> bool:
    """Health check de la base de datos."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def init_rls_policies():
    """
    Inicializa las políticas RLS en PostgreSQL.
    Se ejecuta una vez al arrancar la app.
    Las tablas deben existir previamente (creadas por Alembic).
    """
    rls_tables = [
        "tenants", "centers", "assets", "work_orders", "pm_plans",
        "sensors", "sensor_readings", "users", "contracts", "spaces",
        "service_routes", "checklist_templates", "wo_attachments",
        "wo_time_logs", "wo_checklist_items", "invoices",
    ]
    
    async with engine.begin() as conn:
        for table in rls_tables:
            # Habilitar RLS
            await conn.execute(
                text(f"ALTER TABLE IF EXISTS {table} ENABLE ROW LEVEL SECURITY")
            )
            # Crear política si no existe
            await conn.execute(text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies 
                        WHERE tablename = '{table}' 
                        AND policyname = 'tenant_isolation'
                    ) THEN
                        CREATE POLICY tenant_isolation ON {table}
                        USING (
                            tenant_id = current_setting('app.current_tenant', true)::UUID
                        );
                    END IF;
                END
                $$;
            """))
        
        print(f"✅ RLS configurado en {len(rls_tables)} tablas")
