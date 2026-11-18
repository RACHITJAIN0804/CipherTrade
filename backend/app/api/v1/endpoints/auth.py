"""Auth endpoints: register and login."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import create_access_token
from app.models.models import User
from app.schemas.users import LoginRequest, TokenResponse, UserCreate, UserRead
from app.schemas.wallet import (
    WalletChallengeRequest,
    WalletChallengeResponse,
    WalletVerifyRequest,
    WalletVerifyResponse,
)
from app.services.auth_deps import get_current_user
from app.services import user_service, wallet_service
from app.services.wallet_service import ConflictError

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(data: UserCreate, db: AsyncSession = Depends(get_db)) -> UserRead:
    try:
        user = await user_service.create_user(db, data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Obtain a JWT access token",
)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await user_service.authenticate_user(db, data.email, data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(
        subject=str(user.id),
        extra={"role": user.primary_role.value},
    )
    return TokenResponse(access_token=token)


@router.post(
    "/wallet/challenge",
    response_model=WalletChallengeResponse,
    summary="Request an EIP-4361 SIWE challenge to link a wallet address",
)
async def request_wallet_challenge(
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
    "/wallet/verify",
    response_model=WalletVerifyResponse,
    summary="Submit signed SIWE challenge to link wallet address to user account",
)
async def verify_wallet(
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

