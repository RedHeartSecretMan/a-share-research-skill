from __future__ import annotations

import unittest
from decimal import MAX_EMAX, Decimal, localcontext

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.exact_normalization import (  # noqa: E402
    ExactDecimalTerm,
    ExactNormalizationError,
    exact_scaled_sum,
)


class ExactNormalizationTests(unittest.TestCase):
    def test_compact_extreme_exponent_sum_ignores_decimal_context(self) -> None:
        terms = (
            ExactDecimalTerm(Decimal("1e1000000"), 8),
            ExactDecimalTerm(Decimal("2e1000000"), 8),
        )

        for precision, emax, emin in ((6, 9, -9), (28, 99, -99), (80, 999, -999)):
            with self.subTest(precision=precision, emax=emax, emin=emin):
                with localcontext() as context:
                    context.prec = precision
                    context.Emax = emax
                    context.Emin = emin
                    result = exact_scaled_sum(terms)
                self.assertEqual(result.as_tuple(), Decimal("3e1000008").as_tuple())

    def test_noncompact_exponent_gap_fails_before_unbounded_expansion(self) -> None:
        with self.assertRaisesRegex(
            ExactNormalizationError,
            "result exceeds bounds",
        ):
            exact_scaled_sum(
                (
                    ExactDecimalTerm(Decimal("1e1000000"), 0),
                    ExactDecimalTerm(Decimal("1"), 0),
                )
            )

    def test_nonfinite_term_fails_closed(self) -> None:
        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ExactNormalizationError,
                    "term is invalid",
                ):
                    exact_scaled_sum((ExactDecimalTerm(value, 0),))

    def test_hard_runtime_exponent_limit_has_stable_fail_closed_result(self) -> None:
        with self.assertRaisesRegex(
            ExactNormalizationError,
            "exponent exceeds runtime bounds",
        ):
            exact_scaled_sum((ExactDecimalTerm(Decimal((0, (1,), MAX_EMAX)), 1),))


if __name__ == "__main__":
    unittest.main()
