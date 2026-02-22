"""Performance metrics test suite."""

import time
from typing import List

from . import PhaseResult, TestSuite, register_suite
from ..provider import SandboxProvider


class PerformanceSuite(TestSuite):
    """Performance benchmarks: warm start, large file I/O, rapid exec."""

    name = "performance"
    description = "Warm start time, file I/O throughput, rapid execution latency"

    async def run(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> List[PhaseResult]:
        results = []
        results.append(await self._agent_spawn(provider, sandbox_id))
        results.append(await self._warm_start(provider, sandbox_id))
        results.append(await self._file_io_1mb_write(provider, sandbox_id))
        results.append(await self._file_io_1mb_read(provider, sandbox_id))
        results.append(await self._file_io_10mb(provider, sandbox_id))
        results.append(await self._rapid_exec(provider, sandbox_id))
        return results

    async def _agent_spawn(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        """Simulate the full agent-spawn-to-ready path: create a new sandbox
        and execute a trivial command to confirm it is ready.  This measures
        the end-to-end latency an orchestrator experiences when delegating
        work to a sub-agent that needs its own sandbox."""
        t0 = time.time()
        spawn_id = None
        try:
            spawn_id = await provider.create_sandbox(timeout_seconds=60)
            create_time = time.time() - t0

            # Verify sandbox is truly ready by executing a command
            t_exec = time.time()
            stdout, stderr, exit_code = await provider.execute_command(
                spawn_id, "echo ready"
            )
            exec_time = time.time() - t_exec
            total = time.time() - t0

            ready = exit_code == 0 and "ready" in stdout
            return PhaseResult(
                name="agent_spawn",
                success=ready,
                duration_seconds=total,
                tool_calls=3,  # create + exec + destroy
                friction_points=0 if ready else 1,
                capability_tested="agent_spawn",
                capability_supported=ready,
                details={
                    "agent_spawn_seconds": round(total, 4),
                    "create_seconds": round(create_time, 4),
                    "first_exec_seconds": round(exec_time, 4),
                },
            )
        except Exception as e:
            return PhaseResult(
                name="agent_spawn",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="agent_spawn",
                capability_supported=False,
                error_messages=[str(e)],
            )
        finally:
            if spawn_id:
                try:
                    await provider.destroy(spawn_id)
                except Exception:
                    pass

    async def _warm_start(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        """Create a 2nd sandbox immediately, time it, destroy it."""
        t0 = time.time()
        warm_id = None
        try:
            warm_id = await provider.create_sandbox(timeout_seconds=60)
            create_time = time.time() - t0
            return PhaseResult(
                name="warm_start",
                success=True,
                duration_seconds=create_time,
                tool_calls=2,  # create + destroy
                capability_tested="warm_start",
                capability_supported=True,
                details={"warm_start_seconds": create_time},
            )
        except Exception as e:
            return PhaseResult(
                name="warm_start",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="warm_start",
                capability_supported=False,
                error_messages=[str(e)],
            )
        finally:
            if warm_id:
                try:
                    await provider.destroy(warm_id)
                except Exception:
                    pass

    async def _file_io_1mb_write(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        # 1MB of data
        data = "x" * (1024 * 1024)
        try:
            await provider.write_file(sandbox_id, "/tmp/bench-1mb.txt", data)
            elapsed = time.time() - t0
            throughput_mbps = 1.0 / elapsed if elapsed > 0 else 0
            return PhaseResult(
                name="file_io_1mb_write",
                success=True,
                duration_seconds=elapsed,
                tool_calls=1,
                details={"size_mb": 1, "throughput_mbps": round(throughput_mbps, 2)},
            )
        except Exception as e:
            return PhaseResult(
                name="file_io_1mb_write",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                error_messages=[str(e)],
            )

    async def _file_io_1mb_read(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        try:
            content = await provider.read_file(sandbox_id, "/tmp/bench-1mb.txt")
            elapsed = time.time() - t0
            size_mb = len(content) / (1024 * 1024) if content else 0
            throughput_mbps = size_mb / elapsed if elapsed > 0 else 0
            return PhaseResult(
                name="file_io_1mb_read",
                success=True,
                duration_seconds=elapsed,
                tool_calls=1,
                details={
                    "size_mb": round(size_mb, 2),
                    "throughput_mbps": round(throughput_mbps, 2),
                },
            )
        except Exception as e:
            return PhaseResult(
                name="file_io_1mb_read",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                error_messages=[str(e)],
            )

    async def _file_io_10mb(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        data = "y" * (10 * 1024 * 1024)
        try:
            t_write = time.time()
            await provider.write_file(sandbox_id, "/tmp/bench-10mb.txt", data)
            write_elapsed = time.time() - t_write

            t_read = time.time()
            content = await provider.read_file(sandbox_id, "/tmp/bench-10mb.txt")
            read_elapsed = time.time() - t_read

            total = time.time() - t0
            write_mbps = 10.0 / write_elapsed if write_elapsed > 0 else 0
            read_mbps = (len(content) / (1024 * 1024)) / read_elapsed if read_elapsed > 0 else 0

            return PhaseResult(
                name="file_io_10mb",
                success=True,
                duration_seconds=total,
                tool_calls=2,
                details={
                    "write_mbps": round(write_mbps, 2),
                    "read_mbps": round(read_mbps, 2),
                    "write_seconds": round(write_elapsed, 3),
                    "read_seconds": round(read_elapsed, 3),
                },
            )
        except Exception as e:
            return PhaseResult(
                name="file_io_10mb",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=1,
                error_messages=[str(e)],
            )

    async def _rapid_exec(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        """Run echo ok 10x in succession, measure average latency."""
        t0 = time.time()
        latencies = []
        failures = 0
        try:
            for _ in range(10):
                t_exec = time.time()
                stdout, stderr, exit_code = await provider.execute_command(
                    sandbox_id, "echo ok"
                )
                latencies.append(time.time() - t_exec)
                if exit_code != 0 or "ok" not in stdout:
                    failures += 1

            avg_latency = sum(latencies) / len(latencies) if latencies else 0
            return PhaseResult(
                name="rapid_exec",
                success=failures == 0,
                duration_seconds=time.time() - t0,
                tool_calls=10,
                friction_points=min(failures, 3),
                details={
                    "avg_latency_ms": round(avg_latency * 1000, 1),
                    "min_latency_ms": round(min(latencies) * 1000, 1) if latencies else 0,
                    "max_latency_ms": round(max(latencies) * 1000, 1) if latencies else 0,
                    "iterations": 10,
                    "failures": failures,
                },
            )
        except Exception as e:
            return PhaseResult(
                name="rapid_exec",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=len(latencies) + 1,
                friction_points=1,
                error_messages=[str(e)],
            )


register_suite(PerformanceSuite)
