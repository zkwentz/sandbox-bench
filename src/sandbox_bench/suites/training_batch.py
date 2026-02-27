"""Training batch concurrency test suite.

Measures how providers handle concurrent sandbox provisioning at scale,
emulating a training batch job where N sandboxes spin up simultaneously.
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
CONCURRENCY_LIMIT = 100  # semaphore to prevent local resource exhaustion
CASCADE_THRESHOLD = 0.50  # skip remaining tiers if < 50% success
WORKER_BATCH_SIZE = 500  # max leases per wave to avoid Python memory exhaustion


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

        Work is broken into waves of WORKER_BATCH_SIZE to avoid creating
        hundreds of thousands of coroutines in a single asyncio.gather call,
        which would exhaust Python memory.
        """
        sem = asyncio.Semaphore(CONCURRENCY_LIMIT)
        created_ids: List[str] = []
        failure_modes: Dict[str, int] = defaultdict(int)
        failure_samples: List[str] = []
        failed_count = 0

        t_tier = time.time()
        tier_deadline = t_tier + TIER_TIMEOUT

        # --- Create phase (in waves) ---
        async def _create_one() -> Optional[str]:
            async with sem:
                try:
                    sid = await asyncio.wait_for(
                        provider.create_sandbox(timeout_seconds=CREATE_TIMEOUT),
                        timeout=CREATE_TIMEOUT,
                    )
                    return sid
                except Exception as e:
                    return e

        t_create = time.time()
        remaining = batch_size
        timed_out = False
        while remaining > 0 and not timed_out:
            wave_size = min(remaining, WORKER_BATCH_SIZE)
            remaining -= wave_size

            wave_timeout = max(1, tier_deadline - time.time())
            try:
                wave_results = await asyncio.wait_for(
                    asyncio.gather(
                        *[_create_one() for _ in range(wave_size)],
                        return_exceptions=True,
                    ),
                    timeout=wave_timeout,
                )
            except asyncio.TimeoutError:
                timed_out = True
                # Count remaining in this wave + all future waves as timed out
                failure_modes["tier_timeout"] += wave_size + remaining
                failed_count += wave_size + remaining
                remaining = 0
                if len(failure_samples) < 5:
                    failure_samples.append(f"Tier timeout after {TIER_TIMEOUT}s")
                break

            for res in wave_results:
                if isinstance(res, str):
                    created_ids.append(res)
                elif isinstance(res, Exception):
                    failed_count += 1
                    mode = _classify_error(res)
                    failure_modes[mode] += 1
                    msg = str(res)
                    if len(failure_samples) < 5 and msg not in failure_samples:
                        failure_samples.append(msg)
                else:
                    failed_count += 1
                    failure_modes["unknown"] += 1

        create_duration = time.time() - t_create

        # --- Verify phase (in waves) ---
        ready_count = 0

        async def _verify_one(sid: str) -> bool:
            async with sem:
                try:
                    stdout, stderr, exit_code = await asyncio.wait_for(
                        provider.execute_command(sid, "echo ready"),
                        timeout=30,
                    )
                    return exit_code == 0 and "ready" in stdout
                except Exception:
                    return False

        t_verify = time.time()
        verify_deadline = time.time() + TIER_TIMEOUT
        ids_to_verify = list(created_ids)
        while ids_to_verify:
            wave = ids_to_verify[:WORKER_BATCH_SIZE]
            ids_to_verify = ids_to_verify[WORKER_BATCH_SIZE:]

            wave_timeout = max(1, verify_deadline - time.time())
            try:
                verify_results = await asyncio.wait_for(
                    asyncio.gather(
                        *[_verify_one(sid) for sid in wave],
                        return_exceptions=True,
                    ),
                    timeout=wave_timeout,
                )
                for vr in verify_results:
                    if vr is True:
                        ready_count += 1
            except asyncio.TimeoutError:
                break  # ready_count stays at whatever we got

        verify_duration = time.time() - t_verify

        # --- Destroy phase (in waves) ---
        t_destroy = time.time()
        await self._cleanup_batch(provider, created_ids, sem)
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
                "worker_batch_size": WORKER_BATCH_SIZE,
                "waves_needed": (batch_size + WORKER_BATCH_SIZE - 1) // WORKER_BATCH_SIZE,
            },
        )

    async def _cleanup_batch(
        self,
        provider: SandboxProvider,
        sandbox_ids: List[str],
        sem: asyncio.Semaphore,
    ) -> None:
        """Destroy all sandboxes in waves, swallowing errors."""
        if not sandbox_ids:
            return

        async def _destroy_one(sid: str) -> None:
            async with sem:
                try:
                    await asyncio.wait_for(
                        provider.destroy(sid),
                        timeout=30,
                    )
                except Exception:
                    pass

        remaining = list(sandbox_ids)
        while remaining:
            wave = remaining[:WORKER_BATCH_SIZE]
            remaining = remaining[WORKER_BATCH_SIZE:]
            await asyncio.gather(
                *[_destroy_one(sid) for sid in wave],
                return_exceptions=True,
            )


register_suite(TrainingBatchSuite)
