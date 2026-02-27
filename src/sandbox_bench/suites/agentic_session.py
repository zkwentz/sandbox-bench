"""Agentic session (snapshot/restore) test suite.

Benchmarks the full lifecycle of a long-running agentic session:
create a sandbox, load it with significant state (2GiB disk + 2GiB RAM),
snapshot it, destroy the original, then restore from the snapshot in a
new session.  This measures snapshot fidelity and latency — critical for
providers claiming to support session persistence.
"""

import time
from typing import List, Optional

from . import PhaseResult, TestSuite, register_suite
from ..provider import SandboxProvider


class AgenticSessionSuite(TestSuite):
    """Agentic session snapshot/restore benchmarks."""

    name = "agentic_session"
    description = "Snapshot/restore lifecycle: 2GiB disk + 2GiB RAM state"

    async def run(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> List[PhaseResult]:
        results: List[PhaseResult] = []
        wall_start = time.time()

        # State shared across phases
        expected_checksum: Optional[str] = None
        snapshot_id: Optional[str] = None
        new_sandbox_id: Optional[str] = None
        snapshot_supported = True

        # ---- Phase 1: provision_and_load ----
        result, expected_checksum = await self._phase_provision_and_load(
            provider, sandbox_id
        )
        results.append(result)
        if not result.success:
            # Cannot continue if load failed
            results.extend(self._skip_remaining(
                ["snapshot", "destroy_and_restore", "verify_restore"],
                ["session_snapshot", "session_restore", "session_verify"],
                "load_failed",
            ))
            return results

        # ---- Phase 2: snapshot ----
        result, snapshot_id, snapshot_supported = await self._phase_snapshot(
            provider, sandbox_id
        )
        results.append(result)
        if not snapshot_supported:
            results.extend(self._skip_remaining(
                ["destroy_and_restore", "verify_restore"],
                ["session_restore", "session_verify"],
                "snapshot_not_supported",
            ))
            return results
        if not result.success:
            results.extend(self._skip_remaining(
                ["destroy_and_restore", "verify_restore"],
                ["session_restore", "session_verify"],
                "snapshot_failed",
            ))
            return results

        # ---- Phase 3: destroy_and_restore ----
        result, new_sandbox_id = await self._phase_destroy_and_restore(
            provider, sandbox_id, snapshot_id  # type: ignore[arg-type]
        )
        results.append(result)
        if not result.success:
            results.extend(self._skip_remaining(
                ["verify_restore"],
                ["session_verify"],
                "restore_failed",
            ))
            # Clean up restored sandbox if it was created
            if new_sandbox_id:
                await self._safe_destroy(provider, new_sandbox_id)
            return results

        # ---- Phase 4: verify_restore ----
        result = await self._phase_verify_restore(
            provider,
            new_sandbox_id,  # type: ignore[arg-type]
            expected_checksum,  # type: ignore[arg-type]
            wall_start,
        )
        results.append(result)

        # Clean up the restored sandbox
        if new_sandbox_id:
            await self._safe_destroy(provider, new_sandbox_id)

        return results

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    async def _phase_provision_and_load(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> tuple[PhaseResult, Optional[str]]:
        """Phase 1: Write 2GiB file, compute checksum, allocate 2GiB RAM."""
        t0 = time.time()
        errors: List[str] = []
        expected_checksum: Optional[str] = None
        details: dict = {}

        try:
            # Write 2GiB file
            t_disk = time.time()
            stdout, stderr, rc = await provider.execute_command(
                sandbox_id,
                "dd if=/dev/urandom of=/tmp/state-2g bs=1M count=2048 2>&1",
                timeout_seconds=120,
            )
            details["disk_write_duration_seconds"] = round(time.time() - t_disk, 3)
            if rc != 0:
                errors.append(f"dd failed (rc={rc}): {stderr or stdout}")

            # Compute checksum
            stdout, stderr, rc = await provider.execute_command(
                sandbox_id,
                "md5sum /tmp/state-2g",
                timeout_seconds=60,
            )
            if rc == 0 and stdout.strip():
                expected_checksum = stdout.strip().split()[0]
                details["checksum"] = expected_checksum
            else:
                errors.append(f"md5sum failed (rc={rc}): {stderr or stdout}")

            # Allocate 2GiB RAM via background process
            t_ram = time.time()
            stdout, stderr, rc = await provider.execute_command(
                sandbox_id,
                (
                    "nohup python3 -c \""
                    "d = bytearray(2 * 1024 * 1024 * 1024)\\n"
                    "import time; time.sleep(3600)"
                    "\" > /dev/null 2>&1 &"
                    " echo $!"
                ),
                timeout_seconds=60,
            )
            details["ram_alloc_duration_seconds"] = round(time.time() - t_ram, 3)
            if rc != 0:
                errors.append(f"RAM alloc failed (rc={rc}): {stderr or stdout}")
            else:
                details["ram_pid"] = stdout.strip()

            # Verify memory usage
            stdout, stderr, rc = await provider.execute_command(
                sandbox_id,
                "cat /proc/meminfo | head -5",
                timeout_seconds=10,
            )
            if rc == 0:
                details["meminfo_snippet"] = stdout.strip()

        except Exception as e:
            errors.append(str(e))

        duration = time.time() - t0
        success = len(errors) == 0 and expected_checksum is not None
        details["load_duration_seconds"] = round(duration, 3)

        return PhaseResult(
            name="provision_and_load",
            success=success,
            duration_seconds=duration,
            tool_calls=4,
            errors=len(errors),
            error_messages=errors,
            capability_tested="session_load",
            capability_supported=success,
            details=details,
        ), expected_checksum

    async def _phase_snapshot(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> tuple[PhaseResult, Optional[str], bool]:
        """Phase 2: Snapshot the sandbox."""
        t0 = time.time()
        snapshot_id: Optional[str] = None
        supported = True
        errors: List[str] = []
        details: dict = {}

        try:
            snapshot_id = await provider.snapshot(sandbox_id)
            details["snapshot_id"] = snapshot_id
        except NotImplementedError as e:
            supported = False
            errors.append(str(e))
            details["skipped"] = True
            details["reason"] = "snapshot_not_supported"
        except Exception as e:
            errors.append(str(e))

        duration = time.time() - t0
        details["snapshot_duration_seconds"] = round(duration, 3)
        success = snapshot_id is not None

        return PhaseResult(
            name="snapshot",
            success=success,
            duration_seconds=duration,
            tool_calls=1 if supported else 0,
            errors=len(errors),
            error_messages=errors,
            capability_tested="session_snapshot",
            capability_supported=success,
            details=details,
        ), snapshot_id, supported

    async def _phase_destroy_and_restore(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
        snapshot_id: str,
    ) -> tuple[PhaseResult, Optional[str]]:
        """Phase 3: Destroy original sandbox and restore from snapshot."""
        t0 = time.time()
        new_sandbox_id: Optional[str] = None
        errors: List[str] = []
        details: dict = {}

        # Destroy the original
        t_destroy = time.time()
        try:
            await provider.destroy(sandbox_id)
        except Exception as e:
            errors.append(f"destroy failed: {e}")
        details["destroy_duration_seconds"] = round(time.time() - t_destroy, 3)

        # Restore from snapshot
        t_restore = time.time()
        try:
            new_sandbox_id = await provider.restore(snapshot_id)
            details["new_sandbox_id"] = new_sandbox_id
        except Exception as e:
            errors.append(f"restore failed: {e}")
        details["restore_duration_seconds"] = round(time.time() - t_restore, 3)

        duration = time.time() - t0
        success = new_sandbox_id is not None

        return PhaseResult(
            name="destroy_and_restore",
            success=success,
            duration_seconds=duration,
            tool_calls=2,
            errors=len(errors),
            error_messages=errors,
            capability_tested="session_restore",
            capability_supported=success,
            details=details,
        ), new_sandbox_id

    async def _phase_verify_restore(
        self,
        provider: SandboxProvider,
        new_sandbox_id: str,
        expected_checksum: str,
        wall_start: float,
    ) -> PhaseResult:
        """Phase 4: Verify the restored sandbox has the same state."""
        t0 = time.time()
        errors: List[str] = []
        details: dict = {}
        checksum_match = False
        ram_survived = False

        try:
            # Verify disk state via checksum
            stdout, stderr, rc = await provider.execute_command(
                new_sandbox_id,
                "md5sum /tmp/state-2g",
                timeout_seconds=60,
            )
            if rc == 0 and stdout.strip():
                actual_checksum = stdout.strip().split()[0]
                details["actual_checksum"] = actual_checksum
                details["expected_checksum"] = expected_checksum
                checksum_match = actual_checksum == expected_checksum
            else:
                errors.append(f"md5sum on restored sandbox failed (rc={rc}): {stderr or stdout}")

            # Best-effort check if RAM allocation process survived
            stdout, stderr, rc = await provider.execute_command(
                new_sandbox_id,
                "pgrep -f bytearray || echo 'not_found'",
                timeout_seconds=10,
            )
            if rc == 0 and "not_found" not in stdout:
                ram_survived = True

        except Exception as e:
            errors.append(str(e))

        duration = time.time() - t0
        wall_time = time.time() - wall_start
        details["checksum_match"] = checksum_match
        details["ram_survived"] = ram_survived
        details["verify_duration_seconds"] = round(duration, 3)
        details["wall_time_seconds"] = round(wall_time, 3)

        success = checksum_match

        return PhaseResult(
            name="verify_restore",
            success=success,
            duration_seconds=duration,
            tool_calls=2,
            errors=len(errors),
            error_messages=errors,
            capability_tested="session_verify",
            capability_supported=success,
            details=details,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _skip_remaining(
        self,
        phase_names: List[str],
        capabilities: List[str],
        reason: str,
    ) -> List[PhaseResult]:
        """Generate skipped PhaseResults for remaining phases."""
        return [
            PhaseResult(
                name=name,
                success=False,
                duration_seconds=0.0,
                capability_tested=cap,
                capability_supported=False,
                details={"skipped": True, "reason": reason},
            )
            for name, cap in zip(phase_names, capabilities)
        ]

    async def _safe_destroy(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> None:
        """Destroy a sandbox, swallowing errors."""
        try:
            await provider.destroy(sandbox_id)
        except Exception:
            pass


register_suite(AgenticSessionSuite)
