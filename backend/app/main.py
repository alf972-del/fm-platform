from fastapi import FastAPI, Depends, HTTPException, Header, Query, Path, status
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Any
import uuid
import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text
from pydantic import BaseModel, Field

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://fm_user:fm_pass@db:5432/fm_platform"
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=20, max_overflow=10, pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

app = FastAPI(
    title="FM Platform API",
    version="1.0.0",
    description="Facility Management Platform — REST API (FastAPI)",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session

async def get_current_user(authorization: Optional[str] = Header(None), db: AsyncSession = Depends(get_db)) -> dict:
    return {
        "user_id": "user-dev-001",
        "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
        "email": "dev@fmplatform.io",
        "roles": ["fm_manager"],
        "center_id": "660e8400-e29b-41d4-a716-446655440001",
    }

class HealthCheckResponse(BaseModel):
    status: str
    version: str
    timestamp: str

class WorkOrderCreate(BaseModel):
    center_id: str
    type: str = "corrective"
    title: str = Field(..., min_length=3, max_length=500)
    priority: str = "medium"
    asset_id: Optional[str] = None
    description: Optional[str] = None

class AssetCreate(BaseModel):
    center_id: str
    name: str = Field(..., min_length=2, max_length=300)
    category: str
    criticality: str = "medium"

class PaginatedResponse(BaseModel):
    data: List[Any] = []
    pagination: dict = {}

@app.get("/health", tags=["System"])
async def health_check() -> HealthCheckResponse:
    return HealthCheckResponse(
        status="ok",
        version="1.0.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

@app.get("/")
async def root():
    return {"message": "FM Platform API funcionando! 🚀", "docs": "http://localhost:8000/docs"}

@app.get("/v1/work-orders", tags=["Work Orders"])
async def list_work_orders(
    status_filter: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = Query(None),
    center_id: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse:
    query_str = "SELECT id, code, type, status, priority, title, center_id, sla_deadline, created_at, updated_at FROM work_orders WHERE 1=1"
    params = {}
    
    if center_id:
        query_str += " AND center_id = :center_id"
        params["center_id"] = center_id
    
    if status_filter:
        query_str += " AND UPPER(status) = :status"
        params["status"] = status_filter.upper()
    
    if priority:
        query_str += " AND UPPER(priority) = :priority"
        params["priority"] = priority.upper()
    
    query_str += " ORDER BY created_at DESC LIMIT :limit"
    params["limit"] = limit
    
    try:
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()
        
        work_orders = [
            {
                "id": row[0],
                "code": row[1],
                "type": row[2].lower() if row[2] else None,
                "status": row[3].lower() if row[3] else None,
                "priority": row[4].lower() if row[4] else None,
                "title": row[5],
                "center_id": row[6],
                "sla_deadline": row[7].isoformat() if row[7] else None,
                "created_at": row[8].isoformat() if row[8] else None,
                "updated_at": row[9].isoformat() if row[9] else None,
            }
            for row in rows
        ]

        return PaginatedResponse(
            data=work_orders,
            pagination={"cursor": None, "has_more": False},
        )
    except Exception as e:
        print(f"Error en list_work_orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/work-orders/{work_order_id}", tags=["Work Orders"])
async def get_work_order(
    work_order_id: str = Path(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    query = text("SELECT id, code, type, status, priority, title, description, center_id, asset_id, created_by, sla_deadline, created_at, updated_at FROM work_orders WHERE id = :id")
    result = await db.execute(query, {"id": work_order_id})
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Work order not found")
    
    asset_structured_id = None
    if row[8]:
        asset_query = text("SELECT structured_id FROM assets WHERE id = :id")
        asset_result = await db.execute(asset_query, {"id": row[8]})
        asset_row = asset_result.fetchone()
        if asset_row:
            asset_structured_id = asset_row[0]
    
    return {
        "id": row[0],
        "code": row[1],
        "type": row[2].lower() if row[2] else None,
        "status": row[3].lower() if row[3] else None,
        "priority": row[4].lower() if row[4] else None,
        "title": row[5],
        "description": row[6],
        "center_id": row[7],
        "asset_id": row[8],
        "asset_structured_id": asset_structured_id,
        "created_by": row[9],
        "sla_deadline": row[10].isoformat() if row[10] else None,
        "created_at": row[11].isoformat() if row[11] else None,
        "updated_at": row[12].isoformat() if row[12] else None,
    }

@app.post("/v1/work-orders", status_code=status.HTTP_201_CREATED, tags=["Work Orders"])
async def create_work_order(
    body: WorkOrderCreate,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    now_utc = datetime.now(timezone.utc)
    now_naive = now_utc.replace(tzinfo=None)
    sla_deadline_naive = (now_utc + timedelta(days=3)).replace(tzinfo=None)
    
    work_order_id = str(uuid.uuid4())
    wo_code = f"WO-{now_utc.year}-{str(uuid.uuid4())[:8].upper()}"
    
    insert_query = text("""
        INSERT INTO work_orders 
        (id, tenant_id, center_id, code, type, status, priority, title, description, 
         asset_id, created_by, sla_deadline, created_at, updated_at)
        VALUES 
        (:id, :tenant_id, :center_id, :code, :type, :status, :priority, :title, :description,
         :asset_id, :created_by, :sla_deadline, :created_at, :updated_at)
        RETURNING id
    """)
    
    try:
        await db.execute(
            insert_query,
            {
                "id": work_order_id,
                "tenant_id": current_user["tenant_id"],
                "center_id": body.center_id,
                "code": wo_code,
                "type": body.type.upper(),
                "status": "OPEN",
                "priority": body.priority.upper(),
                "title": body.title,
                "description": body.description,
                "asset_id": body.asset_id,
                "created_by": current_user.get("user_id", "api"),
                "sla_deadline": sla_deadline_naive,
                "created_at": now_naive,
                "updated_at": now_naive,
            }
        )
        await db.commit()
        
        asset_structured_id = None
        if body.asset_id:
            asset_query = text("SELECT structured_id FROM assets WHERE id = :asset_id")
            result = await db.execute(asset_query, {"asset_id": body.asset_id})
            row = result.fetchone()
            if row:
                asset_structured_id = row[0]
        
        return {
            "id": work_order_id,
            "code": wo_code,
            "type": body.type.lower(),
            "status": "open",
            "priority": body.priority.lower(),
            "title": body.title,
            "center_id": body.center_id,
            "asset_id": body.asset_id,
            "asset_structured_id": asset_structured_id,
            "sla_deadline": sla_deadline_naive.isoformat(),
            "created_at": now_naive.isoformat(),
            "updated_at": now_naive.isoformat(),
        }
    except Exception as e:
        await db.rollback()
        print(f"Error creating work order: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/assets", tags=["Assets"])
async def list_assets(
    center_id: str = Query(...),
    category: Optional[str] = Query(None),
    criticality: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse:
    query = "SELECT id, name, code, category, criticality, status, structured_id FROM assets WHERE center_id = :center_id AND active = true"
    params = {"center_id": center_id}
    
    if category:
        query += " AND UPPER(category) = :category"
        params["category"] = category.upper()
    
    if criticality:
        query += " AND UPPER(criticality) = :criticality"
        params["criticality"] = criticality.upper()
    
    query += f" ORDER BY created_at DESC LIMIT {limit}"
    
    try:
        result = await db.execute(text(query), params)
        rows = result.fetchall()
        
        assets = [
            {
                "id": row[0],
                "name": row[1],
                "code": row[2],
                "category": row[3].lower() if row[3] else None,
                "criticality": row[4].lower() if row[4] else None,
                "status": row[5].lower() if row[5] else None,
                "structured_id": row[6],
            }
            for row in rows
        ]
        
        return PaginatedResponse(
            data=assets,
            pagination={"has_more": len(assets) >= limit}
        )
    except Exception as e:
        print(f"Error en list_assets: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/analytics/kpis", tags=["Analytics"])
async def get_kpis(
    center_id: Optional[str] = Query(None),
    period: str = Query("last_30d"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        open_wos_query = text("SELECT COUNT(*) FROM work_orders WHERE status != 'CLOSED' AND center_id = :center_id")
        result = await db.execute(open_wos_query, {"center_id": center_id or current_user.get("center_id")})
        open_wos = result.scalar() or 0
        
        assets_query = text("SELECT COUNT(*) FROM assets WHERE center_id = :center_id AND active = true")
        result = await db.execute(assets_query, {"center_id": center_id or current_user.get("center_id")})
        total_assets = result.scalar() or 0
        
        return {
            "period": period,
            "center_id": center_id,
            "kpis": {
                "open_work_orders": open_wos,
                "total_assets": total_assets,
                "mttr_hours": 4.5,
                "mtbf_days": 28,
                "sla_compliance_pct": 94.2,
                "cost_per_sqm": 12.50,
                "overdue_sla": 1,
            },
        }
    except Exception as e:
        print(f"Error en get_kpis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.on_event("startup")
async def startup_event():
    print("🚀 FM Platform API starting...")
    print(f"   Database: {DATABASE_URL}")
    print("   Docs: http://localhost:8000/docs")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
