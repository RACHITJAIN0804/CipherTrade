"""Pydantic schemas for Users and Authentication."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.models import UserRole


# ─── User schemas ─────────────────────────────────────────────────────────────

class UserBase(BaseModel):
    email: EmailStr
    display_name: str = Field(..., min_length=2, max_length=64)
    primary_role: UserRole


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_not_trivial(cls, v: str) -> str:
        if v.isdigit():
            raise ValueError("Password must not be all digits.")
        return v


class UserUpdate(BaseModel):
    display_name: str | None = Field(None, min_length=2, max_length=64)
    wallet_address: str | None = Field(None, min_length=42, max_length=42)


class UserRead(UserBase):
    id: uuid.UUID
    wallet_address: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserPublic(BaseModel):
    """Safe subset of user data that can be shown to other users."""
    id: uuid.UUID
    display_name: str
    primary_role: UserRole
    wallet_address: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Auth schemas ──────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str
    type: str
    exp: int

