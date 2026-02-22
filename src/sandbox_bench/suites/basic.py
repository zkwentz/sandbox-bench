"""Basic test suite - hello world execution and file I/O."""

import time
from typing import List

from . import PhaseResult, TestSuite, register_suite
from ..provider import SandboxProvider


class BasicSuite(TestSuite):
    """Basic sandbox operations: execute code and file I/O."""

    name = "basic"
    description = "Hello-world execution and file read/write"

    async def run(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> List[PhaseResult]:
        results = []

        # Phase: Execute hello-world
        results.append(await self._execute_hello(provider, sandbox_id))

        # Phase: File I/O
        results.append(await self._file_io(provider, sandbox_id))

        return results

    async def _execute_hello(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        tool_calls = 0
        friction = 0
        errors = 0
        error_msgs: list[str] = []
        details: dict = {}

        test_code = "print('Hello from sandbox-bench!')"
        try:
            stdout, stderr, exit_code = await provider.execute(
                sandbox_id, test_code, language="python"
            )
            tool_calls += 1
            details = {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}

            if exit_code != 0:
                error_msgs.append(f"Execute returned non-zero: {exit_code}")
                friction += 1

            if "Hello from sandbox-bench!" not in stdout:
                error_msgs.append(f"Unexpected output: {stdout}")
                friction += 1

            success = exit_code == 0 and "Hello from sandbox-bench!" in stdout
        except Exception as e:
            error_msgs.append(f"Execute failed: {e}")
            errors += 1
            success = False

        return PhaseResult(
            name="execute_hello",
            success=success,
            duration_seconds=time.time() - t0,
            tool_calls=tool_calls,
            friction_points=friction,
            errors=errors,
            error_messages=error_msgs,
            details=details,
        )

    async def _file_io(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        tool_calls = 0
        friction = 0
        errors = 0
        error_msgs: list[str] = []

        test_content = "sandbox-bench test file content"
        test_path = "/tmp/sandbox-bench-test.txt"

        try:
            await provider.write_file(sandbox_id, test_path, test_content)
            tool_calls += 1

            read_content = await provider.read_file(sandbox_id, test_path)
            tool_calls += 1

            if read_content != test_content:
                error_msgs.append(f"File content mismatch: {read_content}")
                friction += 1

            success = read_content == test_content
        except Exception as e:
            error_msgs.append(f"File I/O failed: {e}")
            friction += 1
            errors += 1
            success = False

        return PhaseResult(
            name="file_io",
            success=success,
            duration_seconds=time.time() - t0,
            tool_calls=tool_calls,
            friction_points=friction,
            errors=errors,
            error_messages=error_msgs,
        )


register_suite(BasicSuite)
