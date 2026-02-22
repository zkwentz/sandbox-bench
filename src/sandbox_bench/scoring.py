"""Scoring and grading logic."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .benchmark import BenchmarkResult


# Normalization constants (based on observed ranges)
MAX_TIME_SECONDS = 300  # 5 minutes is failure
MAX_TOOL_CALLS = 50
MAX_FRICTION = 5
MAX_ERRORS = 10
MAX_COST_USD = 5.0

# Full weights (when capabilities data is present)
WEIGHTS_FULL = {
    "time": 0.25,
    "tool_calls": 0.10,
    "friction": 0.15,
    "errors": 0.20,
    "cost": 0.10,
    "discoverability": 0.10,
    "capabilities": 0.10,
}

# Base weights (when only basic suite runs, no capabilities data)
WEIGHTS_BASE = {
    "time": 0.30,
    "tool_calls": 0.15,
    "friction": 0.15,
    "errors": 0.20,
    "cost": 0.10,
    "discoverability": 0.10,
}


def normalize(value: float, max_value: float) -> float:
    """Normalize a value to 0-1 range (0 is best)."""
    return min(1.0, value / max_value)


def _get_weights(result: "BenchmarkResult") -> dict:
    """Get the appropriate weight set based on available data.

    When capabilities data is present (from non-basic suites), use the full
    weight set. Otherwise use base weights. Weights always sum to 1.0.
    """
    has_capabilities = bool(getattr(result, "capabilities", None))
    if has_capabilities:
        return WEIGHTS_FULL
    return WEIGHTS_BASE


def calculate_score(result: "BenchmarkResult") -> float:
    """
    Calculate overall score from benchmark result.

    Returns:
        Score from 0-100 (higher is better)
    """
    if not result.success:
        return 0.0

    weights = _get_weights(result)

    # Normalize each metric (lower is better for most)
    time_norm = normalize(result.total_time_seconds, MAX_TIME_SECONDS)
    calls_norm = normalize(result.tool_calls, MAX_TOOL_CALLS)
    friction_norm = normalize(result.friction_points, MAX_FRICTION)
    errors_norm = normalize(result.errors, MAX_ERRORS)
    cost_norm = normalize(result.estimated_cost_usd, MAX_COST_USD)

    # Discoverability is already 1-5, normalize to 0-1 (higher is better)
    discoverability_norm = result.discoverability_score / 5.0

    # Calculate weighted score
    # For most metrics, (1 - normalized) because lower is better
    # For discoverability, use directly because higher is better
    score = (
        (1 - time_norm) * weights["time"]
        + (1 - calls_norm) * weights["tool_calls"]
        + (1 - friction_norm) * weights["friction"]
        + (1 - errors_norm) * weights["errors"]
        + (1 - cost_norm) * weights["cost"]
        + discoverability_norm * weights["discoverability"]
    )

    # Add capabilities component if present
    if "capabilities" in weights:
        cap_score = getattr(result, "capability_score", 0.0)
        score += cap_score * weights["capabilities"]

    return round(score * 100, 1)


def calculate_grade(score: float) -> str:
    """
    Convert score to letter grade.

    Args:
        score: Score from 0-100

    Returns:
        Letter grade (A, B, C, D, F)
    """
    if score >= 85:
        return "A"
    elif score >= 70:
        return "B"
    elif score >= 55:
        return "C"
    elif score >= 40:
        return "D"
    else:
        return "F"
