"""Open source benchmark suite for AI agent sandbox providers."""

__version__ = "0.1.0"

from .provider import SandboxProvider, register_provider, get_provider
from .benchmark import run_benchmark, BenchmarkResult, BenchmarkConfig
from .scoring import calculate_score, calculate_grade
from .pricing import estimate_sandbox_cost
from .capabilities import aggregate_capabilities, capability_score

__all__ = [
    "SandboxProvider",
    "register_provider",
    "get_provider",
    "run_benchmark",
    "BenchmarkResult",
    "BenchmarkConfig",
    "calculate_score",
    "calculate_grade",
    "estimate_sandbox_cost",
    "aggregate_capabilities",
    "capability_score",
]
