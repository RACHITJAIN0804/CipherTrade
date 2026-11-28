"""
Numeric utilities and validation for base-unit uint256 token amounts.

Strict numeric rules:
- Decimal digits only ('0'..'9')
- No negative values, no signs (+ or -)
- No scientific notation (no 'e' or 'E')
- No decimal points
- Maximum 78 decimal digits
- Value <= 2**256 - 1 (115792089237316195423570985008687907853269984665640564039457584007913129639935)
- Exact Python Decimal conversions without floating-point arithmetic.
"""
from __future__ import annotations

import decimal
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# 2**256 - 1
UINT256_MAX = (1 << 256) - 1
UINT256_MAX_STR = str(UINT256_MAX)
UINT256_MAX_DECIMAL = Decimal(UINT256_MAX)

_DIGITS_ONLY_RE = re.compile(r"^\d+$")


def validate_base_units_str(value: Any, field_name: str = "amount_base_units", allow_none: bool = False) -> Optional[str]:
    """
    Validate that value is a strictly valid base-unit uint256 string.
    
    Returns the cleaned string, or None if allow_none is True and value is None.
    Raises ValueError or TypeError if invalid.
    """
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{field_name} cannot be None")

    if not isinstance(value, str):
        # Reject ints/floats directly to avoid silent precision issues or float representations
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")

    if not value:
        raise ValueError(f"{field_name} cannot be empty")

    if not _DIGITS_ONLY_RE.match(value):
        raise ValueError(
            f"{field_name} must contain only decimal digits (no signs, decimals, or exponents): '{value}'"
        )

    # Disallow leading zeroes unless the value is exactly "0"
    if len(value) > 1 and value.startswith("0"):
        raise ValueError(f"{field_name} cannot have unnormalized leading zeroes: '{value}'")

    if len(value) > 78:
        raise ValueError(f"{field_name} exceeds maximum length of 78 digits (got {len(value)})")

    int_val = int(value)
    if int_val > UINT256_MAX:
        raise ValueError(f"{field_name} exceeds uint256 maximum ({UINT256_MAX})")

    return value


def decimal_to_base_units_str(amount: Decimal | str | int, decimals: int) -> str:
    """
    Convert a human/token decimal amount (e.g. 1.5 ETH, 100.25 USDC) to exact base units string.
    
    Uses exact Decimal arithmetic.
    Raises ValueError if:
    - decimals < 0 or > 255
    - amount is negative
    - amount has fractional base units (sub-atomic quantum that cannot be represented in integer base units)
    - result exceeds uint256 maximum
    """
    if decimals < 0 or decimals > 255:
        raise ValueError(f"Decimals must be between 0 and 255, got {decimals}")

    if isinstance(amount, float):
        raise TypeError("Floating-point amounts are prohibited to avoid precision loss. Use Decimal or str.")

    try:
        dec_amount = Decimal(str(amount))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Invalid decimal amount: {amount}") from exc

    if not dec_amount.is_finite():
        raise ValueError(f"Amount must be a finite number: {amount}")

    if dec_amount < Decimal("0"):
        raise ValueError(f"Base units cannot represent negative amounts: {amount}")

    scale_factor = Decimal(10) ** decimals
    base_units_dec = dec_amount * scale_factor

    # Check that there is no fractional part in base units (exact integer)
    if base_units_dec != base_units_dec.to_integral_value():
        raise ValueError(
            f"Amount {amount} has fractional base units with {decimals} decimals "
            f"(scale factor {scale_factor}, product {base_units_dec})"
        )

    base_units_int = int(base_units_dec)
    if base_units_int > UINT256_MAX:
        raise ValueError(f"Resulting base units {base_units_int} exceeds uint256 max")

    return str(base_units_int)


def base_units_str_to_decimal(base_units: str, decimals: int) -> Decimal:
    """
    Convert an exact base units string to a Python Decimal with the given decimals.
    """
    validated = validate_base_units_str(base_units, allow_none=False)
    assert validated is not None
    if decimals < 0 or decimals > 255:
        raise ValueError(f"Decimals must be between 0 and 255, got {decimals}")

    scale_factor = Decimal(10) ** decimals
    return Decimal(validated) / scale_factor



def eth_amount_to_base_units_str(amount: Any) -> str:
    """
    Safely convert an ETH amount (Decimal, str, int, or float from legacy models)
    to exact 18-decimal base units string.

    Guarantees:
    - Input cannot be None.
    - Uses exact string-to-Decimal conversion.
    - Prohibits negative amounts.
    - Prohibits >18 decimal places.
    - Rejects fractional base units (sub-wei).
    - Enforces uint256 bounds.
    """
    if amount is None:
        raise ValueError("Amount cannot be None")

    try:
        dec = Decimal(str(amount))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Invalid numeric amount: {amount}") from exc

    if not dec.is_finite():
        raise ValueError(f"Amount must be a finite number: {amount}")

    if dec < Decimal("0"):
        raise ValueError(f"Amount cannot be negative: {amount}")

    # Check decimal places
    # If the number has an exponent less than -18, it has > 18 decimal places
    _, _, exponent = dec.as_tuple()
    if isinstance(exponent, int) and exponent < -18:
        raise ValueError(f"Amount {amount} has more than 18 decimal places ({abs(exponent)})")

    # Use high precision context (100 digits) for base units scaling
    with decimal.localcontext(decimal.Context(prec=100)):
        scale_18 = Decimal("1000000000000000000")
        base_units_dec = dec * scale_18

        if base_units_dec != base_units_dec.to_integral_value():
            raise ValueError(f"Amount {amount} has fractional wei")

        int_val = int(base_units_dec)
        if int_val > UINT256_MAX:
            raise ValueError(f"Amount {amount} exceeds uint256 maximum in base units")

    return str(int_val)
