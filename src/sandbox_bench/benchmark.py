"""Core benchmark runner."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .provider import SandboxProvider, get_provider
from .scoring import calculate_score, calculate_grade


@dataclass
class BenchmarkConfig:
    """Configuration for benchmark runs."""
    
    # Providers to test
    providers: List[str] = field(default_factory=list)
    
    # Agent mode settings
    agent_mode: bool = False
    model: str = "claude-opus-4"
    
    # Benchmark parameters
    warmup_runs: int = 1
    benchmark_runs: int = 3
    timeout_seconds: int = 300
    
    # Cost tracking
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
    
    # Cost
    estimated_cost_usd: float
    input_tokens: int
    output_tokens: int
    
    # Discoverability
    discoverability_score: float
    
    # Computed
    score: float = 0.0
    grade: str = "F"
    
    # Raw data
    trace: List[Dict[str, Any]] = field(default_factory=list)
    
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
    
    async def run_single(
        self,
        provider: SandboxProvider,
        api_key: str,
    ) -> BenchmarkResult:
        """Run benchmark against a single provider."""
        self.traces = []
        errors: List[str] = []
        friction_points = 0
        tool_calls = 0
        
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
        
        try:
            # Phase 1: Authentication
            t0 = time.time()
            try:
                await provider.authenticate(api_key)
                tool_calls += 1
                times["auth"] = time.time() - t0
                self._trace("authenticate", times["auth"] * 1000, True)
            except Exception as e:
                errors.append(f"Auth failed: {e}")
                success = False
                times["auth"] = time.time() - t0
                self._trace("authenticate", times["auth"] * 1000, False, error=str(e))
                raise
            
            # Phase 2: Create sandbox
            t0 = time.time()
            try:
                sandbox_id = await provider.create_sandbox(
                    timeout_seconds=self.config.timeout_seconds,
                )
                tool_calls += 1
                times["create"] = time.time() - t0
                self._trace("create_sandbox", times["create"] * 1000, True, sandbox_id=sandbox_id)
            except Exception as e:
                errors.append(f"Create failed: {e}")
                success = False
                times["create"] = time.time() - t0
                self._trace("create_sandbox", times["create"] * 1000, False, error=str(e))
                raise
            
            # Phase 3: Execute code
            t0 = time.time()
            test_code = "print('Hello from sandbox-bench!')"
            try:
                stdout, stderr, exit_code = await provider.execute(
                    sandbox_id,
                    test_code,
                    language="python",
                )
                tool_calls += 1
                times["execute"] = time.time() - t0
                
                if exit_code != 0:
                    errors.append(f"Execute returned non-zero: {exit_code}")
                    friction_points += 1
                
                if "Hello from sandbox-bench!" not in stdout:
                    errors.append(f"Unexpected output: {stdout}")
                    friction_points += 1
                
                self._trace(
                    "execute",
                    times["execute"] * 1000,
                    exit_code == 0,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                )
            except Exception as e:
                errors.append(f"Execute failed: {e}")
                success = False
                times["execute"] = time.time() - t0
                self._trace("execute", times["execute"] * 1000, False, error=str(e))
                raise
            
            # Phase 4: File I/O
            t0 = time.time()
            test_content = "sandbox-bench test file content"
            test_path = "/tmp/sandbox-bench-test.txt"
            try:
                await provider.write_file(sandbox_id, test_path, test_content)
                tool_calls += 1
                
                read_content = await provider.read_file(sandbox_id, test_path)
                tool_calls += 1
                
                times["file_io"] = time.time() - t0
                
                if read_content != test_content:
                    errors.append(f"File content mismatch: {read_content}")
                    friction_points += 1
                
                self._trace("file_io", times["file_io"] * 1000, True)
            except Exception as e:
                errors.append(f"File I/O failed: {e}")
                friction_points += 1
                times["file_io"] = time.time() - t0
                self._trace("file_io", times["file_io"] * 1000, False, error=str(e))
            
            # Phase 5: Cleanup
            t0 = time.time()
            try:
                await provider.destroy(sandbox_id)
                tool_calls += 1
                times["destroy"] = time.time() - t0
                self._trace("destroy", times["destroy"] * 1000, True)
            except Exception as e:
                errors.append(f"Destroy failed: {e}")
                times["destroy"] = time.time() - t0
                self._trace("destroy", times["destroy"] * 1000, False, error=str(e))
        
        except Exception:
            success = False
        
        finally:
            # Make sure we clean up
            if sandbox_id:
                try:
                    await provider.destroy(sandbox_id)
                except Exception:
                    pass
        
        total_time = time.time() - start_time
        
        # Estimate cost (rough, based on typical API pricing)
        # This would be more accurate in agent mode with actual token counts
        input_tokens = tool_calls * 500  # Rough estimate
        output_tokens = tool_calls * 200  # Rough estimate
        estimated_cost = (
            (input_tokens / 1000) * self.config.cost_per_1k_input_tokens +
            (output_tokens / 1000) * self.config.cost_per_1k_output_tokens
        )
        
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
            estimated_cost_usd=estimated_cost,
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
