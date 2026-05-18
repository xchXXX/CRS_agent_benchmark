"""维度管理接口"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.legacy.models.database import DimFacet, DimValue, Doc, get_db
from app.legacy.services.dimension_service import dimension_service
from app.legacy.utils.auth import TokenData, get_current_user, require_admin


router = APIRouter(prefix="/admin/dimension", tags=["admin-dimension"])


class DimFacetResponse(BaseModel):
    facet_key: str
    facet_name: str
    question: Optional[str]
    priority: int
    db_field: Optional[str]
    parent_facet_key: Optional[str]
    match_mode: str
    specificity: int
    is_active: bool


class DimFacetUpdateRequest(BaseModel):
    facet_name: Optional[str] = None
    question: Optional[str] = None
    priority: Optional[int] = None
    db_field: Optional[str] = None
    parent_facet_key: Optional[str] = None
    match_mode: Optional[str] = None
    specificity: Optional[int] = None
    is_active: Optional[bool] = None


class DimValueResponse(BaseModel):
    id: int
    facet_key: str
    value: str
    match_patterns: Optional[str]
    parent_value_id: Optional[int]
    parent_value: Optional[str] = None
    is_active: bool
    sort_order: int


class DimValueCreateRequest(BaseModel):
    facet_key: str
    value: str
    match_patterns: Optional[str] = None
    parent_value_id: Optional[int] = None
    sort_order: int = 0


class DimValueUpdateRequest(BaseModel):
    value: Optional[str] = None
    match_patterns: Optional[str] = None
    parent_value_id: Optional[int] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


@router.get("/facets", response_model=list[DimFacetResponse])
async def list_facets(
    include_inactive: bool = False,
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user

    query = db.query(DimFacet)
    if not include_inactive:
        query = query.filter(DimFacet.is_active.is_(True))
    facets = query.order_by(DimFacet.priority).all()

    return [
        DimFacetResponse(
            facet_key=facet.facet_key,
            facet_name=facet.facet_name,
            question=facet.question,
            priority=facet.priority or 0,
            db_field=facet.db_field,
            parent_facet_key=facet.parent_facet_key,
            match_mode=facet.match_mode or "dict",
            specificity=facet.specificity or 0,
            is_active=facet.is_active,
        )
        for facet in facets
    ]


@router.get("/facets/{facet_key}", response_model=DimFacetResponse)
async def get_facet(
    facet_key: str,
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user

    facet = db.query(DimFacet).filter_by(facet_key=facet_key).first()
    if not facet:
        raise HTTPException(status_code=404, detail=f"维度 {facet_key} 不存在")

    return DimFacetResponse(
        facet_key=facet.facet_key,
        facet_name=facet.facet_name,
        question=facet.question,
        priority=facet.priority or 0,
        db_field=facet.db_field,
        parent_facet_key=facet.parent_facet_key,
        match_mode=facet.match_mode or "dict",
        specificity=facet.specificity or 0,
        is_active=facet.is_active,
    )


@router.put("/facets/{facet_key}")
async def update_facet(
    facet_key: str,
    request: DimFacetUpdateRequest,
    current_user: TokenData = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del current_user

    facet = db.query(DimFacet).filter_by(facet_key=facet_key).first()
    if not facet:
        raise HTTPException(status_code=404, detail=f"维度 {facet_key} 不存在")

    for field_name, value in request.model_dump(exclude_unset=True).items():
        setattr(facet, field_name, value)

    db.commit()
    return {"message": f"维度 {facet_key} 更新成功"}


@router.get("/values", response_model=list[DimValueResponse])
async def list_values(
    facet_key: Optional[str] = None,
    include_inactive: bool = False,
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user

    query = db.query(DimValue)
    if facet_key:
        query = query.filter(DimValue.facet_key == facet_key)
    if not include_inactive:
        query = query.filter(DimValue.is_active.is_(True))

    values = query.order_by(DimValue.facet_key, DimValue.sort_order.desc()).all()
    id_to_value = {value.id: value.value for value in values}

    return [
        DimValueResponse(
            id=value.id,
            facet_key=value.facet_key,
            value=value.value,
            match_patterns=value.match_patterns,
            parent_value_id=value.parent_value_id,
            parent_value=id_to_value.get(value.parent_value_id) if value.parent_value_id else None,
            is_active=value.is_active,
            sort_order=value.sort_order or 0,
        )
        for value in values
    ]


@router.get("/values/{value_id}", response_model=DimValueResponse)
async def get_value(
    value_id: int,
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user

    value = db.query(DimValue).filter_by(id=value_id).first()
    if not value:
        raise HTTPException(status_code=404, detail=f"维度值 {value_id} 不存在")

    parent_value = None
    if value.parent_value_id:
        parent = db.query(DimValue).filter_by(id=value.parent_value_id).first()
        parent_value = parent.value if parent else None

    return DimValueResponse(
        id=value.id,
        facet_key=value.facet_key,
        value=value.value,
        match_patterns=value.match_patterns,
        parent_value_id=value.parent_value_id,
        parent_value=parent_value,
        is_active=value.is_active,
        sort_order=value.sort_order or 0,
    )


@router.post("/values")
async def create_value(
    request: DimValueCreateRequest,
    current_user: TokenData = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del current_user

    facet = db.query(DimFacet).filter_by(facet_key=request.facet_key).first()
    if not facet:
        raise HTTPException(status_code=400, detail=f"维度 {request.facet_key} 不存在")

    existing = db.query(DimValue).filter_by(facet_key=request.facet_key, value=request.value).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"维度值 {request.value} 已存在")

    if request.parent_value_id:
        parent = db.query(DimValue).filter_by(id=request.parent_value_id).first()
        if not parent:
            raise HTTPException(status_code=400, detail=f"父值 {request.parent_value_id} 不存在")

    value = DimValue(
        facet_key=request.facet_key,
        value=request.value,
        match_patterns=request.match_patterns,
        parent_value_id=request.parent_value_id,
        sort_order=request.sort_order,
    )
    db.add(value)
    db.commit()
    db.refresh(value)

    return {"message": "维度值创建成功", "id": value.id}


@router.put("/values/{value_id}")
async def update_value(
    value_id: int,
    request: DimValueUpdateRequest,
    current_user: TokenData = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del current_user

    value = db.query(DimValue).filter_by(id=value_id).first()
    if not value:
        raise HTTPException(status_code=404, detail=f"维度值 {value_id} 不存在")

    if request.value is not None:
        existing = (
            db.query(DimValue)
            .filter(DimValue.facet_key == value.facet_key, DimValue.value == request.value, DimValue.id != value_id)
            .first()
        )
        if existing:
            raise HTTPException(status_code=400, detail=f"维度值 {request.value} 已存在")

    if request.parent_value_id is not None:
        parent = db.query(DimValue).filter_by(id=request.parent_value_id).first()
        if not parent:
            raise HTTPException(status_code=400, detail=f"父值 {request.parent_value_id} 不存在")

    for field_name, field_value in request.model_dump(exclude_unset=True).items():
        setattr(value, field_name, field_value)

    db.commit()
    return {"message": f"维度值 {value_id} 更新成功"}


@router.delete("/values/{value_id}")
async def delete_value(
    value_id: int,
    hard_delete: bool = False,
    current_user: TokenData = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del current_user

    value = db.query(DimValue).filter_by(id=value_id).first()
    if not value:
        raise HTTPException(status_code=404, detail=f"维度值 {value_id} 不存在")

    if hard_delete:
        db.delete(value)
    else:
        value.is_active = False

    db.commit()
    action = "物理删除" if hard_delete else "软删除"
    return {"message": f"维度值 {value_id} {action}成功"}


@router.post("/refresh")
async def refresh_cache(
    current_user: TokenData = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del current_user
    dimension_service.refresh(db)
    return {"message": "维度缓存刷新成功"}


@router.get("/stats")
async def get_stats(
    current_user: TokenData = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    del current_user

    from sqlalchemy import func

    facet_count = db.query(DimFacet).filter_by(is_active=True).count()
    value_count = db.query(DimValue).filter_by(is_active=True).count()

    value_stats = (
        db.query(DimValue.facet_key, func.count(DimValue.id))
        .filter(DimValue.is_active.is_(True))
        .group_by(DimValue.facet_key)
        .all()
    )

    return {
        "facet_count": facet_count,
        "value_count": value_count,
        "value_by_facet": {key: count for key, count in value_stats},
        "cache_loaded": dimension_service.is_loaded,
    }


@router.post("/sync-from-docs")
async def sync_from_docs(
    facet_key: str,
    dry_run: bool = True,
    current_user: TokenData = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del current_user

    facet = db.query(DimFacet).filter_by(facet_key=facet_key).first()
    if not facet:
        raise HTTPException(status_code=400, detail=f"维度 {facet_key} 不存在")

    db_field = facet.db_field or facet_key
    if not hasattr(Doc, db_field):
        raise HTTPException(status_code=400, detail=f"Doc 表中不存在字段 {db_field}")

    column = getattr(Doc, db_field)
    unique_values = db.query(column).filter(column.isnot(None)).distinct().all()
    normalized_values = [row[0] for row in unique_values if row[0] and isinstance(row[0], str)]

    existing = db.query(DimValue.value).filter_by(facet_key=facet_key).all()
    existing_set = {row[0] for row in existing}
    new_values = [value for value in normalized_values if value not in existing_set]

    if dry_run:
        return {
            "message": "预览模式",
            "facet_key": facet_key,
            "total_in_docs": len(normalized_values),
            "already_exists": len(existing_set),
            "new_values": new_values[:50],
            "new_count": len(new_values),
        }

    for value in new_values:
        db.add(DimValue(facet_key=facet_key, value=value, match_patterns=value))

    db.commit()
    return {"message": f"同步完成，新增 {len(new_values)} 个值", "facet_key": facet_key, "new_count": len(new_values)}
