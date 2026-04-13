"""
app/core/database.py — SQLAlchemy async + PostgreSQL Row Level Security
El patrón multi-tenant: SET app.current_tenant = '<uuid>' antes de cada query.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from app.core.config import settings


# Engine async
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    echo=settings.DEBUG,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db(tenant_id: str | None = None) -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency que provee una sesión DB con RLS configurado.
    Si tenant_id está presente, establece app.current_tenant para RLS.
    """
    async with AsyncSessionLocal() as session:
        if tenant_id:
            # Establecer tenant para RLS en todas las queries de esta sesión
            await session.execute(
                text("SET LOCAL app.current_tenant = :tid"),
                {"tid": str(tenant_id)}
            )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context(tenant_id: str | None = None):
    """Context manager para usar fuera de FastAPI (jobs, scripts)."""
    async with AsyncSessionLocal() as session:
        if tenant_id:
            await session.execute(
                text("SET LOCAL app.current_tenant = :tid"),
                {"tid": str(tenant_id)}
            )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── MIGRATION HELPERS ──────────────────────────────────────────────────────────

RLS_SETUP_SQL = """
-- Función helper para obtener el tenant actual
CREATE OR REPLACE FUNCTION current_tenant_id()
RETURNS UUID AS $$
BEGIN
    RETURN current_setting('app.current_tenant', true)::UUID;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

-- Aplicar RLS a una tabla (llamar por cada tabla con tenant_id)
-- Ejemplo: SELECT apply_tenant_rls('assets');
CREATE OR REPLACE FUNCTION apply_tenant_rls(table_name TEXT)
RETURNS VOID AS $$
BEGIN
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format(
        'CREATE POLICY tenant_isolation ON %I
         USING (tenant_id = current_tenant_id())',
        table_name
    );
END;
$$ LANGUAGE plpgsql;
"""
