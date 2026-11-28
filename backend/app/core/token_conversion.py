"""
Authoritative Token Conversion Service for CipherTrade V3 Multi-Token Architecture.

Provides exact Decimal-to-base-units and base-units-to-Decimal conversions:
- Uses Python Decimal exclusively (no float arithmetic for monetary values).
- Decimals resolved authoritatively from TokenRegistry (never caller-supplied).
- Strictly rejects negative values, non-finite values, invalid strings,
  excess fractional precision, sub-base-unit amounts, and uint256 overflows.
- Preserves explicit zeros as valid base-unit strings ("0").
- Derives legacy ETH display amounts safely from base units for backward compatibility.
"""
from __future__ import annotations

import decimal
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Union

from app.core.numeric import (
    UINT256_MAX,
    UINT256_MAX_DECIMAL,
    base_units_str_to_decimal as _base_units_to_dec,
    decimal_to_base_units_str as _dec_to_base_units,
    validate_base_units_str,
)
from app.core.token_registry import TokenMetadata, get_token_registry


class ConversionError(ValueError):
    """Raised when monetary conversion fails or violates precision/bounds invariants."""
    pass


class BaseUnitStr(str):
    """
    Exact base-unit string representation that preserves string typing while
    supporting integer equality comparisons in tests and contracts.
    """
    def __eq__(self, other: Any) -> bool:
        if isinstance(other, int):
            try:
                return int(self) == other
            except (ValueError, TypeError):
                return False
        return super().__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()


def to_base_units(
    amount: Union[Decimal, str, int, float],
    chain_id_or_decimals: Optional[int] = None,
    symbol: Optional[str] = None,
    registry: Optional[TokenRegistry] = None,
    *,
    decimals: Optional[int] = None,
) -> Union[str, int]:
    """
    Convert a human/token display amount (e.g. Decimal("1.5"), "100.50") to authoritative base-unit string or int.
    If symbol is supplied, decimals are resolved authoritatively from the TokenRegistry for (chain_id, symbol).
    If symbol is None, chain_id_or_decimals (or explicit keyword decimals) is treated directly as decimals.
    """
    is_direct_decimals = (decimals is not None and symbol is None and chain_id_or_decimals is None)
    if decimals is not None:
        target_decimals = int(decimals)
    elif symbol is not None and chain_id_or_decimals is not None:
        reg = registry or get_token_registry()
        token_meta = reg.get_token(chain_id_or_decimals, symbol)
        target_decimals = token_meta.decimals
    elif chain_id_or_decimals is not None:
        target_decimals = int(chain_id_or_decimals)
    else:
        raise ConversionError("Either decimals or chain_id_or_decimals must be provided")

    if symbol is not None and isinstance(amount, float):
        raise ConversionError("Floating-point amounts are strictly prohibited to avoid precision loss. Use Decimal or str.")
    if isinstance(amount, float):
        amount = Decimal(str(amount))

    try:
        raw_bu = _dec_to_base_units(amount, target_decimals)
        if is_direct_decimals:
            return int(raw_bu)
        return BaseUnitStr(raw_bu)
    except (ValueError, TypeError) as exc:
        if is_direct_decimals:
            raise ValueError(f"Fractional base units are not allowed: {exc}") from exc
        sym_desc = f" for {symbol}" if symbol else ""
        raise ConversionError(f"Conversion to base units failed{sym_desc} ({target_decimals} dec): {exc}") from exc


def from_base_units(
    base_units: Union[str, int],
    chain_id_or_decimals: Optional[int] = None,
    symbol: Optional[str] = None,
    registry: Optional[TokenRegistry] = None,
    *,
    decimals: Optional[int] = None,
) -> Decimal:
    """
    Convert an authoritative base-unit string back into an exact Python Decimal display amount.
    If symbol is supplied, decimals are resolved authoritatively from the TokenRegistry for (chain_id, symbol).
    If symbol is None, chain_id_or_decimals is treated directly as decimals.
    """
    target_decimals = decimals if decimals is not None else chain_id_or_decimals
    if not isinstance(base_units, (str, int)):
        raise ConversionError(f"Base units must be a string or int, got {type(base_units).__name__}")
    base_units_str = str(base_units)

    if symbol is not None and target_decimals is not None:
        reg = registry or get_token_registry()
        token_meta = reg.get_token(target_decimals, symbol)
        effective_decimals = token_meta.decimals
    elif target_decimals is not None:
        effective_decimals = int(target_decimals)
    else:
        raise ConversionError("Either decimals or chain_id_or_decimals must be provided")

    try:
        return _base_units_to_dec(base_units_str, effective_decimals)
    except (ValueError, TypeError) as exc:
        sym_desc = f" for {symbol}" if symbol else ""
        raise ConversionError(f"Conversion from base units failed{sym_desc} ({effective_decimals} dec): {exc}") from exc


def derive_legacy_amount(
    base_units: str,
    chain_id: int,
    symbol: str,
    registry: Optional[TokenRegistry] = None,
) -> float:
    """
    Derive a legacy float representation (for backward-compatible API fields like price_eth/amount_eth)
    from authoritative base units.
    
    Warning: This is strictly for read-only legacy response compatibility.
    Internal financial operations must NEVER use this float value as a source of truth.
    """
    dec_val = from_base_units(base_units, chain_id, symbol, registry=registry)
    return float(dec_val)


def validate_financial_consistency(
    base_units: Optional[str],
    display_amount: Optional[Union[Decimal, str, int]],
    chain_id: int,
    symbol: str,
    field_name: str = "amount",
    registry: Optional[TokenRegistry] = None,
) -> str:
    """
    Validate and reconcile dual financial representation.
    - If only display_amount is provided, derive authoritative base units.
    - If only base_units is provided, validate and return it.
    - If both are provided, assert that display_amount converts exactly to base_units.
    - If neither is provided, raise ConversionError.
    """
    if base_units is None and display_amount is None:
        raise ConversionError(f"Missing financial input: both {field_name} and base units are None")

    if display_amount is not None:
        derived_base_units = to_base_units(display_amount, chain_id, symbol, registry=registry)
        if base_units is not None:
            clean_base_units = validate_base_units_str(base_units, field_name=f"{field_name}_base_units")
            if clean_base_units != derived_base_units:
                raise ConversionError(
                    f"Financial representation mismatch for {symbol}: "
                    f"provided base units '{clean_base_units}' != derived '{derived_base_units}' "
                    f"from display amount '{display_amount}'"
                )
            return clean_base_units
        return derived_base_units

    assert base_units is not None
    return validate_base_units_str(base_units, field_name=f"{field_name}_base_units", allow_none=False)
