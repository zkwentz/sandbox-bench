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
        """Run a single tier: create N sandboxes concurrently, verify, destroy."""
        sem = asyncio.Semaphore(CONCURRENCY_LIMIT)
        created_ids: List[str] = []
        failure_modes: Dict[str, int] = defaultdict(int)
        failure_samples: List[str] = []
        failed_count = 0

        t_tier = time.time()

        # --- Create phase ---
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
        try:
            create_results = await asyncio.wait_for(
                asyncio.gather(*[_create_one() for _ in range(batch_size)],
                               return_exceptions=True),
                timeout=TIER_TIMEOUT,
            )
        except asyncio.TimeoutError:
            # Tier-level timeout
            create_results = []
            failure_modes["tier_timeout"] += batch_size
            if len(failure_samples) < 5:
                failure_samples.append(f"Tier timeout after {TIER_TIMEOUT}s")

        create_duration = time.time() - t_create

        for res in create_results:
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
                # None or unexpected
                failed_count += 1
                failure_modes["unknown"] += 1

        # --- Verify phase ---
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
        if created_ids:
            try:
                verify_results = await asyncio.wait_for(
                    asyncio.gather(
                        *[_verify_one(sid) for sid in created_ids],
                        return_exceptions=True,
                    ),
                    timeout=TIER_TIMEOUT,
                )
                for vr in verify_results:
                    if vr is True:
                        ready_count += 1
            except asyncio.TimeoutError:
                pass  # ready_count stays at whatever we got

        verify_duration = time.time() - t_verify

        # --- Destroy phase ---
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
            },
        )

    async def _cleanup_batch(
        self,
        provider: SandboxProvider,
        sandbox_ids: List[str],
        sem: asyncio.Semaphore,
    ) -> None:
        """Destroy all sandboxes in parallel, swallowing errors."""
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

        await asyncio.gather(
            *[_destroy_one(sid) for sid in sandbox_ids],
            return_exceptions=True,
        )


register_suite(TrainingBatchSuite)
