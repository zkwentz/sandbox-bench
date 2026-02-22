"""SWE-bench-style software engineering test suite."""

import time
from typing import List

from . import PhaseResult, TestSuite, register_suite
from ..provider import SandboxProvider


class SweSuite(TestSuite):
    """SWE-bench-style tests: pip install, git clone, pytest, network access."""

    name = "swe"
    description = "Network access, pip install, git clone, pytest execution"

    async def run(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> List[PhaseResult]:
        results = []
        results.append(await self._network_access(provider, sandbox_id))
        results.append(await self._pip_install(provider, sandbox_id))
        results.append(await self._git_clone(provider, sandbox_id))
        results.append(await self._pytest_run(provider, sandbox_id))
        return results

    async def _network_access(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        cmd = (
            "python3 -c \""
            "import urllib.request; "
            "print(urllib.request.urlopen('https://httpbin.org/get').status)"
            "\""
        )
        try:
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, cmd, timeout_seconds=15
            )
            success = exit_code == 0 and "200" in stdout.strip()
            return PhaseResult(
                name="network_access",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=0 if success else 1,
                capability_tested="network_access",
                capability_supported=success,
                details={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="network_access",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="network_access",
                capability_supported=False,
                error_messages=[str(e)],
            )

    async def _pip_install(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        cmd = (
            "pip install requests==2.31.0 -q && "
            "python3 -c \"import requests; print(requests.__version__)\""
        )
        try:
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, cmd, timeout_seconds=60
            )
            success = exit_code == 0 and "2.31.0" in stdout
            return PhaseResult(
                name="pip_install",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=0 if success else 1,
                capability_tested="pip_install",
                capability_supported=success,
                details={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="pip_install",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="pip_install",
                capability_supported=False,
                error_messages=[str(e)],
            )

    async def _git_clone(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        cmd = (
            "git clone --depth 1 https://github.com/pallets/flask.git "
            "/tmp/flask-test 2>&1 && test -d /tmp/flask-test/.git"
        )
        try:
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, cmd, timeout_seconds=30
            )
            success = exit_code == 0
            return PhaseResult(
                name="git_clone",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=0 if success else 1,
                capability_tested="git_clone",
                capability_supported=success,
                details={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="git_clone",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="git_clone",
                capability_supported=False,
                error_messages=[str(e)],
            )

    async def _pytest_run(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        test_code = (
            "def test_add():\n"
            "    assert 1 + 1 == 2\n"
            "\n"
            "def test_string():\n"
            "    assert 'hello'.upper() == 'HELLO'\n"
        )
        try:
            await provider.write_file(
                sandbox_id, "/tmp/test_bench.py", test_code
            )
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id,
                "pip install pytest -q 2>/dev/null; python3 -m pytest /tmp/test_bench.py -v 2>&1",
                timeout_seconds=60,
            )
            success = exit_code == 0 and "passed" in stdout
            return PhaseResult(
                name="pytest_run",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=0 if success else 1,
                capability_tested="pytest",
                capability_supported=success,
                details={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="pytest_run",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=1,
                capability_tested="pytest",
                capability_supported=False,
                error_messages=[str(e)],
            )


register_suite(SweSuite)
