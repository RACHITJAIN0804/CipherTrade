"""User profile endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import User
from app.schemas.users import UserPublic, UserRead, UserUpdate
from app.schemas.wallet import (
    WalletChallengeRequest,
    WalletChallengeResponse,
    WalletVerifyRequest,
    WalletVerifyResponse,
)
from app.services.auth_deps import get_current_user
from app.services import user_service, wallet_service
from app.services.wallet_service import ConflictError

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
    if data.wallet_address is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Direct wallet_address update is not permitted. "
                "Please verify wallet ownership cryptographically via /api/v1/auth/wallet/challenge and /api/v1/auth/wallet/verify."
            ),
        )
    updated = await user_service.update_user(db, current_user, data)
    return UserRead.model_validate(updated)


@router.post(
    "/me/wallet/challenge",
    response_model=WalletChallengeResponse,
    summary="Request an EIP-4361 SIWE challenge to link a wallet address",
)
async def request_my_wallet_challenge(
    data: WalletChallengeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WalletChallengeResponse:
    try:
        raw_host = request.headers.get("host") or request.url.netloc or "localhost"
        validated_domain = wallet_service.validate_trusted_domain(raw_host)
        uri = str(request.base_url).rstrip("/")
        challenge = await wallet_service.create_wallet_challenge(
            db=db,
            user=current_user,
            candidate_address=data.wallet_address,
            domain=validated_domain,
            uri=uri,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return challenge


@router.post(
    "/me/wallet/verify",
    response_model=WalletVerifyResponse,
    summary="Submit signed SIWE challenge to link wallet address to user account",
)
async def verify_my_wallet(
    data: WalletVerifyRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WalletVerifyResponse:
    try:
        raw_host = request.headers.get("host") or request.url.netloc or "localhost"
        validated_domain = wallet_service.validate_trusted_domain(raw_host)
        updated_user = await wallet_service.verify_wallet_challenge(
            db=db,
            user=current_user,
            message_text=data.message,
            signature=data.signature,
            expected_domain=validated_domain,
        )

    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return WalletVerifyResponse(
        success=True,
        wallet_address=str(updated_user.wallet_address),
        user=UserRead.model_validate(updated_user),
    )




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

