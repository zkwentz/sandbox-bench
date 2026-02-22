"""Capability detection and aggregation."""

from typing import Dict, List

from .suites import PhaseResult

# Maps capability name to human-readable description
CAPABILITY_DESCRIPTIONS: Dict[str, str] = {
    # Competitive
    "stdin_piping": "Pipe stdin to running process",
    "gcc": "GCC C compiler available",
    "gpp": "G++ C++ compiler available",
    "exec_timeout": "Server-side execution timeout enforcement",
    "python3": "Python 3 runtime available",
    # SWE
    "network_access": "Outbound network connectivity",
    "pip_install": "pip package installation",
    "git_clone": "Git clone from remote repositories",
    "pytest": "pytest test framework execution",
    # Environment
    "nodejs": "Node.js runtime available",
    "npm": "npm package manager and install",
    "project_clone": "Clone real-world project repos",
    "multi_step_build": "Multi-step build and test pipeline",
    "python_venv": "Python venv creation and use",
    # Performance
    "agent_spawn": "End-to-end sub-agent spawn to sandbox ready",
    "warm_start": "Warm/pre-warmed sandbox creation",
}


def aggregate_capabilities(
    phase_results: List[PhaseResult],
) -> Dict[str, bool]:
    """Aggregate capability support from suite phase results.

    Args:
        phase_results: All PhaseResult objects from suite runs.

    Returns:
        Dict mapping capability name to whether it's supported.
        Only includes capabilities that were actually tested.
    """
    caps: Dict[str, bool] = {}
    for pr in phase_results:
        if pr.capability_tested is not None and pr.capability_supported is not None:
            caps[pr.capability_tested] = pr.capability_supported
    return caps


def capability_score(capabilities: Dict[str, bool]) -> float:
    """Calculate a 0-1 score from capabilities dict.

    Returns:
        Fraction of tested capabilities that are supported (0.0 to 1.0).
        Returns 0.0 if no capabilities were tested.
    """
    if not capabilities:
        return 0.0
    supported = sum(1 for v in capabilities.values() if v)
    return supported / len(capabilities)
