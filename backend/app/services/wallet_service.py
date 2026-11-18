"""
Wallet ownership verification service implementing EIP-4361 (Sign-In with Ethereum).

Provides:
- Cryptographic challenge generation with single-use nonce and short expiration.
- Cryptographic signature verification and atomic account wallet linking.
- Comprehensive replay prevention and account conflict protection.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3
import siwe

from app.config import get_settings
from app.models.models import User, WalletChallenge
from app.schemas.wallet import WalletChallengeResponse

logger = logging.getLogger(__name__)


class ConflictError(ValueError):
    """Raised when an operation conflicts with existing resource state (e.g. 409)."""
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_trusted_domain(host_or_domain: str | None) -> str:
    """
    Validate that the incoming request host is within the configured trusted SIWE domains.
    Returns the normalized trusted domain, or raises ValueError if untrusted.
    """
    settings = get_settings()
    if not host_or_domain:
        return settings.siwe_domain

    clean_domain = host_or_domain.split(":")[0].strip().lower()
    trusted = [d.split(":")[0].strip().lower() for d in settings.siwe_trusted_domains]
    if clean_domain not in trusted:
        raise ValueError(
            f"Untrusted host domain '{host_or_domain}'. Domain is not in configured trusted SIWE domains."
        )
    return clean_domain


async def create_wallet_challenge(
    db: AsyncSession,
    user: User,
    candidate_address: str,
    domain: Optional[str] = None,
    uri: Optional[str] = None,
) -> WalletChallengeResponse:
    """
    Generate an EIP-4361 SIWE challenge for the candidate wallet address.
    """
    settings = get_settings()
    checksum_addr = Web3.to_checksum_address(candidate_address)

    # 1. Check if the candidate address is already bound to another user account
    stmt = select(User).where(
        func.lower(User.wallet_address) == checksum_addr.lower(),
        User.id != user.id,
    )
    existing_owner = (await db.execute(stmt)).scalar_one_or_none()
    if existing_owner is not None:
        raise ConflictError("This wallet address is already linked to another account.")

    # 2. Cryptographic nonce generation
    nonce = siwe.generate_nonce()

    # 3. Time parameters
    issued_at = _now()
    expires_at = issued_at + timedelta(minutes=settings.siwe_expire_minutes)

    challenge_domain = validate_trusted_domain(domain)
    challenge_uri = uri or settings.siwe_uri

    # 4. Construct EIP-4361 SIWE message
    siwe_msg = siwe.SiweMessage(
        domain=challenge_domain,
        address=checksum_addr,
        uri=challenge_uri,
        version="1",
        chain_id=settings.chain_id,
        nonce=nonce,
        issued_at=issued_at.isoformat(),
        expiration_time=expires_at.isoformat(),
        statement="Sign in with Ethereum to verify ownership of this wallet for your CipherTrade account.",
    )
    message_text = siwe_msg.prepare_message()

    # 5. Persist challenge record
    challenge = WalletChallenge(
        user_id=user.id,
        wallet_address=checksum_addr,
        nonce=nonce,
        message=message_text,
        chain_id=settings.chain_id,
        expires_at=expires_at,
        created_at=issued_at,
    )
    db.add(challenge)
    await db.commit()
    await db.refresh(challenge)

    return WalletChallengeResponse(
        message=message_text,
        nonce=nonce,
        expires_at=expires_at,
    )


async def verify_wallet_challenge(
    db: AsyncSession,
    user: User,
    message_text: str,
    signature: str,
    expected_domain: Optional[str] = None,
) -> User:
    """
    Verify an EIP-4361 SIWE challenge signature and bind the wallet address to the user profile.
    """
    settings = get_settings()

    # 1. Parse SIWE message
    try:
        siwe_msg = siwe.SiweMessage.from_message(message_text)
    except Exception as exc:
        raise ValueError(f"Invalid SIWE message format: {exc}")

    # 2. Validate Chain ID
    if siwe_msg.chain_id != settings.chain_id:
        raise ValueError(
            f"Chain ID mismatch. Expected {settings.chain_id}, got {siwe_msg.chain_id}."
        )

    # 3. Validate Domain
    clean_msg_domain = siwe_msg.domain.split(":")[0].lower()
    if expected_domain:
        clean_expected = validate_trusted_domain(expected_domain)
        if clean_expected != clean_msg_domain:
            raise ValueError(
                f"Domain mismatch. Expected {expected_domain}, got {siwe_msg.domain}."
            )
    else:
        validate_trusted_domain(clean_msg_domain)

    # 4. Look up challenge by nonce
    stmt_challenge = select(WalletChallenge).where(WalletChallenge.nonce == siwe_msg.nonce)
    challenge = (await db.execute(stmt_challenge)).scalar_one_or_none()
    if challenge is None:
        raise ValueError("Invalid or unknown challenge nonce.")

    # 5. Verify challenge ownership (must belong to authenticated user)
    if challenge.user_id != user.id:
        raise PermissionError("Challenge was not issued to this authenticated user.")

    # 6. Replay prevention: ensure nonce has not already been consumed
    if challenge.used_at is not None:
        raise ValueError("Challenge nonce has already been consumed (replay prevention).")

    # 7. Check expiration (handling offset-aware and offset-naive datetime from SQLite)
    now = _now()
    expires_at = challenge.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        raise ValueError("Challenge has expired. Please request a new challenge.")

    # 8. Check wallet address in message matches the address requested in challenge
    try:
        msg_address = Web3.to_checksum_address(siwe_msg.address)
        challenge_address = Web3.to_checksum_address(challenge.wallet_address)
    except Exception as exc:
        raise ValueError(f"Invalid Ethereum address format in message: {exc}")

    if msg_address != challenge_address:
        raise ValueError("Message address does not match challenge address.")

    # 9. Cryptographic signature verification
    try:
        siwe_msg.verify(
            signature=signature,
            nonce=challenge.nonce,
            domain=siwe_msg.domain,
            timestamp=now,
        )
    except siwe.ExpiredMessage:
        raise ValueError("SIWE message has expired.")
    except siwe.InvalidSignature:
        raise ValueError("Invalid cryptographic signature.")
    except siwe.NonceMismatch:
        raise ValueError("Nonce mismatch during signature verification.")
    except siwe.DomainMismatch:
        raise ValueError("Domain mismatch during signature verification.")
    except Exception as exc:
        raise ValueError(f"Cryptographic signature verification failed: {exc}")

    # 10. Check uniqueness across accounts right before linking
    stmt_conflict = select(User).where(
        func.lower(User.wallet_address) == msg_address.lower(),
        User.id != user.id,
    )
    existing_owner = (await db.execute(stmt_conflict)).scalar_one_or_none()
    if existing_owner is not None:
        raise ConflictError("This wallet address is already linked to another account.")

    # 11. Atomic conditional update to consume nonce (Fix 2)
    stmt_consume = (
        update(WalletChallenge)
        .where(
            WalletChallenge.nonce == siwe_msg.nonce,
            WalletChallenge.used_at.is_(None),
            WalletChallenge.expires_at > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    res_consume = await db.execute(stmt_consume)
    if res_consume.rowcount == 0:
        raise ValueError("Challenge nonce has already been consumed or has expired.")

    challenge.used_at = now

    # 12. Link wallet address to user account within the same transaction
    user.wallet_address = msg_address
    db.add(user)

    # 13. Commit atomically with IntegrityError handling (Fix 2)
    try:
        await db.commit()
        await db.refresh(user)
    except IntegrityError:
        await db.rollback()
        raise ConflictError("This wallet address is already linked to another account.")

    logger.info(
        "Successfully verified wallet %s for user %s (nonce=%s)",
        msg_address,
        user.id,
        challenge.nonce,
    )
    return user


async def cleanup_expired_challenges(
    db: AsyncSession,
    older_than: timedelta = timedelta(hours=1),
) -> int:
    """
    Safely delete expired, unconsumed wallet challenges older than a given duration.
    Active (unexpired) challenges and consumed challenges (retained for audit trail) are preserved.
    Returns the number of deleted records.
    """
    cutoff = _now() - older_than
    stmt = (
        delete(WalletChallenge)
        .where(
            WalletChallenge.expires_at < cutoff,
            WalletChallenge.used_at.is_(None),
        )
    )
    res = await db.execute(stmt)
    await db.commit()
    logger.info("Cleaned up %d expired unconsumed wallet challenges", res.rowcount)
    return res.rowcount
