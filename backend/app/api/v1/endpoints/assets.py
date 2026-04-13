"""
app/api/v1/endpoints/assets.py — Gestión de activos con árbol y QR automático
"""

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload
from typing import Optional, List
import uuid
import csv
import io

from app.core.database import get_db
from app.core.auth import get_current_tenant, require_scope
from app.models.models import Asset, AssetCategory, AssetStatus, AssetCriticality
from app.schemas.schemas import AssetCreate, AssetResponse, AssetImportRow
from app.services.asset_service import AssetService
from app.services.qr_service import QRService
from app.services.search_service import SearchService

router = APIRouter()


@router.get("", response_model=dict)
async def list_assets(
    center_id: uuid.UUID = Query(...),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    criticality: Optional[str] = Query(None),
    floor: Optional[str] = Query(None),
    parent_id: Optional[uuid.UUID] = Query(None),
    include_tree: bool = Query(False),
    q: Optional[str] = Query(None, description="Búsqueda full-text"),
    cursor: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:read")),
):
    """
    Lista activos. Con include_tree=true devuelve árbol completo anidado.
    Con q= usa Meilisearch para búsqueda full-text.
    Optimizado para 600+ activos por centro con índices y lazy loading.
    """
    # Búsqueda full-text via Meilisearch
    if q:
        results = await SearchService.search_assets(
            tenant_id=str(tenant_id),
            center_id=str(center_id),
            query=q,
            limit=limit,
        )
        return {"data": results, "pagination": {"has_more": False}}

    svc = AssetService(db, tenant_id)

    if include_tree:
        # CTE recursiva para árbol completo
        tree = await svc.get_tree(center_id)
        return {"data": tree, "pagination": {"has_more": False}}

    # Lista plana con filtros
    query = (
        select(Asset)
        .where(Asset.tenant_id == tenant_id, Asset.center_id == center_id, Asset.active == True)
    )

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

    if cursor:
        query = query.where(Asset.id > uuid.UUID(cursor))

    query = query.order_by(Asset.floor.asc(), Asset.name.asc()).limit(limit + 1)

    result = await db.execute(query)
    rows = result.scalars().all()

    has_more = len(rows) > limit
    items = rows[:limit]

    return {
        "data": [AssetResponse.model_validate(a).model_dump() for a in items],
        "pagination": {
            "cursor": str(items[-1].id) if has_more and items else None,
            "has_more": has_more,
        }
    }


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(
    asset_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
):
    query = (
        select(Asset)
        .where(Asset.id == asset_id, Asset.tenant_id == tenant_id)
        .options(selectinload(Asset.children), selectinload(Asset.sensors))
    )
    result = await db.execute(query)
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.post("", response_model=AssetResponse, status_code=201)
async def create_asset(
    body: AssetCreate,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:write")),
):
    """Crea activo y genera QR automáticamente."""
    svc = AssetService(db, tenant_id)

    # Validar parent pertenece al mismo centro
    if body.parent_id:
        parent = await db.get(Asset, body.parent_id)
        if not parent or parent.center_id != body.center_id:
            raise HTTPException(status_code=422,
                                detail="parent_id does not belong to the specified center_id")

    asset = await svc.create(body)

    # Generar código único: HVAC-C3-02
    asset.code = await svc.generate_code(asset.category, asset.floor)

    # Generar QR
    asset.qr_code_url = await QRService.generate_and_upload(
        asset_id=str(asset.id),
        tenant_id=str(tenant_id),
        code=asset.code,
    )

    await db.commit()
    await db.refresh(asset)

    # Indexar en Meilisearch
    await SearchService.index_asset(asset)

    return asset


@router.patch("/{asset_id}", response_model=AssetResponse)
async def update_asset(
    asset_id: uuid.UUID,
    body: dict,
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:write")),
):
    asset = await db.get(Asset, asset_id)
    if not asset or asset.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Asset not found")

    editable = {"name", "status", "criticality", "floor", "zone", "room",
                "specs", "warranty_until", "nfc_tag_id"}
    for key, val in body.items():
        if key in editable:
            setattr(asset, key, val)

    await db.commit()
    await db.refresh(asset)
    await SearchService.index_asset(asset)
    return asset


@router.get("/{asset_id}/history")
async def asset_history(
    asset_id: uuid.UUID,
    limit: int = Query(50, le=200),
    cursor: Optional[str] = Query(None),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
):
    """Historial completo de OTs del activo — para calcular MTBF."""
    from app.models.models import WorkOrder
    query = (
        select(WorkOrder)
        .where(WorkOrder.asset_id == asset_id, WorkOrder.tenant_id == tenant_id)
        .order_by(WorkOrder.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    return {"data": result.scalars().all()}


@router.post("/import/csv")
async def import_assets_csv(
    center_id: uuid.UUID = Query(...),
    file: UploadFile = File(...),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
    db: AsyncSession = Depends(lambda: get_db(str(tenant_id))),
    _: None = Depends(require_scope("fm:write")),
):
    """
    Importación masiva CSV — hasta 600 activos en una sola operación.
    Columnas esperadas: name, category, floor, zone, room, criticality,
                        brand, model, serial, purchase_date, warranty_until
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))

    rows = []
    errors = []
    for i, row in enumerate(reader, start=2):  # start=2 porque fila 1 es header
        try:
            rows.append(AssetImportRow(**row))
        except Exception as e:
            errors.append({"row": i, "error": str(e)})

    if errors and len(errors) > len(rows) * 0.1:
        raise HTTPException(
            status_code=422,
            detail={"message": "Too many validation errors", "errors": errors[:20]}
        )

    # Batch insert
    svc = AssetService(db, tenant_id)
    created_count = await svc.bulk_create(center_id, rows)

    return {
        "imported": created_count,
        "errors": errors,
        "message": f"Successfully imported {created_count} assets"
    }
