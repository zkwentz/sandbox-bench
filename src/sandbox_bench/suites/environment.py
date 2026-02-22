"""Complex environment onramp test suite."""

import time
from typing import List

from . import PhaseResult, TestSuite, register_suite
from ..provider import SandboxProvider


class EnvironmentSuite(TestSuite):
    """Complex environment setup tests: Node.js, npm, project clone, multi-step
    builds, Python venv."""

    name = "environment"
    description = "Node.js setup, npm install, project clone, multi-step builds, venv"

    async def run(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> List[PhaseResult]:
        results = []
        results.append(await self._node_available(provider, sandbox_id))
        results.append(await self._npm_install(provider, sandbox_id))
        results.append(await self._project_clone(provider, sandbox_id))
        results.append(await self._multi_step_build(provider, sandbox_id))
        results.append(await self._python_venv(provider, sandbox_id))
        return results

    async def _node_available(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        try:
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, "node --version"
            )
            success = exit_code == 0 and stdout.strip().startswith("v")
            return PhaseResult(
                name="node_available",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=0 if success else 1,
                capability_tested="nodejs",
                capability_supported=success,
                details={"version": stdout.strip()},
            )
        except Exception as e:
            return PhaseResult(
                name="node_available",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="nodejs",
                capability_supported=False,
                error_messages=[str(e)],
            )

    async def _npm_install(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        package_json = '{"name":"bench-test","version":"1.0.0","dependencies":{"express":"^4.18.0"}}'
        try:
            await provider.write_file(
                sandbox_id, "/tmp/npm-test/package.json", package_json
            )
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id,
                "cd /tmp/npm-test && npm install 2>&1",
                timeout_seconds=60,
            )
            success = exit_code == 0
            return PhaseResult(
                name="npm_install",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=0 if success else 1,
                capability_tested="npm",
                capability_supported=success,
                details={"stdout": stdout[:500], "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="npm_install",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=1,
                capability_tested="npm",
                capability_supported=False,
                error_messages=[str(e)],
            )

    async def _project_clone(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        cmd = (
            "git clone --depth 1 https://github.com/expressjs/express.git "
            "/tmp/express-test 2>&1"
        )
        try:
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, cmd, timeout_seconds=30
            )
            success = exit_code == 0
            return PhaseResult(
                name="project_clone",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=0 if success else 1,
                capability_tested="project_clone",
                capability_supported=success,
                details={"stdout": stdout[:500], "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="project_clone",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="project_clone",
                capability_supported=False,
                error_messages=[str(e)],
            )

    async def _multi_step_build(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        cmd = "cd /tmp/express-test && npm install 2>&1 | tail -5 && npm test 2>&1 | head -20"
        try:
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, cmd, timeout_seconds=120
            )
            # Success if npm install completes (exit 0 or test output present)
            success = exit_code == 0 or "passing" in stdout.lower()
            return PhaseResult(
                name="multi_step_build",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=0 if success else 1,
                capability_tested="multi_step_build",
                capability_supported=success,
                details={"stdout": stdout[:500], "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="multi_step_build",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="multi_step_build",
                capability_supported=False,
                error_messages=[str(e)],
            )

    async def _python_venv(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        cmd = (
            "python3 -m venv /tmp/test-venv && "
            "/tmp/test-venv/bin/pip install flask -q 2>&1 && "
            "/tmp/test-venv/bin/python -c \"import flask; print(flask.__version__)\""
        )
        try:
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, cmd, timeout_seconds=60
            )
            success = exit_code == 0
            return PhaseResult(
                name="python_venv",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=0 if success else 1,
                capability_tested="python_venv",
                capability_supported=success,
                details={"stdout": stdout, "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="python_venv",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="python_venv",
                capability_supported=False,
                error_messages=[str(e)],
            )


register_suite(EnvironmentSuite)
