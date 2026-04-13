-- ============================================================
-- FM Platform — Database Setup Script
-- PostgreSQL 17 + TimescaleDB extension
-- Ejecutar como superuser una vez después de crear la BD
-- ============================================================

-- 1. EXTENSIONES
-- ============================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- Full-text search mejorado

-- 2. ROW LEVEL SECURITY — Función de tenant actual
-- ============================================================
-- El backend ejecuta: SET app.current_tenant = '{tenant_uuid}'
-- antes de cada query. Esta función lo lee.
CREATE OR REPLACE FUNCTION current_tenant_id()
RETURNS UUID AS $$
BEGIN
  RETURN current_setting('app.current_tenant', true)::UUID;
EXCEPTION
  WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

-- 3. TABLA sensor_readings — TimescaleDB Hypertable
-- ============================================================
-- Esta tabla NO está en los modelos SQLAlchemy porque
-- se gestiona directamente con TimescaleDB.
CREATE TABLE IF NOT EXISTS sensor_readings (
  time        TIMESTAMPTZ  NOT NULL,
  sensor_id   UUID         NOT NULL REFERENCES sensors(id),
  tenant_id   UUID         NOT NULL REFERENCES tenants(id),
  value       NUMERIC      NOT NULL,
  unit        TEXT         NOT NULL,
  quality     TEXT         NOT NULL DEFAULT 'good'
);

-- Convertir a hypertable con particionado semanal
SELECT create_hypertable(
  'sensor_readings', 'time',
  chunk_time_interval => INTERVAL '7 days',
  if_not_exists => TRUE
);

-- Compresión automática de datos > 30 días
ALTER TABLE sensor_readings SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'sensor_id, tenant_id',
  timescaledb.compress_orderby = 'time DESC'
);

SELECT add_compression_policy('sensor_readings', INTERVAL '30 days');

-- Política de retención: borrar datos > 2 años (ajustar por plan)
SELECT add_retention_policy('sensor_readings', INTERVAL '2 years');

-- Índice para queries frecuentes de dashboards IoT
CREATE INDEX IF NOT EXISTS idx_sensor_readings_sensor_time
  ON sensor_readings (sensor_id, time DESC);

-- Continuous aggregate: promedios por hora (para dashboards)
CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_hourly_avg
WITH (timescaledb.continuous) AS
  SELECT
    time_bucket('1 hour', time) AS bucket,
    sensor_id,
    tenant_id,
    avg(value)  AS avg_value,
    min(value)  AS min_value,
    max(value)  AS max_value,
    count(*)    AS readings_count
  FROM sensor_readings
  GROUP BY bucket, sensor_id, tenant_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('sensor_hourly_avg',
  start_offset => INTERVAL '2 hours',
  end_offset   => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour'
);

-- 4. ROW LEVEL SECURITY — Políticas por tabla principal
-- ============================================================
-- assets
ALTER TABLE assets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON assets;
CREATE POLICY tenant_isolation ON assets
  USING (tenant_id = current_tenant_id());

-- work_orders
ALTER TABLE work_orders ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON work_orders;
CREATE POLICY tenant_isolation ON work_orders
  USING (tenant_id = current_tenant_id());

-- centers
ALTER TABLE centers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON centers;
CREATE POLICY tenant_isolation ON centers
  USING (tenant_id = current_tenant_id());

-- users
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON users;
CREATE POLICY tenant_isolation ON users
  USING (tenant_id = current_tenant_id());

-- contracts
ALTER TABLE contracts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON contracts;
CREATE POLICY tenant_isolation ON contracts
  USING (tenant_id = current_tenant_id());

-- pm_plans
ALTER TABLE pm_plans ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON pm_plans;
CREATE POLICY tenant_isolation ON pm_plans
  USING (tenant_id = current_tenant_id());

-- sensors
ALTER TABLE sensors ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON sensors;
CREATE POLICY tenant_isolation ON sensors
  USING (tenant_id = current_tenant_id());

-- sensor_readings
ALTER TABLE sensor_readings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON sensor_readings;
CREATE POLICY tenant_isolation ON sensor_readings
  USING (tenant_id = current_tenant_id());

-- 5. ÍNDICES CRÍTICOS DE PERFORMANCE
-- ============================================================

-- Dashboard principal: OTs abiertas por SLA urgencia
CREATE INDEX IF NOT EXISTS idx_wo_dashboard
  ON work_orders (tenant_id, center_id, status, sla_deadline)
  WHERE status NOT IN ('closed', 'cancelled');

-- Historial de OTs por activo (ficha del activo)
CREATE INDEX IF NOT EXISTS idx_wo_asset_history
  ON work_orders (asset_id, created_at DESC);

-- OTs asignadas a técnico (app móvil)
CREATE INDEX IF NOT EXISTS idx_wo_assigned_user
  ON work_orders (assigned_to, status)
  WHERE status NOT IN ('closed', 'cancelled', 'draft');

-- Búsqueda de activos por centro + categoría + estado
CREATE INDEX IF NOT EXISTS idx_assets_center_cat
  ON assets (tenant_id, center_id, category, status)
  WHERE active = true;

-- Árbol de activos (navegación jerárquica)
CREATE INDEX IF NOT EXISTS idx_assets_parent
  ON assets (parent_id, center_id)
  WHERE active = true;

-- Búsqueda por código QR (escaneo en campo)
CREATE INDEX IF NOT EXISTS idx_assets_code
  ON assets (code, center_id)
  WHERE active = true;

-- Full-text search en nombre de activos (pg_trgm)
CREATE INDEX IF NOT EXISTS idx_assets_name_trgm
  ON assets USING GIN (name gin_trgm_ops);

-- PM Plans vencidos (scheduler job cada hora)
CREATE INDEX IF NOT EXISTS idx_pm_plans_due
  ON pm_plans (tenant_id, next_due_at)
  WHERE active = true;

-- Contratos próximos a vencer (alertas 30/60/90 días)
CREATE INDEX IF NOT EXISTS idx_contracts_expiry
  ON contracts (tenant_id, end_date)
  WHERE status = 'active';

-- 6. FUNCIÓN: Calcular siguiente ejecución de PM plan
-- ============================================================
CREATE OR REPLACE FUNCTION calc_next_pm_due(
  trigger_type TEXT,
  frequency    JSONB,
  from_date    TIMESTAMPTZ DEFAULT now()
) RETURNS TIMESTAMPTZ AS $$
DECLARE
  unit    TEXT;
  every   INT;
BEGIN
  IF trigger_type = 'calendar' THEN
    unit  := frequency->>'unit';
    every := (frequency->>'every')::INT;

    RETURN CASE unit
      WHEN 'day'   THEN from_date + (every || ' days')::INTERVAL
      WHEN 'week'  THEN from_date + (every || ' weeks')::INTERVAL
      WHEN 'month' THEN from_date + (every || ' months')::INTERVAL
      WHEN 'year'  THEN from_date + (every || ' years')::INTERVAL
      ELSE from_date + INTERVAL '1 month'
    END;
  END IF;

  -- Para trigger por uso: requiere lógica en el servicio Python
  RETURN from_date + INTERVAL '1 month';
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- 7. FUNCIÓN: Código secuencial de OT por tenant
-- ============================================================
-- En producción usar una tabla de secuencias por tenant
CREATE TABLE IF NOT EXISTS wo_sequences (
  tenant_id UUID NOT NULL PRIMARY KEY REFERENCES tenants(id),
  next_val  BIGINT NOT NULL DEFAULT 1
);

CREATE OR REPLACE FUNCTION next_wo_code(p_tenant_id UUID)
RETURNS TEXT AS $$
DECLARE
  seq_val BIGINT;
  year_2  INT := EXTRACT(YEAR FROM now())::INT % 100;
BEGIN
  INSERT INTO wo_sequences (tenant_id, next_val)
  VALUES (p_tenant_id, 2)
  ON CONFLICT (tenant_id) DO UPDATE
    SET next_val = wo_sequences.next_val + 1
  RETURNING next_val - 1 INTO seq_val;

  RETURN 'OT-' || LPAD(year_2::TEXT, 2, '0') || '-' || LPAD(seq_val::TEXT, 6, '0');
END;
$$ LANGUAGE plpgsql;

-- 8. TRIGGER: updated_at automático
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Aplicar a tablas con updated_at
DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['assets', 'work_orders', 'tenants', 'centers'] LOOP
    EXECUTE format('
      DROP TRIGGER IF EXISTS trg_updated_at ON %I;
      CREATE TRIGGER trg_updated_at
        BEFORE UPDATE ON %I
        FOR EACH ROW EXECUTE FUNCTION update_updated_at();
    ', t, t);
  END LOOP;
END $$;

-- 9. VISTA: Dashboard KPIs por centro (útil para Metabase)
-- ============================================================
CREATE OR REPLACE VIEW v_center_kpis AS
SELECT
  wo.tenant_id,
  wo.center_id,
  c.name AS center_name,
  c.type AS center_type,
  COUNT(*) FILTER (WHERE wo.status NOT IN ('closed','cancelled')) AS open_work_orders,
  COUNT(*) FILTER (WHERE wo.sla_breached = true AND wo.status NOT IN ('closed','cancelled')) AS sla_breached_open,
  ROUND(
    AVG(
      EXTRACT(EPOCH FROM (wo.completed_at - wo.started_at)) / 3600
    ) FILTER (WHERE wo.started_at IS NOT NULL AND wo.completed_at IS NOT NULL),
    2
  ) AS avg_mttr_hours,
  ROUND(
    (COUNT(*) FILTER (WHERE wo.status = 'closed' AND wo.sla_breached = false)::NUMERIC /
     NULLIF(COUNT(*) FILTER (WHERE wo.status = 'closed'), 0)) * 100,
    1
  ) AS sla_compliance_pct,
  SUM(wo.actual_cost) FILTER (WHERE wo.status = 'closed') AS total_cost_eur
FROM work_orders wo
JOIN centers c ON wo.center_id = c.id
GROUP BY wo.tenant_id, wo.center_id, c.name, c.type;

-- 10. DATOS INICIALES: Tenant de demo
-- ============================================================
-- INSERT INTO tenants (id, slug, name, plan)
-- VALUES (gen_random_uuid(), 'demo', 'Demo Organization', 'scale');
-- (Descomentar para setup inicial)

-- ============================================================
-- VERIFICACIÓN FINAL
-- ============================================================
DO $$
BEGIN
  RAISE NOTICE 'FM Platform DB setup complete.';
  RAISE NOTICE 'TimescaleDB: %', (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb');
  RAISE NOTICE 'RLS enabled on: assets, work_orders, centers, users, contracts, pm_plans, sensors, sensor_readings';
  RAISE NOTICE 'Indexes created: dashboard, asset_history, assigned, qr_scan, pm_due, contract_expiry';
END $$;
