"""
Moderation service: user reporting and blocking.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Block, Report, User
from app.schemas.moderation import BlockCreate, ReportCreate


# ─── Reports ──────────────────────────────────────────────────────────────────

async def file_report(
    db: AsyncSession, reporter: User, data: ReportCreate
) -> Report:
    if reporter.id == data.reported_user_id:
        raise ValueError("Cannot report yourself.")

    report = Report(
        reporter_id=reporter.id,
        reported_user_id=data.reported_user_id,
        reason=data.reason,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def list_my_reports(
    db: AsyncSession, reporter_id: uuid.UUID
) -> list[Report]:
    result = await db.execute(
        select(Report)
        .where(Report.reporter_id == reporter_id)
        .order_by(Report.created_at.desc())
    )
    return result.scalars().all()  # type: ignore[return-value]


# ─── Blocks ───────────────────────────────────────────────────────────────────

async def block_user(
    db: AsyncSession, blocker: User, data: BlockCreate
) -> Block:
    if blocker.id == data.blocked_user_id:
        raise ValueError("Cannot block yourself.")

    existing = await db.execute(
        select(Block).where(
            Block.blocker_id == blocker.id,
            Block.blocked_id == data.blocked_user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError("User is already blocked.")

    block = Block(blocker_id=blocker.id, blocked_id=data.blocked_user_id)
    db.add(block)
    await db.commit()
    await db.refresh(block)
    return block


async def unblock_user(
    db: AsyncSession, blocker_id: uuid.UUID, blocked_id: uuid.UUID
) -> None:
    result = await db.execute(
        select(Block).where(
            Block.blocker_id == blocker_id,
            Block.blocked_id == blocked_id,
        )
    )
    block = result.scalar_one_or_none()
    if block is None:
        raise ValueError("Block record not found.")
    await db.delete(block)
    await db.commit()


async def is_blocked(
    db: AsyncSession, blocker_id: uuid.UUID, blocked_id: uuid.UUID
) -> bool:
    result = await db.execute(
        select(Block).where(
            Block.blocker_id == blocker_id,
            Block.blocked_id == blocked_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def list_my_blocks(
    db: AsyncSession, blocker_id: uuid.UUID
) -> list[Block]:
    result = await db.execute(
        select(Block)
        .where(Block.blocker_id == blocker_id)
        .order_by(Block.created_at.desc())
    )
    return result.scalars().all()  # type: ignore[return-value]

