"""
SQLAlchemy ORM models for CipherTrade.

Design decisions
────────────────
- UUIDs as primary keys (generated server-side) — avoids enumerable IDs.
- ``created_at`` / ``updated_at`` on every mutable table.
- Soft-delete not used here; records are kept for audit integrity.
- Plaintext message content is NOT stored; only encrypted blobs and
  conversation metadata live in this database.
- Wallet address stored optionally; never used as a credential.

UUID storage
────────────
We use a dialect-aware UUID type:
  • PostgreSQL  → native UUID column (efficient, indexed).
  • SQLite      → VARCHAR(36) — used by the test suite (no asyncpg needed).
SQLAlchemy's ``Uuid(native_uuid=True/False)`` handles this automatically.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Uuid  # SQLAlchemy 2 built-in, dialect-aware
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ─── Enums ───────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    BUYER = "buyer"
    SELLER = "seller"


class OfferStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class RequirementStatus(str, enum.Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class EscrowState(str, enum.Enum):
    """Mirrors CipherTradeEscrow.sol State enum — never invented; copied verbatim."""
    FUNDED = "FUNDED"
    CONFIRMED = "CONFIRMED"
    RELEASED = "RELEASED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"
    DISPUTED = "DISPUTED"
    RESOLVED = "RESOLVED"


class ReportStatus(str, enum.Enum):
    OPEN = "open"
    REVIEWED = "reviewed"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


# ─── Models ──────────────────────────────────────────────────────────────────

class User(Base):
    """
    Represents a registered user.

    A user may act as both buyer and seller across different deals,
    but their *primary_role* determines the default dashboard experience.
    """
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    primary_role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), nullable=False
    )

    # Optional Ethereum wallet address (linked via EIP-191 challenge signature, Phase 5).
    wallet_address: Mapped[str | None] = mapped_column(String(42), unique=True, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    # Relationships
    requirements: Mapped[list["Requirement"]] = relationship(
        back_populates="buyer", foreign_keys="Requirement.buyer_id"
    )
    offers: Mapped[list["Offer"]] = relationship(
        back_populates="seller", foreign_keys="Offer.seller_id"
    )
    escrow_deals_as_buyer: Mapped[list["EscrowDeal"]] = relationship(
        back_populates="buyer", foreign_keys="EscrowDeal.buyer_id"
    )
    escrow_deals_as_seller: Mapped[list["EscrowDeal"]] = relationship(
        back_populates="seller", foreign_keys="EscrowDeal.seller_id"
    )
    conversations_initiated: Mapped[list["Conversation"]] = relationship(
        back_populates="initiator", foreign_keys="Conversation.initiator_id"
    )
    conversations_received: Mapped[list["Conversation"]] = relationship(
        back_populates="recipient", foreign_keys="Conversation.recipient_id"
    )
    reports_filed: Mapped[list["Report"]] = relationship(
        back_populates="reporter", foreign_keys="Report.reporter_id"
    )
    blocks_given: Mapped[list["Block"]] = relationship(
        back_populates="blocker", foreign_keys="Block.blocker_id"
    )
    blocks_received: Mapped[list["Block"]] = relationship(
        back_populates="blocked", foreign_keys="Block.blocked_id"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!s:.8} email={self.email!r}>"


class Requirement(Base):
    """
    A buyer's posted requirement / listing.

    Buyers describe what they need; sellers browse these and submit offers.
    """
    __tablename__ = "requirements"
    __table_args__ = (
        Index("ix_requirements_status_created", "status", "created_at"),
        Index("ix_requirements_buyer_status", "buyer_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)  # CSV for simple search
    budget_min: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    budget_max: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    status: Mapped[RequirementStatus] = mapped_column(
        Enum(RequirementStatus, name="requirement_status"),
        default=RequirementStatus.ACTIVE,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    buyer: Mapped["User"] = relationship(back_populates="requirements", foreign_keys=[buyer_id])
    offers: Mapped[list["Offer"]] = relationship(back_populates="requirement")


class Offer(Base):
    """
    A seller's offer in response to a buyer's requirement.
    """
    __tablename__ = "offers"
    __table_args__ = (
        Index("ix_offers_requirement_status", "requirement_id", "status"),
        Index("ix_offers_seller_status", "seller_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    price_eth: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    # Unix timestamp the seller commits to deliver by (mirrors contract deadline)
    delivery_deadline: Mapped[int] = mapped_column(Integer, nullable=False)
    terms: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[OfferStatus] = mapped_column(
        Enum(OfferStatus, name="offer_status"),
        default=OfferStatus.PENDING,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    requirement: Mapped["Requirement"] = relationship(back_populates="offers")
    seller: Mapped["User"] = relationship(back_populates="offers", foreign_keys=[seller_id])
    escrow_deal: Mapped["EscrowDeal | None"] = relationship(
        back_populates="offer", uselist=False
    )


class EscrowDeal(Base):
    """
    Off-chain mirror of an on-chain CipherTradeEscrow deal.

    The authoritative state lives on-chain. This table caches the last
    verified state for fast API responses and links it to the relevant
    off-chain Offer.

    IMPORTANT: ``on_chain_state`` is only updated after the backend has
    independently verified the transaction receipt and emitted event from
    the Ethereum node. It is NEVER updated merely because the frontend
    claims a transaction was sent.
    """
    __tablename__ = "escrow_deals"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # On-chain identifiers — set once the tx is verified
    chain_deal_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    fund_tx_hash: Mapped[str | None] = mapped_column(String(66), unique=True, nullable=True)

    # Amounts
    amount_eth: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)

    # Deadline (Unix timestamp) submitted to the contract
    deadline_ts: Mapped[int] = mapped_column(Integer, nullable=False)

    # agreement_hash = keccak256(abi.encode(offer_id, price, deadline, ...))
    # Computed by the backend and verified against the on-chain event.
    agreement_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)

    # Cached on-chain state (only updated after verification — see note above)
    on_chain_state: Mapped[EscrowState | None] = mapped_column(
        Enum(EscrowState, name="escrow_state"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    offer: Mapped["Offer"] = relationship(back_populates="escrow_deal")
    buyer: Mapped["User"] = relationship(back_populates="escrow_deals_as_buyer", foreign_keys=[buyer_id])
    seller: Mapped["User"] = relationship(back_populates="escrow_deals_as_seller", foreign_keys=[seller_id])


class Conversation(Base):
    """
    Metadata for a private messaging thread between two users.

    Actual message content is NOT stored here. When E2EE messaging is
    implemented (Phase 4), encrypted blobs will be stored in a separate
    table. This table holds only the relationship metadata needed to
    enforce access control and blocking.
    """
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("initiator_id", "recipient_id", name="uq_conversation_pair"),
        Index("ix_conversations_initiator", "initiator_id"),
        Index("ix_conversations_recipient", "recipient_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    initiator_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Offer that started this conversation (optional link)
    offer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    initiator: Mapped["User"] = relationship(
        back_populates="conversations_initiated", foreign_keys=[initiator_id]
    )
    recipient: Mapped["User"] = relationship(
        back_populates="conversations_received", foreign_keys=[recipient_id]
    )


class Report(Base):
    """
    A moderation report filed by one user against another.

    Access: only the reporter and moderators (future role) may read this.
    """
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_reported_user", "reported_user_id"),
        Index("ix_reports_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    reporter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reported_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status"),
        default=ReportStatus.OPEN,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    reporter: Mapped["User"] = relationship(
        back_populates="reports_filed", foreign_keys=[reporter_id]
    )
    reported_user: Mapped["User"] = relationship(foreign_keys=[reported_user_id])


class Block(Base):
    """
    Records that *blocker* has blocked *blocked*.

    Enforced by checking this table before creating new conversations.
    Existing escrow deals and conversations are not deleted.
    The blocked user is NOT notified.
    """
    __tablename__ = "blocks"
    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_block_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    blocker_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blocked_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    blocker: Mapped["User"] = relationship(
        back_populates="blocks_given", foreign_keys=[blocker_id]
    )
    blocked: Mapped["User"] = relationship(
        back_populates="blocks_received", foreign_keys=[blocked_id]
    )


class WalletChallenge(Base):
    """
    Cryptographic challenge for wallet ownership verification (EIP-4361 SIWE).
    Tied to an authenticated user, single-use, with strict expiration.
    """
    __tablename__ = "wallet_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    nonce: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])


class AgreementConsentChallenge(Base):
    """
    Cryptographic challenge for agreement consent (EIP-712 typed-data).
    Tied to an authenticated user and specific accepted offer/agreement_hash, single-use, with strict expiration.
    """
    __tablename__ = "agreement_consent_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("escrow_deals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    party_role: Mapped[str] = mapped_column(String(16), nullable=False)
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    agreement_hash: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    deadline: Mapped[int] = mapped_column(Integer, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    offer: Mapped["Offer"] = relationship("Offer", foreign_keys=[offer_id])
    deal: Mapped["EscrowDeal | None"] = relationship("EscrowDeal", foreign_keys=[deal_id])


class AgreementConsent(Base):
    """
    Verified cryptographic consent record for an agreement (EIP-712 signature).
    Enforces that buyer and seller independently sign the exact same canonical agreement_hash.
    """
    __tablename__ = "agreement_consents"
    __table_args__ = (
        UniqueConstraint("offer_id", "party_role", name="uq_offer_party_consent"),
        Index("ix_agreement_consents_offer", "offer_id"),
        Index("ix_agreement_consents_deal", "deal_id"),
        Index("ix_agreement_consents_hash", "agreement_hash"),
        Index("ix_agreement_consents_nonce", "nonce", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=_uuid
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), nullable=False
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("escrow_deals.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    party_role: Mapped[str] = mapped_column(String(16), nullable=False)  # "buyer" or "seller"
    signer_wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    agreement_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(132), nullable=False)
    deadline: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    offer: Mapped["Offer"] = relationship("Offer", foreign_keys=[offer_id])
    deal: Mapped["EscrowDeal | None"] = relationship("EscrowDeal", foreign_keys=[deal_id])
