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
import os
import random
import resource
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from . import PhaseResult, TestSuite, register_suite
from ..provider import SandboxProvider


def _log(msg: str) -> None:
    """Flush-safe progress log to stderr."""
    print(f"  [training_batch] {msg}", file=sys.stderr, flush=True)


def _tune_system_limits() -> None:
    """Best-effort raise of OS resource limits before high-concurrency work.

    Prevents client-side bottlenecks (inotify watches, open file descriptors)
    from polluting benchmark results.  Silently no-ops on macOS / non-root.
    """
    # --- Open file descriptors (ulimit -n) ---
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = min(hard, 65_536)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            _log(f"Raised NOFILE limit: {soft} → {target}")
    except Exception:
        pass

    # --- inotify watches & instances (Linux /proc/sys) ---
    _inotify_targets = [
        ("/proc/sys/fs/inotify/max_user_watches", "524288"),
        ("/proc/sys/fs/inotify/max_user_instances", "8192"),
    ]
    for path, target_val in _inotify_targets:
        try:
            with open(path) as f:
                current = int(f.read().strip())
            if current < int(target_val):
                with open(path, "w") as f:
                    f.write(target_val)
                _log(f"Raised {path}: {current} → {target_val}")
        except Exception:
            pass  # Not Linux, no permissions, etc.


# Tier definitions: (name, batch_size, capability_name)
ALL_TIERS = [
    ("tier_1_256", 256, "batch_256"),
    ("tier_2_1024", 1_024, "batch_1024"),
    ("tier_3_8192", 8_192, "batch_8192"),
    ("tier_4_65536", 65_536, "batch_65536"),
    ("tier_5_262144", 262_144, "batch_262144"),
]

# TRAINING_BATCH_MAX_TIER env var limits how many tiers to run (1-5, default all)
_max_tier = int(os.environ.get("TRAINING_BATCH_MAX_TIER", len(ALL_TIERS)))
TIERS = ALL_TIERS[:max(1, min(_max_tier, len(ALL_TIERS)))]

TIER_TIMEOUT = 600  # 10 minutes per tier
CREATE_TIMEOUT = 60  # 60s per individual create
WORKER_POOL_SIZE = 500  # concurrent worker coroutines per phase
CASCADE_THRESHOLD = 0.50  # skip remaining tiers if < 50% success
RESOURCE_RETRY_MAX = 5  # retries on resource-limit / rate-limit errors
RESOURCE_RETRY_BASE = 2.0  # base backoff seconds (doubles each attempt)


def _classify_error(e: Exception) -> str:
    """Classify an exception into a failure mode bucket."""
    msg = str(e).lower()
    # Resource exhaustion (inotify, file descriptors, memory, etc.)
    if any(tok in msg for tok in ("inotify", "too many open files", "emfile", "enfile", "no space left")):
        return "resource_limit"
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

        _log(f"Starting training_batch suite ({len(TIERS)} tiers)")
        _tune_system_limits()
        for i, (tier_name, batch_size, capability) in enumerate(TIERS, 1):
            if cascade_failed:
                _log(f"Tier {i}/{len(TIERS)} {tier_name}: SKIPPED (cascade)")
                results.append(PhaseResult(
                    name=tier_name,
                    success=False,
                    duration_seconds=0.0,
                    capability_tested=capability,
                    capability_supported=False,
                    details={"skipped": True, "reason": "cascade_skip"},
                ))
                continue

            _log(f"Tier {i}/{len(TIERS)} {tier_name}: starting ({batch_size} sandboxes)")
            result = await self._run_tier(
                provider, tier_name, batch_size, capability
            )
            results.append(result)

            # Check cascade rule
            created = result.details.get("created", 0)
            requested = result.details.get("requested", 0)
            rate = created / requested if requested > 0 else 0
            _log(
                f"Tier {i}/{len(TIERS)} {tier_name}: "
                f"{'PASS' if result.success else 'FAIL'} "
                f"({created}/{requested} created, {rate:.0%} success, "
                f"{result.duration_seconds:.1f}s)"
            )
            if requested > 0 and rate < CASCADE_THRESHOLD:
                cascade_failed = True
                _log(f"Cascade threshold not met ({rate:.0%} < {CASCADE_THRESHOLD:.0%}), skipping remaining tiers")

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
        _last_log_count = [0]  # mutable ref for progress logging

        async def _progress_logger():
            """Periodically log create phase progress."""
            while True:
                await asyncio.sleep(5)
                done = len(created_ids) + failed_count
                if done != _last_log_count[0]:
                    _last_log_count[0] = done
                    elapsed = time.time() - t_create
                    _log(
                        f"  CREATE: {len(created_ids)} ok + {failed_count} fail "
                        f"= {done}/{batch_size} ({elapsed:.0f}s elapsed)"
                    )

        _retried_count = [0]  # track retries for logging
        _backoff_total = [0.0]  # total seconds spent in retry backoff
        _provider_times: List[float] = []  # per-create provider call durations (seconds)

        async def _create_worker():
            nonlocal create_remaining, failed_count
            while create_remaining > 0:
                create_remaining -= 1
                for attempt in range(RESOURCE_RETRY_MAX + 1):
                    try:
                        t0 = time.monotonic()
                        sid = await asyncio.wait_for(
                            provider.create_sandbox(timeout_seconds=CREATE_TIMEOUT),
                            timeout=CREATE_TIMEOUT,
                        )
                        _provider_times.append(time.monotonic() - t0)
                        created_ids.append(sid)
                        break
                    except Exception as e:
                        mode = _classify_error(e)
                        if mode in ("resource_limit", "rate_limit") and attempt < RESOURCE_RETRY_MAX:
                            # Back off with jitter, letting in-flight creates
                            # finish and release system resources (inotify watches, fds)
                            backoff = min(RESOURCE_RETRY_BASE * (2 ** attempt) + random.uniform(0, 1), 30)
                            _retried_count[0] += 1
                            _backoff_total[0] += backoff
                            await asyncio.sleep(backoff)
                            continue
                        # Terminal failure — record and move on
                        failed_count += 1
                        failure_modes[mode] += 1
                        msg = str(e)
                        if len(failure_samples) < 5 and msg not in failure_samples:
                            failure_samples.append(msg)
                        break

        num_create_workers = min(WORKER_POOL_SIZE, batch_size)
        _log(f"  CREATE phase: {batch_size} sandboxes, {num_create_workers} workers")
        t_create = time.time()
        progress_task = asyncio.create_task(_progress_logger())
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
            _log(f"  CREATE phase: TIMED OUT after {TIER_TIMEOUT}s")
        finally:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass
        create_duration = time.time() - t_create
        retried = _retried_count[0]
        _log(
            f"  CREATE done: {len(created_ids)} ok, {failed_count} fail "
            f"in {create_duration:.1f}s "
            f"({len(created_ids)/create_duration:.1f} sandboxes/s)"
            + (f" [{retried} retries]" if retried else "")
            if create_duration > 0 else
            f"  CREATE done: {len(created_ids)} ok, {failed_count} fail"
        )
        if failure_modes:
            _log(f"  Failure modes: {dict(failure_modes)}")

        # --- Verify phase (worker pool) ---
        ready_count = 0
        verify_idx = 0  # index into created_ids
        _verify_last_log = [0]

        async def _verify_progress_logger():
            while True:
                await asyncio.sleep(5)
                done = ready_count + (verify_idx - ready_count)
                if done != _verify_last_log[0]:
                    _verify_last_log[0] = done
                    elapsed = time.time() - t_verify
                    _log(
                        f"  VERIFY: {ready_count}/{verify_idx} ready "
                        f"of {len(created_ids)} ({elapsed:.0f}s elapsed)"
                    )

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
        _log(f"  VERIFY phase: {len(created_ids)} sandboxes, {num_verify_workers} workers")
        t_verify = time.time()
        if created_ids:
            verify_progress = asyncio.create_task(_verify_progress_logger())
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *[_verify_worker() for _ in range(num_verify_workers)],
                        return_exceptions=True,
                    ),
                    timeout=TIER_TIMEOUT,
                )
            except asyncio.TimeoutError:
                _log(f"  VERIFY phase: TIMED OUT after {TIER_TIMEOUT}s")
            finally:
                verify_progress.cancel()
                try:
                    await verify_progress
                except asyncio.CancelledError:
                    pass
        verify_duration = time.time() - t_verify
        _log(f"  VERIFY done: {ready_count}/{len(created_ids)} ready in {verify_duration:.1f}s")

        # --- Destroy phase (worker pool) ---
        _log(f"  DESTROY phase: {len(created_ids)} sandboxes")
        t_destroy = time.time()
        await self._cleanup_pool(provider, created_ids)
        destroy_duration = time.time() - t_destroy
        _log(f"  DESTROY done: {len(created_ids)} in {destroy_duration:.1f}s")

        total_duration = time.time() - t_tier

        # Compute metrics
        created = len(created_ids)
        throughput = created / create_duration if create_duration > 0 else 0.0
        success_rate = created / batch_size if batch_size > 0 else 0.0
        ready_rate = ready_count / batch_size if batch_size > 0 else 0.0

        # Provider-only throughput: exclude client-side retry backoff from the
        # create window so benchmark results reflect provider performance, not
        # client resource limits (inotify, fd exhaustion, etc.).
        backoff_secs = _backoff_total[0]
        effective_create = max(create_duration - backoff_secs, 0.001)
        provider_throughput = created / effective_create if created > 0 else 0.0
        avg_provider_create = (
            sum(_provider_times) / len(_provider_times)
            if _provider_times else 0.0
        )

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
                "provider_throughput_per_sec": round(provider_throughput, 2),
                "avg_provider_create_seconds": round(avg_provider_create, 4),
                "client_backoff_seconds": round(backoff_secs, 3),
                "worker_pool_size": num_create_workers,
                "resource_retries": _retried_count[0],
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
