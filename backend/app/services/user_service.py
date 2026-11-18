"""
User service: registration, lookup, profile management.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.models import User
from app.schemas.users import UserCreate, UserUpdate


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(db: AsyncSession, data: UserCreate) -> User:
    """
    Create a new user with a bcrypt-hashed password.

    Raises ValueError if the email is already registered.
    """
    existing = await get_user_by_email(db, data.email)
    if existing:
        raise ValueError("Email already registered.")

    user = User(
        email=data.email,
        display_name=data.display_name,
        hashed_password=hash_password(data.password),
        primary_role=data.primary_role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    """Return User if credentials are valid, else None."""
    user = await get_user_by_email(db, email)
    if user is None:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user


async def update_user(db: AsyncSession, user: User, data: UserUpdate) -> User:
    if data.display_name is not None:
        user.display_name = data.display_name
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

