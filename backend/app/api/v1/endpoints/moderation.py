"""Report and Block endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import User
from app.schemas.moderation import BlockCreate, BlockRead, ReportCreate, ReportRead
from app.services.auth_deps import get_current_user
from app.services import moderation_service
import uuid

router = APIRouter(prefix="/moderation", tags=["moderation"])


# ─── Reports ──────────────────────────────────────────────────────────────────

@router.post(
    "/reports",
    response_model=ReportRead,
    status_code=status.HTTP_201_CREATED,
    summary="File a report against a user",
)
async def file_report(
    data: ReportCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReportRead:
    try:
        report = await moderation_service.file_report(db, current_user, data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ReportRead.model_validate(report)


@router.get(
    "/reports",
    response_model=list[ReportRead],
    summary="List reports I have filed",
)
async def list_my_reports(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ReportRead]:
    reports = await moderation_service.list_my_reports(db, current_user.id)
    return [ReportRead.model_validate(r) for r in reports]


# ─── Blocks ───────────────────────────────────────────────────────────────────

@router.post(
    "/blocks",
    response_model=BlockRead,
    status_code=status.HTTP_201_CREATED,
    summary="Block a user",
)
async def block_user(
    data: BlockCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BlockRead:
    try:
        block = await moderation_service.block_user(db, current_user, data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return BlockRead.model_validate(block)


@router.delete(
    "/blocks/{blocked_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Unblock a user",
)
async def unblock_user(
    blocked_user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        await moderation_service.unblock_user(db, current_user.id, blocked_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/blocks",
    response_model=list[BlockRead],
    summary="List users I have blocked",
)
async def list_my_blocks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[BlockRead]:
    blocks = await moderation_service.list_my_blocks(db, current_user.id)
    return [BlockRead.model_validate(b) for b in blocks]

