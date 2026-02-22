"""Competitive programming (Baekjoon-style) test suite."""

import time
from typing import List

from . import PhaseResult, TestSuite, register_suite
from ..provider import SandboxProvider


class CompetitiveSuite(TestSuite):
    """Baekjoon-style competitive programming tests: stdin piping,
    compilation, time limits."""

    name = "competitive"
    description = "Stdin piping, GCC/G++ compilation, exec timeout, Python version"

    async def run(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> List[PhaseResult]:
        results = []
        results.append(await self._stdin_piping(provider, sandbox_id))
        results.append(await self._gcc_compilation(provider, sandbox_id))
        results.append(await self._cpp_compilation(provider, sandbox_id))
        results.append(await self._exec_timeout(provider, sandbox_id))
        results.append(await self._python_version(provider, sandbox_id))
        return results

    async def _stdin_piping(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        cmd = "echo '3 5' | python3 -c \"a,b=map(int,input().split()); print(a+b)\""
        try:
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, cmd
            )
            success = exit_code == 0 and "8" in stdout.strip()
            friction = 0 if success else 1
            return PhaseResult(
                name="stdin_piping",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=friction,
                capability_tested="stdin_piping",
                capability_supported=success,
                details={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="stdin_piping",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="stdin_piping",
                capability_supported=False,
                error_messages=[str(e)],
            )

    async def _gcc_compilation(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        c_code = '#include <stdio.h>\nint main() { printf("hello-c\\n"); return 0; }'
        try:
            await provider.write_file(sandbox_id, "/tmp/hello.c", c_code)
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id,
                "gcc /tmp/hello.c -o /tmp/hello_c && /tmp/hello_c",
            )
            success = exit_code == 0 and "hello-c" in stdout
            return PhaseResult(
                name="gcc_compilation",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=0 if success else 1,
                capability_tested="gcc",
                capability_supported=success,
                details={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="gcc_compilation",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=1,
                capability_tested="gcc",
                capability_supported=False,
                error_messages=[str(e)],
            )

    async def _cpp_compilation(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        cpp_code = (
            '#include <iostream>\n'
            'int main() { std::cout << "hello-cpp" << std::endl; return 0; }'
        )
        try:
            await provider.write_file(sandbox_id, "/tmp/hello.cpp", cpp_code)
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id,
                "g++ /tmp/hello.cpp -o /tmp/hello_cpp && /tmp/hello_cpp",
            )
            success = exit_code == 0 and "hello-cpp" in stdout
            return PhaseResult(
                name="cpp_compilation",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=0 if success else 1,
                capability_tested="gpp",
                capability_supported=success,
                details={"stdout": stdout, "stderr": stderr, "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="cpp_compilation",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=1,
                capability_tested="gpp",
                capability_supported=False,
                error_messages=[str(e)],
            )

    async def _exec_timeout(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        # Script sleeps 10s, but we set a 3s timeout
        cmd = "python3 -c \"import time; time.sleep(10); print('done')\""
        try:
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, cmd, timeout_seconds=3
            )
            # If we get here quickly with a non-zero exit, timeout was enforced
            elapsed = time.time() - t0
            # Timeout is enforced if the command didn't run the full 10s
            enforced = elapsed < 8 and exit_code != 0
            # Also count as enforced if provider raised and we got an empty result
            if not enforced and "done" not in stdout and elapsed < 8:
                enforced = True
            return PhaseResult(
                name="exec_timeout",
                success=enforced,
                duration_seconds=elapsed,
                tool_calls=1,
                friction_points=0 if enforced else 1,
                capability_tested="exec_timeout",
                capability_supported=enforced,
                details={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": exit_code,
                    "elapsed": elapsed,
                },
            )
        except Exception as e:
            elapsed = time.time() - t0
            # A timeout exception within a reasonable window means enforcement worked
            enforced = elapsed < 8
            return PhaseResult(
                name="exec_timeout",
                success=enforced,
                duration_seconds=elapsed,
                tool_calls=1,
                friction_points=0 if enforced else 1,
                capability_tested="exec_timeout",
                capability_supported=enforced,
                error_messages=[] if enforced else [str(e)],
                details={"elapsed": elapsed, "exception": str(e)},
            )

    async def _python_version(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        try:
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, "python3 --version"
            )
            # python3 --version may output to stdout or stderr
            version_str = (stdout + stderr).strip()
            success = exit_code == 0 and "Python" in version_str
            return PhaseResult(
                name="python_version",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                capability_tested="python3",
                capability_supported=success,
                details={"version": version_str},
            )
        except Exception as e:
            return PhaseResult(
                name="python_version",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="python3",
                capability_supported=False,
                error_messages=[str(e)],
            )


register_suite(CompetitiveSuite)
