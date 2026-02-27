"""Training batch concurrency test suite.

Measures how providers handle concurrent sandbox provisioning at scale,
emulating a training batch job where N sandboxes spin up simultaneously.

Uses a worker-pool pattern: a fixed number of long-lived worker coroutines
each loop and grab the next unit of work.  This keeps all work concurrent
(no sequential waves) while bounding memory to O(pool_size) instead of
O(batch_size) — critical at 65K+ sandboxes where creating one coroutine
per lease would exhaust Python memory.
"""

import asyncio
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from . import PhaseResult, TestSuite, register_suite
from ..provider import SandboxProvider

# Tier definitions: (name, batch_size, capability_name)
TIERS = [
    ("tier_1_256", 256, "batch_256"),
    ("tier_2_1024", 1_024, "batch_1024"),
    ("tier_3_8192", 8_192, "batch_8192"),
    ("tier_4_65536", 65_536, "batch_65536"),
    ("tier_5_262144", 262_144, "batch_262144"),
]

TIER_TIMEOUT = 600  # 10 minutes per tier
CREATE_TIMEOUT = 60  # 60s per individual create
WORKER_POOL_SIZE = 500  # concurrent worker coroutines per phase
CASCADE_THRESHOLD = 0.50  # skip remaining tiers if < 50% success


def _classify_error(e: Exception) -> str:
    """Classify an exception into a failure mode bucket."""
    msg = str(e).lower()
    if "rate" in msg and "limit" in msg:
        return "rate_limit"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "quota" in msg or "capacity" in msg:
        return "quota_exceeded"
    if "auth" in msg or "401" in msg or "403" in msg:
        return "auth_error"
    if "429" in msg:
        return "rate_limit"
    if "503" in msg or "502" in msg or "500" in msg:
        return "server_error"
    return "unknown"


class TrainingBatchSuite(TestSuite):
    """Training batch concurrency benchmarks: concurrent sandbox provisioning at scale."""

    name = "training_batch"
    description = "Concurrent sandbox provisioning at scale (256 to 262K)"

    async def run(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> List[PhaseResult]:
        results = []
        cascade_failed = False

        for tier_name, batch_size, capability in TIERS:
            if cascade_failed:
                results.append(PhaseResult(
                    name=tier_name,
                    success=False,
                    duration_seconds=0.0,
                    capability_tested=capability,
                    capability_supported=False,
                    details={"skipped": True, "reason": "cascade_skip"},
                ))
                continue

            result = await self._run_tier(
                provider, tier_name, batch_size, capability
            )
            results.append(result)

            # Check cascade rule
            created = result.details.get("created", 0)
            requested = result.details.get("requested", 0)
            if requested > 0 and created / requested < CASCADE_THRESHOLD:
                cascade_failed = True

        return results

    async def _run_tier(
        self,
        provider: SandboxProvider,
        tier_name: str,
        batch_size: int,
        capability: str,
    ) -> PhaseResult:
        """Run a single tier: create N sandboxes concurrently, verify, destroy.

        Uses a fixed-size worker pool so all batch_size leases are issued
        concurrently (up to WORKER_POOL_SIZE in-flight at once) without
        allocating one coroutine per lease.
        """
        created_ids: List[str] = []
        failure_modes: Dict[str, int] = defaultdict(int)
        failure_samples: List[str] = []
        failed_count = 0

        t_tier = time.time()

        # --- Create phase (worker pool) ---
        # Shared mutable counter — safe because asyncio is single-threaded;
        # no two workers execute between the same pair of await points.
        create_remaining = batch_size

        async def _create_worker():
            nonlocal create_remaining, failed_count
            while create_remaining > 0:
                create_remaining -= 1
                try:
                    sid = await asyncio.wait_for(
                        provider.create_sandbox(timeout_seconds=CREATE_TIMEOUT),
                        timeout=CREATE_TIMEOUT,
                    )
                    created_ids.append(sid)
                except Exception as e:
                    failed_count += 1
                    mode = _classify_error(e)
                    failure_modes[mode] += 1
                    msg = str(e)
                    if len(failure_samples) < 5 and msg not in failure_samples:
                        failure_samples.append(msg)

        num_create_workers = min(WORKER_POOL_SIZE, batch_size)
        t_create = time.time()
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *[_create_worker() for _ in range(num_create_workers)],
                    return_exceptions=True,
                ),
                timeout=TIER_TIMEOUT,
            )
        except asyncio.TimeoutError:
            # Whatever hasn't been claimed yet counts as timed out
            timed_out_count = max(0, create_remaining)
            failure_modes["tier_timeout"] += timed_out_count
            failed_count += timed_out_count
            if len(failure_samples) < 5:
                failure_samples.append(f"Tier timeout after {TIER_TIMEOUT}s")
        create_duration = time.time() - t_create

        # --- Verify phase (worker pool) ---
        ready_count = 0
        verify_idx = 0  # index into created_ids

        async def _verify_worker():
            nonlocal verify_idx, ready_count
            while verify_idx < len(created_ids):
                idx = verify_idx
                verify_idx += 1
                sid = created_ids[idx]
                try:
                    stdout, stderr, exit_code = await asyncio.wait_for(
                        provider.execute_command(sid, "echo ready"),
                        timeout=30,
                    )
                    if exit_code == 0 and "ready" in stdout:
                        ready_count += 1
                except Exception:
                    pass

        num_verify_workers = min(WORKER_POOL_SIZE, len(created_ids))
        t_verify = time.time()
        if created_ids:
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *[_verify_worker() for _ in range(num_verify_workers)],
                        return_exceptions=True,
                    ),
                    timeout=TIER_TIMEOUT,
                )
            except asyncio.TimeoutError:
                pass  # ready_count stays at whatever we got
        verify_duration = time.time() - t_verify

        # --- Destroy phase (worker pool) ---
        t_destroy = time.time()
        await self._cleanup_pool(provider, created_ids)
        destroy_duration = time.time() - t_destroy

        total_duration = time.time() - t_tier

        # Compute metrics
        created = len(created_ids)
        throughput = created / create_duration if create_duration > 0 else 0.0
        success_rate = created / batch_size if batch_size > 0 else 0.0
        ready_rate = ready_count / batch_size if batch_size > 0 else 0.0

        tier_success = success_rate >= CASCADE_THRESHOLD and ready_rate >= CASCADE_THRESHOLD
        all_perfect = created == batch_size and ready_count == batch_size

        return PhaseResult(
            name=tier_name,
            success=tier_success,
            duration_seconds=total_duration,
            tool_calls=created + ready_count + created,  # create + verify + destroy
            friction_points=min(failed_count, 3),
            capability_tested=capability,
            capability_supported=all_perfect,
            details={
                "requested": batch_size,
                "created": created,
                "ready": ready_count,
                "failed": failed_count,
                "failure_modes": dict(failure_modes),
                "failure_samples": failure_samples,
                "create_duration_seconds": round(create_duration, 3),
                "verify_duration_seconds": round(verify_duration, 3),
                "destroy_duration_seconds": round(destroy_duration, 3),
                "throughput_sandboxes_per_sec": round(throughput, 2),
                "worker_pool_size": num_create_workers,
            },
        )

    async def _cleanup_pool(
        self,
        provider: SandboxProvider,
        sandbox_ids: List[str],
    ) -> None:
        """Destroy all sandboxes using a worker pool, swallowing errors."""
        if not sandbox_ids:
            return

        destroy_idx = 0

        async def _destroy_worker():
            nonlocal destroy_idx
            while destroy_idx < len(sandbox_ids):
                idx = destroy_idx
                destroy_idx += 1
                try:
                    await asyncio.wait_for(
                        provider.destroy(sandbox_ids[idx]),
                        timeout=30,
                    )
                except Exception:
                    pass

        num_workers = min(WORKER_POOL_SIZE, len(sandbox_ids))
        await asyncio.gather(
            *[_destroy_worker() for _ in range(num_workers)],
            return_exceptions=True,
        )


register_suite(TrainingBatchSuite)
