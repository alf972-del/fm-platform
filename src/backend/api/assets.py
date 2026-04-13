"""
api/assets.py — Router de Activos
===================================
  GET    /assets                    → lista con filtros + árbol
  POST   /assets                    → crear activo + QR
  GET    /assets/{id}               → detalle completo
  PATCH  /assets/{id}               → actualizar
  DELETE /assets/{id}               → desactivar (soft delete)
  GET    /assets/{id}/history       → historial de OTs
  GET    /assets/{id}/qr            → descargar imagen QR
  POST   /assets/bulk-import        → importar desde CSV
  POST   /assets/bulk-assign-pm     → asignar plan preventivo masivo
"""

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import Optional, List
import uuid
import io
import csv
import qrcode
from qrcode.image.svg import SvgPathImage

from database import get_db
from models import Asset, AssetCategory, AssetStatus, Criticality
from schemas.assets import (
    AssetCreate, AssetUpdate, AssetResponse, AssetListResponse,
    AssetTreeNode, BulkImportResponse, BulkAssignPMRequest,
)
from services.assets import AssetService
from services.qr import QRService
from middleware.auth import get_current_user

router = APIRouter()


@router.get("", response_model=AssetListResponse)
async def list_assets(
    center_id: Optional[uuid.UUID] = Query(None, description="Centro (requerido para lista grande)"),
    category: Optional[AssetCategory] = Query(None),
    status: Optional[AssetStatus] = Query(None),
    criticality: Optional[Criticality] = Query(None),
    floor: Optional[str] = Query(None, description="Planta: P0, P1, P2..."),
    parent_id: Optional[uuid.UUID] = Query(None, description="Hijos directos de este activo"),
    include_tree: bool = Query(False, description="Árbol completo anidado (max 6 niveles)"),
    q: Optional[str] = Query(None, description="Búsqueda full-text"),
    cursor: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Lista activos del tenant.
    
    Con include_tree=true devuelve jerarquía anidada completa usando CTE recursiva.
    Sin filtros activos y con 600+ activos, recomienda usar la vista árbol.
    """
    service = AssetService(db, current_user.tenant_id)
    
    if include_tree and center_id:
        # CTE recursiva para árbol completo
        tree = await service.get_tree(center_id=center_id, category=category)
        return AssetListResponse(data=tree, pagination=None)
    
    result = await service.list(
        center_id=center_id,
        category=category,
        status=status,
        criticality=criticality,
        floor=floor,
        parent_id=parent_id,
        q=q,
        cursor=cursor,
        limit=limit,
    )
    
    return result


@router.post("", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
async def create_asset(
    data: AssetCreate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Crea un activo y genera automáticamente su código QR.
    El QR se sube a S3 y la URL se devuelve en la respuesta.
    """
    service = AssetService(db, current_user.tenant_id)
    qr_service = QRService()
    
    # Crear activo
    asset = await service.create(data)
    
    # Generar QR con la URL de acceso móvil
    qr_url = f"https://app.fmplatform.io/assets/{asset.id}/scan"
    qr_s3_key = await qr_service.generate_and_upload(
        asset_id=asset.id,
        tenant_id=current_user.tenant_id,
        url=qr_url,
        label=asset.code,
    )
    
    # Actualizar activo con URL del QR
    asset = await service.update(asset.id, {"qr_code_url": f"https://cdn.fmplatform.io/{qr_s3_key}"})
    
    return asset


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(
    asset_id: uuid.UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Detalle completo del activo con sensores, planes preventivos y estadísticas."""
    service = AssetService(db, current_user.tenant_id)
    asset = await service.get_by_id(asset_id, include_stats=True)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.patch("/{asset_id}", response_model=AssetResponse)
async def update_asset(
    asset_id: uuid.UUID,
    data: AssetUpdate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Actualiza un activo. Los specs JSONB se fusionan (merge), no reemplazan."""
    service = AssetService(db, current_user.tenant_id)
    return await service.update(asset_id, data.model_dump(exclude_unset=True))


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_asset(
    asset_id: uuid.UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft delete — marca el activo como inactivo pero conserva historial."""
    service = AssetService(db, current_user.tenant_id)
    await service.deactivate(asset_id)


@router.get("/{asset_id}/history")
async def get_asset_history(
    asset_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Historial completo de OTs del activo.
    Usado para calcular MTBF y costo histórico de mantenimiento.
    """
    service = AssetService(db, current_user.tenant_id)
    return await service.get_history(asset_id, limit=limit, cursor=cursor)


@router.get("/{asset_id}/qr")
async def download_qr(
    asset_id: uuid.UUID,
    format: str = Query("png", description="png | svg | pdf"),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Descarga el código QR del activo para imprimir y pegar físicamente.
    Incluye el código del activo como texto debajo del QR.
    """
    service = AssetService(db, current_user.tenant_id)
    asset = await service.get_by_id(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    qr_service = QRService()
    qr_bytes, content_type = await qr_service.generate_for_download(
        url=f"https://app.fmplatform.io/assets/{asset_id}/scan",
        label=f"{asset.code}\n{asset.name}",
        format=format,
    )
    
    filename = f"qr-{asset.code}.{format}"
    return StreamingResponse(
        io.BytesIO(qr_bytes),
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/bulk-import", response_model=BulkImportResponse)
async def bulk_import_assets(
    file: UploadFile = File(..., description="CSV con columnas: code,name,category,floor,zone,criticality,specs_json"),
    center_id: uuid.UUID = Query(..., description="Centro destino"),
    dry_run: bool = Query(False, description="Validar sin importar"),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Importa hasta 600 activos desde un archivo CSV.
    
    Columnas requeridas:
      code, name, category, floor, zone, criticality
    
    Columnas opcionales:
      serial_number, purchase_date, warranty_until,
      expected_life_years, purchase_cost, specs_json (JSON string)
    
    En dry_run=true valida el CSV y devuelve errores sin importar.
    
    Proceso:
    1. Parsea el CSV
    2. Valida cada fila (categorías, formatos de fecha, duplicados)
    3. Si no es dry_run, inserta en batch dentro de una transacción
    4. Genera QRs en background (worker)
    5. Devuelve resumen: importados, errores, advertencias
    """
    service = AssetService(db, current_user.tenant_id)
    
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="El archivo debe ser CSV")
    
    content = await file.read()
    text = content.decode("utf-8-sig")  # Soporta BOM de Excel
    reader = csv.DictReader(text.splitlines())
    
    rows = list(reader)
    if len(rows) > 1000:
        raise HTTPException(
            status_code=400,
            detail=f"El CSV tiene {len(rows)} filas. Máximo permitido: 1000 por importación."
        )
    
    result = await service.bulk_import(
        rows=rows,
        center_id=center_id,
        dry_run=dry_run,
        imported_by=current_user.id,
    )
    
    return result


@router.post("/bulk-assign-pm")
async def bulk_assign_pm_plan(
    data: BulkAssignPMRequest,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Asigna un plan de mantenimiento preventivo a múltiples activos.
    
    Selección por filtros (se aplican en AND):
    - center_id    → todos los activos del centro
    - category     → por categoría (ej: todos los HVAC)
    - floor        → por planta
    - asset_ids    → lista explícita de IDs
    
    Útil para: "asignar revisión mensual a todos los 60 climatizadores de golpe"
    """
    from services.pm_plans import PMPlanService
    
    service = AssetService(db, current_user.tenant_id)
    pm_service = PMPlanService(db, current_user.tenant_id)
    
    # Obtener activos que coinciden con los filtros
    matching_assets = await service.list_by_filters(
        center_id=data.center_id,
        category=data.category,
        floor=data.floor,
        asset_ids=data.asset_ids,
    )
    
    if not matching_assets:
        raise HTTPException(status_code=404, detail="No assets match the provided filters")
    
    # Asignar plan preventivo a cada activo
    created_plans = await pm_service.bulk_assign(
        asset_ids=[a.id for a in matching_assets],
        plan_template=data.plan_template,
    )
    
    return {
        "matched_assets": len(matching_assets),
        "plans_created": len(created_plans),
        "asset_ids": [str(a.id) for a in matching_assets],
    }
