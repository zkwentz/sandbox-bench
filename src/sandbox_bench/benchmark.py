"""Core benchmark runner."""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .provider import SandboxProvider, get_provider
from .scoring import calculate_score, calculate_grade
from .pricing import estimate_sandbox_cost
from .capabilities import aggregate_capabilities, capability_score
from .suites import PhaseResult, get_suite, list_suites

# Import suites so they register themselves
from .suites import basic as _basic  # noqa: F401
from .suites import competitive as _competitive  # noqa: F401
from .suites import swe as _swe  # noqa: F401
from .suites import environment as _environment  # noqa: F401
from .suites import performance as _performance  # noqa: F401

# The "full" alias expands to all available suites
SUITE_ALIASES = {
    "full": ["basic", "competitive", "swe", "environment", "performance"],
}


@dataclass
class BenchmarkConfig:
    """Configuration for benchmark runs."""

    # Providers to test
    providers: List[str] = field(default_factory=list)

    # Suites to run (default: basic only for backward compat)
    suites: List[str] = field(default_factory=lambda: ["basic"])

    # Agent mode settings
    agent_mode: bool = False
    model: str = "claude-opus-4"

    # Benchmark parameters
    warmup_runs: int = 1
    benchmark_runs: int = 3
    timeout_seconds: int = 300

    # Cost tracking (kept for backward compat but no longer drives cost)
    track_costs: bool = True
    cost_per_1k_input_tokens: float = 0.015
    cost_per_1k_output_tokens: float = 0.075


@dataclass
class BenchmarkResult:
    """Result from benchmarking a single provider."""

    provider: str
    success: bool

    # Timing
    total_time_seconds: float
    auth_time_seconds: float
    create_time_seconds: float
    execute_time_seconds: float
    file_io_time_seconds: float
    destroy_time_seconds: float

    # Metrics
    tool_calls: int
    friction_points: int
    errors: int
    error_messages: List[str]

    # Cost - provider-specific sandbox pricing
    estimated_cost_usd: float
    input_tokens: int  # Kept for backward compat
    output_tokens: int  # Kept for backward compat

    # Discoverability
    discoverability_score: float

    # Computed
    score: float = 0.0
    grade: str = "F"

    # Raw data
    trace: List[Dict[str, Any]] = field(default_factory=list)

    # New fields for suite system
    suites_run: List[str] = field(default_factory=list)
    suite_results: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    capabilities: Dict[str, bool] = field(default_factory=dict)
    capability_score: float = 0.0
    cold_start_seconds: float = 0.0
    warm_start_seconds: Optional[float] = None
    agent_spawn_seconds: Optional[float] = None
    file_io_throughput_mbps: Optional[float] = None
    sandbox_cost_usd: float = 0.0

    def __post_init__(self):
        self.score = calculate_score(self)
        self.grade = calculate_grade(self.score)


@dataclass
class BenchmarkTrace:
    """Trace entry for debugging."""

    timestamp: float
    action: str
    duration_ms: float
    success: bool
    details: Dict[str, Any] = field(default_factory=dict)


class BenchmarkRunner:
    """Runs benchmarks against sandbox providers."""

    def __init__(self, config: BenchmarkConfig):
        self.config = config
        self.traces: List[BenchmarkTrace] = []

    def _trace(
        self,
        action: str,
        duration_ms: float,
        success: bool,
        **details,
    ) -> None:
        """Add a trace entry."""
        self.traces.append(BenchmarkTrace(
            timestamp=time.time(),
            action=action,
            duration_ms=duration_ms,
            success=success,
            details=details,
        ))

    def _resolve_suites(self) -> List[str]:
        """Resolve suite names, expanding aliases like 'full'."""
        resolved = []
        for s in self.config.suites:
            if s in SUITE_ALIASES:
                resolved.extend(SUITE_ALIASES[s])
            else:
                resolved.append(s)
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for s in resolved:
            if s not in seen:
                seen.add(s)
                unique.append(s)
        return unique

    async def run_single(
        self,
        provider: SandboxProvider,
        api_key: str,
    ) -> BenchmarkResult:
        """Run benchmark against a single provider."""
        self.traces = []
        errors: List[str] = []
        friction_points = 0
        provider.reset_api_calls()

        # Track timing for each phase
        times = {
            "auth": 0.0,
            "create": 0.0,
            "execute": 0.0,
            "file_io": 0.0,
            "destroy": 0.0,
        }

        start_time = time.time()
        sandbox_id = None
        success = True
        destroyed = False

        # Suite tracking
        suite_names = self._resolve_suites()
        all_phase_results: List[PhaseResult] = []
        suite_results: Dict[str, List[Dict[str, Any]]] = {}

        try:
            # Phase 1: Authentication
            t0 = time.time()
            try:
                await provider.authenticate(api_key)
                times["auth"] = time.time() - t0
                self._trace("authenticate", times["auth"] * 1000, True)
            except Exception as e:
                errors.append(f"Auth failed: {e}")
                success = False
                times["auth"] = time.time() - t0
                self._trace("authenticate", times["auth"] * 1000, False, error=str(e))
                raise

            # Phase 2: Create sandbox (cold start)
            t0 = time.time()
            try:
                sandbox_id = await provider.create_sandbox(
                    timeout_seconds=self.config.timeout_seconds,
                )
                times["create"] = time.time() - t0
                self._trace("create_sandbox", times["create"] * 1000, True, sandbox_id=sandbox_id)
            except Exception as e:
                errors.append(f"Create failed: {e}")
                success = False
                times["create"] = time.time() - t0
                self._trace("create_sandbox", times["create"] * 1000, False, error=str(e))
                raise

            # Run test suites
            for suite_name in suite_names:
                suite_class = get_suite(suite_name)
                suite = suite_class()

                t_suite = time.time()
                try:
                    phase_results = await suite.run(provider, sandbox_id)
                except Exception as e:
                    # Suite crashed entirely
                    errors.append(f"Suite {suite_name} failed: {e}")
                    phase_results = [PhaseResult(
                        name=f"{suite_name}_error",
                        success=False,
                        duration_seconds=time.time() - t_suite,
                        errors=1,
                        error_messages=[str(e)],
                    )]

                suite_duration = time.time() - t_suite

                # Accumulate metrics from phase results
                suite_phase_dicts = []
                for pr in phase_results:
                    friction_points += pr.friction_points
                    errors.extend(pr.error_messages)
                    all_phase_results.append(pr)

                    # Add trace entry for each phase
                    self._trace(
                        f"{suite_name}/{pr.name}",
                        pr.duration_seconds * 1000,
                        pr.success,
                        **pr.details,
                    )

                    suite_phase_dicts.append({
                        "name": pr.name,
                        "success": pr.success,
                        "duration_seconds": pr.duration_seconds,
                        "tool_calls": pr.tool_calls,
                        "friction_points": pr.friction_points,
                        "errors": pr.errors,
                        "capability_tested": pr.capability_tested,
                        "capability_supported": pr.capability_supported,
                    })

                suite_results[suite_name] = suite_phase_dicts

                # Map basic suite phases to legacy timing fields
                if suite_name == "basic":
                    for pr in phase_results:
                        if pr.name == "execute_hello":
                            times["execute"] = pr.duration_seconds
                        elif pr.name == "file_io":
                            times["file_io"] = pr.duration_seconds

            # Phase: Cleanup
            t0 = time.time()
            try:
                await provider.destroy(sandbox_id)
                destroyed = True
                times["destroy"] = time.time() - t0
                self._trace("destroy", times["destroy"] * 1000, True)
            except Exception as e:
                errors.append(f"Destroy failed: {e}")
                times["destroy"] = time.time() - t0
                self._trace("destroy", times["destroy"] * 1000, False, error=str(e))

        except Exception:
            success = False

        finally:
            # Safety cleanup - only if not already destroyed
            if sandbox_id and not destroyed:
                try:
                    await provider.destroy(sandbox_id)
                except Exception:
                    pass

        total_time = time.time() - start_time
        tool_calls = provider.api_calls

        # Provider-specific cost estimation
        sandbox_cost = estimate_sandbox_cost(provider.name, total_time)

        # Aggregate capabilities from suite results
        capabilities = aggregate_capabilities(all_phase_results)
        cap_score = capability_score(capabilities)

        # Extract performance metrics if available
        warm_start_seconds = None
        agent_spawn_seconds = None
        file_io_throughput = None
        for pr in all_phase_results:
            if pr.name == "agent_spawn" and pr.success:
                agent_spawn_seconds = pr.details.get("agent_spawn_seconds")
            if pr.name == "warm_start" and pr.success:
                warm_start_seconds = pr.details.get("warm_start_seconds")
            if pr.name == "file_io_1mb_write" and pr.success:
                file_io_throughput = pr.details.get("throughput_mbps")

        # Backward-compat token estimates (not used for cost anymore)
        input_tokens = tool_calls * 500
        output_tokens = tool_calls * 200

        return BenchmarkResult(
            provider=provider.name,
            success=success,
            total_time_seconds=total_time,
            auth_time_seconds=times["auth"],
            create_time_seconds=times["create"],
            execute_time_seconds=times["execute"],
            file_io_time_seconds=times["file_io"],
            destroy_time_seconds=times["destroy"],
            tool_calls=tool_calls,
            friction_points=friction_points,
            errors=len(errors),
            error_messages=errors,
            estimated_cost_usd=sandbox_cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            discoverability_score=provider.get_discoverability_score(),
            trace=[{
                "timestamp": t.timestamp,
                "action": t.action,
                "duration_ms": t.duration_ms,
                "success": t.success,
                "details": t.details,
            } for t in self.traces],
            suites_run=suite_names,
            suite_results=suite_results,
            capabilities=capabilities,
            capability_score=cap_score,
            cold_start_seconds=times["create"],
            warm_start_seconds=warm_start_seconds,
            agent_spawn_seconds=agent_spawn_seconds,
            file_io_throughput_mbps=file_io_throughput,
            sandbox_cost_usd=sandbox_cost,
        )

    async def run_all(
        self,
        api_keys: Dict[str, str],
    ) -> List[BenchmarkResult]:
        """Run benchmark against all configured providers."""
        results = []

        for provider_name in self.config.providers:
            if provider_name not in api_keys:
                print(f"Skipping {provider_name}: no API key provided")
                continue

            provider_class = get_provider(provider_name)
            provider = provider_class()

            print(f"Benchmarking {provider_name}...")
            print(f"  Suites: {', '.join(self._resolve_suites())}")

            # Warmup runs
            for i in range(self.config.warmup_runs):
                try:
                    await self.run_single(provider, api_keys[provider_name])
                except Exception:
                    pass

            # Benchmark runs
            run_results = []
            for i in range(self.config.benchmark_runs):
                try:
                    result = await self.run_single(provider, api_keys[provider_name])
                    run_results.append(result)
                except Exception as e:
                    print(f"  Run {i+1} failed: {e}")

            if run_results:
                # Use the best result
                best = min(run_results, key=lambda r: r.total_time_seconds)
                results.append(best)
                print(f"  {provider_name}: {best.total_time_seconds:.1f}s, grade {best.grade}")

        # Sort by score
        results.sort(key=lambda r: r.score, reverse=True)

        return results


async def run_benchmark(
    config: BenchmarkConfig,
    api_keys: Dict[str, str],
) -> List[BenchmarkResult]:
    """Run benchmark with given configuration."""
    runner = BenchmarkRunner(config)
    return await runner.run_all(api_keys)
