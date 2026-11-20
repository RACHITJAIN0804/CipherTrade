"""Requirements (buyer listings) endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import RequirementStatus, User, UserRole
from app.schemas.requirements import (
    PaginatedRequirements,
    RequirementCreate,
    RequirementRead,
    RequirementUpdate,
)
from app.services.auth_deps import get_current_user
from app.services import requirement_service

router = APIRouter(prefix="/requirements", tags=["requirements"])


@router.post(
    "",
    response_model=RequirementRead,
    status_code=status.HTTP_201_CREATED,
    summary="Post a new requirement (buyer only)",
)
async def create_requirement(
    data: RequirementCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RequirementRead:
    if current_user.primary_role != UserRole.BUYER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only buyers can post requirements.",
        )
    req = await requirement_service.create_requirement(db, current_user, data)
    return RequirementRead.model_validate(req)


@router.get("", response_model=PaginatedRequirements, summary="List / search requirements")
async def list_requirements(
    q: str = Query("", description="Search in title, description, keywords"),
    category: str = Query("", description="Filter by category"),
    buyer_id: uuid.UUID | None = Query(None, description="Filter by buyer"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PaginatedRequirements:
    items, total = await requirement_service.list_requirements(
        db,
        q=q or None,
        category=category or None,
        buyer_id=buyer_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedRequirements(
        items=[RequirementRead.model_validate(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/buyer/{buyer_id}",
    response_model=PaginatedRequirements,
    summary="View active requirements posted by a particular buyer",
)
async def list_buyer_requirements(
    buyer_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PaginatedRequirements:
    items, total = await requirement_service.list_requirements(
        db,
        buyer_id=buyer_id,
        status=RequirementStatus.ACTIVE,
        page=page,
        page_size=page_size,
    )
    return PaginatedRequirements(
        items=[RequirementRead.model_validate(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{requirement_id}",
    response_model=RequirementRead,
    summary="Get a single requirement",
)
async def get_requirement(
    requirement_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> RequirementRead:
    req = await requirement_service.get_requirement(db, requirement_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found.")
    return RequirementRead.model_validate(req)


@router.patch(
    "/{requirement_id}",
    response_model=RequirementRead,
    summary="Update my requirement",
)
async def update_requirement(
    requirement_id: uuid.UUID,
    data: RequirementUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RequirementRead:
    req = await requirement_service.get_requirement(db, requirement_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found.")
    try:
        updated = await requirement_service.update_requirement(db, req, data, current_user.id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return RequirementRead.model_validate(updated)


@router.delete(
    "/{requirement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Close / cancel my requirement",
)
async def close_requirement(
    requirement_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    req = await requirement_service.get_requirement(db, requirement_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found.")
    try:
        await requirement_service.close_requirement(db, req, current_user.id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return Response(status_code=status.HTTP_204_NO_CONTENT)

