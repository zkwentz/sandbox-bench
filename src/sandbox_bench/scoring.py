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

# Weights
WEIGHTS = {
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


def calculate_score(result: "BenchmarkResult") -> float:
    """
    Calculate overall score from benchmark result.
    
    Returns:
        Score from 0-100 (higher is better)
    """
    if not result.success:
        return 0.0
    
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
        (1 - time_norm) * WEIGHTS["time"] +
        (1 - calls_norm) * WEIGHTS["tool_calls"] +
        (1 - friction_norm) * WEIGHTS["friction"] +
        (1 - errors_norm) * WEIGHTS["errors"] +
        (1 - cost_norm) * WEIGHTS["cost"] +
        discoverability_norm * WEIGHTS["discoverability"]
    ) * 100
    
    return round(score, 1)


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
