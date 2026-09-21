from __future__ import annotations

import unittest

from tests.golden_plans import GOLDEN_CASES, render_golden_plan


class GoldenDeploymentPlanTests(unittest.TestCase):
    maxDiff = None

    def test_compiled_plans_match_reviewed_fixtures(self) -> None:
        for name, (source, fixture) in GOLDEN_CASES.items():
            with self.subTest(name=name):
                expected = fixture.read_text(encoding="utf-8")
                actual = render_golden_plan(source)
                self.assertMultiLineEqual(
                    expected,
                    actual,
                    msg=(
                        "deployment plan changed; review the compiler/schema change, "
                        "then run `uv run python -m tests.update_golden_plans` "
                        "if the new contract is intentional"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
