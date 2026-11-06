"""Confidence validity shared by campaign adapters and runner policy."""

import math


def valid_confidence(value: object) -> bool:
    """Accept finite JSON numbers in [0,1], never booleans or numeric strings."""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        # Reject huge integers before isfinite can overflow converting to float.
        and 0 <= value <= 1
        and math.isfinite(value)
    )
