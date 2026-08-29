"""Exact coefficient/exponent normalization for bounded Decimal contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Sequence


class ExactNormalizationError(ValueError):
    """A finite Decimal transform cannot be represented within contract bounds."""


@dataclass(frozen=True)
class ExactDecimalTerm:
    """One finite Decimal value and its declared power-of-ten destination scale."""

    value: Decimal
    scale_exponent: int


@dataclass(frozen=True)
class _CoefficientExponent:
    coefficient: int
    exponent: int


def exact_scaled_sum(
    terms: Sequence[ExactDecimalTerm],
    *,
    max_coefficient_digits: int = 256,
) -> Decimal:
    """Return an exact scaled sum without consulting any Decimal Context."""

    if (
        not isinstance(terms, (list, tuple))
        or not 1 <= len(terms) <= 16
        or not isinstance(max_coefficient_digits, int)
        or isinstance(max_coefficient_digits, bool)
        or not 1 <= max_coefficient_digits <= 4096
    ):
        raise ExactNormalizationError("exact normalization input is invalid")

    total = _CoefficientExponent(0, 0)
    for term in terms:
        if (
            not isinstance(term, ExactDecimalTerm)
            or not isinstance(term.value, Decimal)
            or not term.value.is_finite()
            or not isinstance(term.scale_exponent, int)
            or isinstance(term.scale_exponent, bool)
        ):
            raise ExactNormalizationError("exact normalization term is invalid")
        value = _from_decimal(term.value, max_coefficient_digits)
        scaled = _CoefficientExponent(
            coefficient=value.coefficient,
            exponent=value.exponent + term.scale_exponent,
        )
        total = _add(total, scaled, max_coefficient_digits)
    return _to_decimal(total, max_coefficient_digits)


def _from_decimal(
    value: Decimal,
    max_coefficient_digits: int,
) -> _CoefficientExponent:
    decimal_tuple = value.as_tuple()
    digits = decimal_tuple.digits
    exponent = decimal_tuple.exponent
    if not isinstance(exponent, int) or len(digits) > max_coefficient_digits:
        raise ExactNormalizationError("exact normalization value exceeds bounds")
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    if decimal_tuple.sign:
        coefficient = -coefficient
    return _canonical(coefficient, exponent)


def _add(
    left: _CoefficientExponent,
    right: _CoefficientExponent,
    max_coefficient_digits: int,
) -> _CoefficientExponent:
    if left.coefficient == 0:
        return right
    if right.coefficient == 0:
        return left
    common_exponent = min(left.exponent, right.exponent)
    left_shift = left.exponent - common_exponent
    right_shift = right.exponent - common_exponent
    if (
        _digit_count(left.coefficient) + left_shift > max_coefficient_digits
        or _digit_count(right.coefficient) + right_shift > max_coefficient_digits
    ):
        raise ExactNormalizationError("exact normalization result exceeds bounds")
    coefficient = left.coefficient * (10**left_shift) + right.coefficient * (
        10**right_shift
    )
    result = _canonical(coefficient, common_exponent)
    if _digit_count(result.coefficient) > max_coefficient_digits:
        raise ExactNormalizationError("exact normalization result exceeds bounds")
    return result


def _canonical(coefficient: int, exponent: int) -> _CoefficientExponent:
    if coefficient == 0:
        return _CoefficientExponent(0, 0)
    while coefficient % 10 == 0:
        coefficient //= 10
        exponent += 1
    return _CoefficientExponent(coefficient, exponent)


def _digit_count(coefficient: int) -> int:
    return len(str(abs(coefficient))) if coefficient else 1


def _to_decimal(
    value: _CoefficientExponent,
    max_coefficient_digits: int,
) -> Decimal:
    if value.coefficient == 0:
        return Decimal(0)
    digits_text = str(abs(value.coefficient))
    if len(digits_text) > max_coefficient_digits:
        raise ExactNormalizationError("exact normalization result exceeds bounds")
    try:
        return Decimal(
            (
                int(value.coefficient < 0),
                tuple(ord(character) - ord("0") for character in digits_text),
                value.exponent,
            )
        )
    except (InvalidOperation, OverflowError, ValueError) as error:
        raise ExactNormalizationError(
            "exact normalization exponent exceeds runtime bounds"
        ) from error
