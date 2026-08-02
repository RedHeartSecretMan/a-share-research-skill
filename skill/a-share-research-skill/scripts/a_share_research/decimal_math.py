"""Exact decimal operations shared by valuation selection and calculation."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import cast


def exact_product(*factors: Decimal) -> Decimal:
    """Multiply finite Decimal operands without the ambient context truncating them."""
    precision = max(1, sum(len(factor.as_tuple().digits) for factor in factors))
    with localcontext() as context:
        context.prec = precision
        result = Decimal(1)
        for factor in factors:
            result *= factor
        return result


def exact_sum(*terms: Decimal) -> Decimal:
    """Add signed Decimal terms with enough precision for every place and carry."""
    if not terms:
        return Decimal(0)
    least_exponent = min(cast(int, term.as_tuple().exponent) for term in terms)
    greatest_adjusted = max(term.adjusted() for term in terms)
    carry_digits = len(str(len(terms)))
    with localcontext() as context:
        context.prec = max(
            1,
            greatest_adjusted - least_exponent + 1 + carry_digits,
        )
        return sum(terms, start=Decimal(0))


def exact_difference(left: Decimal, right: Decimal) -> Decimal:
    """Subtract without losing low-order places or a leading carry."""
    return exact_sum(left, right.copy_negate())


def decimal_ratio(
    numerator: Decimal,
    denominator: Decimal,
) -> Decimal:
    """Return a reproducibly rounded Decimal ratio."""
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        return numerator / denominator
