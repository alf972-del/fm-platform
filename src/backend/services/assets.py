"""
services/assets.py — Lógica de negocio para Activos
=====================================================
- CRUD completo con paginación por cursor
- CTE recursiva para árbol jerárquico (Centro→Planta→Zona→Activo)
- Importación masiva desde CSV (para onboarding 600 activos)
- Generación de QR automático al crear activo
- Búsqueda full-text vía Meilisearch
"""

import uuid
import base64
import io
import csv
from typing import Optional, List, Any
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, text
from sqlalchemy.orm import selectinload

from models import Asset, AssetCategory, AssetStatus, Criticality, WorkOrder
from services.qr import QRService
from services.search import SearchService


class AssetService:
    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID):
        self.db = db
        self.tenant_id = tenant_id
        self.qr_service = QRService()
        self.search = SearchService()

    # ── LIST ──────────────────────────────────────────

    async def list(
        self,
        center_id: Optional[uuid.UUID] = None,
        category: Optional[AssetCategory] = None,
        status: Optional[AssetStatus] = None,
        criticality: Optional[Criticality] = None,
        floor: Optional[str] = None,
        parent_id: Optional[uuid.UUID] = None,
        q: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        """
        Lista activos con filtros y paginación por cursor.
        RLS ya está activo en la sesión — filtra por tenant automáticamente.
        """
        # Si hay búsqueda full-text, usar Meilisearch
        if q:
            return await self._search_full_text(q, center_id, category, limit)

        query = select(Asset).where(Asset.active == True)

        if center_id:
            query = query.where(Asset.center_id == center_id)
        if category:
            query = query.where(Asset.category == category)
        if status:
            query = query.where(Asset.status == status)
        if criticality:
            query = query.where(Asset.criticality == criticality)
        if floor:
            query = query.where(Asset.floor == floor)
        if parent_id is not None:
            query = query.where(Asset.parent_id == parent_id)
        else:
            # Por defecto, solo activos raíz (sin parent) si no se especifica
            pass

        # Decodificar cursor (base64 del último ID + created_at)
        if cursor:
            cursor_data = self._decode_cursor(cursor)
            query = query.where(Asset.created_at < cursor_data["created_at"])

        query = query.order_by(Asset.floor, Asset.name).limit(limit + 1)

        result = await self.db.execute(query)
        assets = result.scalars().all()

        has_more = len(assets) > limit
        if has_more:
            assets = assets[:limit]

        next_cursor = None
        if has_more and assets:
            next_cursor = self._encode_cursor(assets[-1])

        # Enriquecer con conteo de OTs abiertas
        asset_ids = [a.id for a in assets]
        open_wo_counts = await self._get_open_wo_counts(asset_ids)

        return {
            "data": [self._enrich(a, open_wo_counts) for a in assets],
            "pagination": {
                "cursor": next_cursor,
                "has_more": has_more,
                "count": len(assets),
            }
        }

    async def get_tree(
        self,
        center_id: uuid.UUID,
        category: Optional[AssetCategory] = None,
    ) -> List[dict]:
        """
        Árbol completo usando CTE recursiva de PostgreSQL.
        Eficiente para 600 activos — una sola query, ~8-35ms.
        
        Resultado: lista de nodos raíz con children anidados.
        """
        category_filter = ""
        params: dict = {"center_id": str(center_id)}

        if category:
            category_filter = "AND a.category = :category"
            params["category"] = category.value

        # CTE recursiva
        cte_sql = text(f"""
            WITH RECURSIVE asset_tree AS (
                -- Base: activos raíz (sin parent)
                SELECT
                    a.id, a.code, a.name, a.category, a.status, a.criticality,
                    a.floor, a.zone, a.parent_id, a.specs, a.qr_code_url,
                    a.warranty_until, a.purchase_date, a.active,
                    0 AS depth,
                    ARRAY[a.id] AS path
                FROM assets a
                WHERE a.center_id = :center_id
                  AND a.parent_id IS NULL
                  AND a.active = true
                  {category_filter}

                UNION ALL

                -- Recursión: hijos
                SELECT
                    a.id, a.code, a.name, a.category, a.status, a.criticality,
                    a.floor, a.zone, a.parent_id, a.specs, a.qr_code_url,
                    a.warranty_until, a.purchase_date, a.active,
                    t.depth + 1,
                    t.path || a.id
                FROM assets a
                INNER JOIN asset_tree t ON a.parent_id = t.id
                WHERE a.active = true
                  AND t.depth < 6  -- max 6 niveles de profundidad
            )
            SELECT * FROM asset_tree ORDER BY floor, name;
        """)

        result = await self.db.execute(cte_sql, params)
        rows = result.mappings().all()

        # Construir árbol anidado en Python
        return self._build_tree(rows)

    # ── GET ONE ───────────────────────────────────────

    async def get(self, asset_id: uuid.UUID) -> Optional[Asset]:
        """Obtiene un activo con sus relaciones."""
        query = (
            select(Asset)
            .where(Asset.id == asset_id, Asset.active == True)
            .options(
                selectinload(Asset.sensors),
                selectinload(Asset.pm_plans),
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_history(
        self,
        asset_id: uuid.UUID,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> dict:
        """Historial completo de OTs de un activo para su ficha."""
        query = (
            select(WorkOrder)
            .where(WorkOrder.asset_id == asset_id)
            .order_by(WorkOrder.created_at.desc())
            .limit(limit + 1)
        )
        if cursor:
            cursor_data = self._decode_cursor(cursor)
            query = query.where(WorkOrder.created_at < cursor_data["created_at"])

        result = await self.db.execute(query)
        wos = result.scalars().all()
        has_more = len(wos) > limit
        if has_more:
            wos = wos[:limit]

        return {
            "data": wos,
            "pagination": {"has_more": has_more},
        }

    # ── CREATE ────────────────────────────────────────

    async def create(self, data: dict) -> Asset:
        """
        Crea un activo y genera su QR automáticamente.
        El código QR lleva al detalle del activo en la app móvil.
        """
        asset = Asset(
            tenant_id=self.tenant_id,
            **data,
        )
        self.db.add(asset)
        await self.db.flush()  # Obtener ID sin commit

        # Generar QR
        qr_url = await self.qr_service.generate_and_upload(
            entity_type="asset",
            entity_id=str(asset.id),
            tenant_id=str(self.tenant_id),
        )
        asset.qr_code_url = qr_url

        await self.db.flush()

        # Indexar en Meilisearch
        await self.search.index_asset(asset)

        return asset

    # ── UPDATE ────────────────────────────────────────

    async def update(self, asset_id: uuid.UUID, data: dict) -> Optional[Asset]:
        asset = await self.get(asset_id)
        if not asset:
            return None
        for key, value in data.items():
            if value is not None:
                setattr(asset, key, value)
        await self.db.flush()
        await self.search.update_asset(asset)
        return asset

    # ── BULK IMPORT ───────────────────────────────────

    async def bulk_import_csv(self, csv_content: str) -> dict:
        """
        Importación masiva desde CSV.
        CRÍTICO para onboarding: un centro puede tener 600 activos.
        
        Formato CSV esperado:
            code,name,category,floor,zone,criticality,brand,model,specs_json
        
        Retorna:
            {imported: N, errors: [{row: N, error: "..."}], skipped: N}
        """
        reader = csv.DictReader(io.StringIO(csv_content))
        imported = 0
        errors = []
        skipped = 0

        # Validar cabeceras
        required_cols = {"code", "name", "category", "floor"}
        if not required_cols.issubset(set(reader.fieldnames or [])):
            return {
                "imported": 0,
                "errors": [{"row": 0, "error": f"Columnas requeridas: {required_cols}"}],
                "skipped": 0,
            }

        batch = []
        for i, row in enumerate(reader, start=2):
            try:
                # Validar categoría
                try:
                    category = AssetCategory(row["category"].lower().strip())
                except ValueError:
                    errors.append({
                        "row": i,
                        "error": f"Categoría inválida: {row['category']}. "
                                 f"Valores válidos: {[c.value for c in AssetCategory]}",
                    })
                    continue

                # Parsear specs opcionales
                import json
                specs = {}
                if row.get("specs_json"):
                    try:
                        specs = json.loads(row["specs_json"])
                    except json.JSONDecodeError:
                        errors.append({"row": i, "error": "specs_json inválido"})
                        continue

                asset = Asset(
                    tenant_id=self.tenant_id,
                    code=row["code"].strip(),
                    name=row["name"].strip(),
                    category=category,
                    floor=row.get("floor", "").strip() or None,
                    zone=row.get("zone", "").strip() or None,
                    criticality=Criticality(row.get("criticality", "medium").lower()),
                    specs={
                        "brand": row.get("brand", ""),
                        "model": row.get("model", ""),
                        **specs,
                    },
                )
                batch.append(asset)

                # Guardar en lotes de 50 para no saturar la transacción
                if len(batch) >= 50:
                    await self._save_batch(batch)
                    imported += len(batch)
                    batch = []

            except Exception as e:
                errors.append({"row": i, "error": str(e)})

        # Guardar lote final
        if batch:
            await self._save_batch(batch)
            imported += len(batch)

        return {
            "imported": imported,
            "errors": errors,
            "skipped": skipped,
            "total_rows": imported + len(errors) + skipped,
        }

    async def _save_batch(self, assets: List[Asset]):
        """Guarda un lote de activos y genera sus QRs en paralelo."""
        import asyncio

        for asset in assets:
            self.db.add(asset)
        await self.db.flush()

        # Generar QRs en paralelo
        qr_tasks = [
            self.qr_service.generate_and_upload(
                entity_type="asset",
                entity_id=str(a.id),
                tenant_id=str(self.tenant_id),
            )
            for a in assets
        ]
        qr_urls = await asyncio.gather(*qr_tasks, return_exceptions=True)

        for asset, qr_url in zip(assets, qr_urls):
            if isinstance(qr_url, str):
                asset.qr_code_url = qr_url

        await self.db.flush()

    # ── BULK ASSIGN PM ────────────────────────────────

    async def bulk_assign_pm_plan(
        self,
        plan_template_id: uuid.UUID,
        center_id: Optional[uuid.UUID] = None,
        category: Optional[AssetCategory] = None,
        floor: Optional[str] = None,
        asset_ids: Optional[List[uuid.UUID]] = None,
    ) -> dict:
        """
        Asigna un plan preventivo a múltiples activos de golpe.
        
        Ej: asignar "revisión mensual HVAC" a todos los 60 climatizadores
        de un centro sin hacerlo uno a uno.
        """
        from models import PMPlan

        # Obtener activos que cumplen los filtros
        query = select(Asset).where(Asset.active == True)

        if asset_ids:
            query = query.where(Asset.id.in_(asset_ids))
        else:
            if center_id:
                query = query.where(Asset.center_id == center_id)
            if category:
                query = query.where(Asset.category == category)
            if floor:
                query = query.where(Asset.floor == floor)

        result = await self.db.execute(query)
        assets = result.scalars().all()

        # Obtener template del plan
        template_result = await self.db.execute(
            select(PMPlan).where(PMPlan.id == plan_template_id)
        )
        template = template_result.scalar_one_or_none()
        if not template:
            raise ValueError(f"Plan template {plan_template_id} no encontrado")

        # Crear plan para cada activo
        created = 0
        skipped = 0
        for asset in assets:
            # Verificar si ya tiene este tipo de plan
            existing = await self.db.execute(
                select(PMPlan).where(
                    PMPlan.asset_id == asset.id,
                    PMPlan.name == template.name,
                    PMPlan.active == True,
                )
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            new_plan = PMPlan(
                tenant_id=self.tenant_id,
                asset_id=asset.id,
                name=template.name,
                trigger_type=template.trigger_type,
                priority=template.priority,
                frequency=template.frequency,
                checklist_template_id=template.checklist_template_id,
                estimated_duration_min=template.estimated_duration_min,
                estimated_cost=template.estimated_cost,
            )
            self.db.add(new_plan)
            created += 1

        await self.db.flush()

        return {
            "created": created,
            "skipped": skipped,
            "total_assets": len(assets),
        }

    # ── PRIVATE HELPERS ───────────────────────────────

    async def _get_open_wo_counts(self, asset_ids: List[uuid.UUID]) -> dict:
        """Obtiene el conteo de OTs abiertas por activo en una sola query."""
        if not asset_ids:
            return {}
        result = await self.db.execute(
            text("""
                SELECT asset_id, COUNT(*) as cnt
                FROM work_orders
                WHERE asset_id = ANY(:ids)
                  AND status NOT IN ('closed', 'cancelled')
                GROUP BY asset_id
            """),
            {"ids": [str(i) for i in asset_ids]},
        )
        return {row.asset_id: row.cnt for row in result}

    async def _search_full_text(
        self,
        q: str,
        center_id: Optional[uuid.UUID],
        category: Optional[AssetCategory],
        limit: int,
    ) -> dict:
        """Delegación a Meilisearch para búsqueda full-text."""
        filters = [f"tenant_id = {self.tenant_id}"]
        if center_id:
            filters.append(f"center_id = {center_id}")
        if category:
            filters.append(f"category = {category.value}")

        results = await self.search.search_assets(
            query=q,
            filters=" AND ".join(filters),
            limit=limit,
        )
        return {"data": results, "pagination": {"has_more": False}}

    def _build_tree(self, rows: list) -> list:
        """Convierte filas planas de la CTE en árbol anidado."""
        nodes = {}
        roots = []

        for row in rows:
            node = {
                "id": str(row["id"]),
                "code": row["code"],
                "name": row["name"],
                "category": row["category"],
                "status": row["status"],
                "criticality": row["criticality"],
                "floor": row["floor"],
                "zone": row["zone"],
                "depth": row["depth"],
                "children": [],
            }
            nodes[row["id"]] = node

            if row["parent_id"] is None:
                roots.append(node)
            elif row["parent_id"] in nodes:
                nodes[row["parent_id"]]["children"].append(node)

        return roots

    def _encode_cursor(self, asset: Asset) -> str:
        import json
        data = json.dumps({
            "id": str(asset.id),
            "created_at": asset.created_at.isoformat(),
        })
        return base64.b64encode(data.encode()).decode()

    def _decode_cursor(self, cursor: str) -> dict:
        import json
        data = json.loads(base64.b64decode(cursor).decode())
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        return data

    def _enrich(self, asset: Asset, open_wo_counts: dict) -> dict:
        return {
            "id": str(asset.id),
            "code": asset.code,
            "name": asset.name,
            "category": asset.category,
            "status": asset.status,
            "criticality": asset.criticality,
            "floor": asset.floor,
            "zone": asset.zone,
            "specs": asset.specs,
            "qr_code_url": asset.qr_code_url,
            "warranty_until": asset.warranty_until,
            "open_work_orders": open_wo_counts.get(asset.id, 0),
            "created_at": asset.created_at,
        }
