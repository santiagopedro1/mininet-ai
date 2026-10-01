"""Explicitly regenerate reviewed deployment-plan fixtures."""

from __future__ import annotations

from tests.golden_plans import GOLDEN_CASES, render_golden_plan


def main() -> None:
    for name, (source, fixture) in GOLDEN_CASES.items():
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text(render_golden_plan(source), encoding="utf-8")
        print(f"updated {name}: {fixture}")


if __name__ == "__main__":
    main()
