"""
Conversation service: manage messaging thread metadata and enforce block checks.
"""
from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Conversation, User
from app.schemas.conversations import ConversationCreate
from app.services import moderation_service, user_service


async def create_or_get_conversation(
    db: AsyncSession, initiator: User, data: ConversationCreate
) -> Conversation:
    """
    Create a new conversation or return an existing one between two users.

    Enforces:
    - Initiator cannot start a conversation with themselves.
    - Recipient must exist.
    - If recipient has blocked initiator, raises PermissionError.
    - If initiator has blocked recipient, raises ValueError.
    """
    if initiator.id == data.recipient_id:
        raise ValueError("Cannot start a conversation with yourself.")

    recipient = await user_service.get_user_by_id(db, data.recipient_id)
    if recipient is None:
        raise ValueError("Recipient not found.")

    # Check if recipient has blocked initiator
    if await moderation_service.is_blocked(db, blocker_id=recipient.id, blocked_id=initiator.id):
        raise PermissionError("You cannot initiate a conversation with this user.")

    # Check if initiator has blocked recipient
    if await moderation_service.is_blocked(db, blocker_id=initiator.id, blocked_id=recipient.id):
        raise ValueError("You have blocked this user. Unblock to start a conversation.")

    # Check for existing conversation in either direction
    stmt = select(Conversation).where(
        or_(
            (Conversation.initiator_id == initiator.id) & (Conversation.recipient_id == recipient.id),
            (Conversation.initiator_id == recipient.id) & (Conversation.recipient_id == initiator.id),
        )
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    conversation = Conversation(
        initiator_id=initiator.id,
        recipient_id=recipient.id,
        offer_id=data.offer_id,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def get_conversation(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation | None:
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        or_(
            Conversation.initiator_id == user_id,
            Conversation.recipient_id == user_id,
        ),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_conversations(
    db: AsyncSession, user_id: uuid.UUID
) -> list[Conversation]:
    stmt = (
        select(Conversation)
        .where(
            or_(
                Conversation.initiator_id == user_id,
                Conversation.recipient_id == user_id,
            )
        )
        .order_by(Conversation.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()  # type: ignore[return-value]

