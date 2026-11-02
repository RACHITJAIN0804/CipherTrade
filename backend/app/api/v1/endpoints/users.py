"""User profile endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import User
from app.schemas.users import UserPublic, UserRead, UserUpdate
from app.services.auth_deps import get_current_user
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead, summary="Get my profile")
async def get_me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)


@router.patch("/me", response_model=UserRead, summary="Update my profile")
async def update_me(
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserRead:
    updated = await user_service.update_user(db, current_user, data)
    return UserRead.model_validate(updated)


@router.get(
    "/{user_id}",
    response_model=UserPublic,
    summary="Get public profile of any user",
)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> UserPublic:
    user = await user_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return UserPublic.model_validate(user)


@router.get("", response_model=list[UserPublic], summary="Search users by display name and role")
async def search_users(
    q: str = "",
    role: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[UserPublic]:
    from sqlalchemy import select
    from app.models.models import User as UserModel, UserRole

    stmt = select(UserModel).where(
        UserModel.is_active == True,
        UserModel.display_name.ilike(f"%{q}%"),
    )
    if role:
        try:
            role_enum = UserRole(role.lower())
            stmt = stmt.where(UserModel.primary_role == role_enum)
        except ValueError:
            pass
    stmt = stmt.limit(50)
    result = await db.execute(stmt)
    users = result.scalars().all()
    return [UserPublic.model_validate(u) for u in users]

