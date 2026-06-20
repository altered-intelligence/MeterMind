"""Small Decimal helpers shared across services.

Centralizes the money/division guard so the same rounding and divide-by-zero/None handling
is used everywhere (previously duplicated in schemas, bill_service, and bill_comparison).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional


def safe_div(numerator, denominator, places: str = "0.0001") -> Optional[Decimal]:
    """Decimal division guarding against ``None`` and zero denominators.

    Returns the quotient quantized to ``places``, or ``None`` when either operand is missing
    or the denominator is zero.
    """
    if numerator is None or denominator is None:
        return None
    num, den = Decimal(numerator), Decimal(denominator)
    if den == 0:
        return None
    return (num / den).quantize(Decimal(places))
