"""Test suite framework for sandbox-bench."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Type

from ..provider import SandboxProvider


@dataclass
class PhaseResult:
    """Result from a single test phase within a suite."""

    name: str
    success: bool
    duration_seconds: float
    tool_calls: int = 0
    friction_points: int = 0
    errors: int = 0
    error_messages: List[str] = field(default_factory=list)
    # Capability detection
    capability_tested: Optional[str] = None
    capability_supported: Optional[bool] = None
    # Extra data for trace
    details: Dict = field(default_factory=dict)


class TestSuite(ABC):
    """Base class for test suites."""

    name: str = "base"
    description: str = ""

    @abstractmethod
    async def run(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> List[PhaseResult]:
        """Run all phases in this suite.

        Args:
            provider: The sandbox provider to test against.
            sandbox_id: ID of the already-created sandbox.

        Returns:
            List of PhaseResult for each phase executed.
        """
        pass


# Suite registry
_suites: Dict[str, Type[TestSuite]] = {}


def register_suite(suite_class: Type[TestSuite]) -> None:
    """Register a test suite class."""
    _suites[suite_class.name] = suite_class


def get_suite(name: str) -> Type[TestSuite]:
    """Get a suite class by name."""
    if name not in _suites:
        raise ValueError(
            f"Unknown suite: {name}. Available: {list(_suites.keys())}"
        )
    return _suites[name]


def list_suites() -> list[str]:
    """List all registered suite names."""
    return list(_suites.keys())
