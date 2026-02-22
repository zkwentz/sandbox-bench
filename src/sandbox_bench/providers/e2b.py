"""E2B provider implementation."""

from __future__ import annotations

import os
import shlex
from typing import Optional

from ..provider import SandboxProvider, ProviderInfo, register_provider


class E2BProvider(SandboxProvider):
    """E2B sandbox provider (Firecracker microVMs)."""

    name = "e2b"
    info = ProviderInfo(
        name="E2B",
        description="Firecracker-based cloud sandboxes for AI agents",
        docs_url="https://e2b.dev/docs",
        pricing_url="https://e2b.dev/pricing",
        mcp_server=True,
        openapi_spec=True,
        llms_txt=False,
    )

    def __init__(self):
        self._sandboxes: dict[str, object] = {}
        self._api_key = None
        self._has_code_interpreter = False

    def _get(self, sandbox_id: str):
        """Get sandbox by ID."""
        sb = self._sandboxes.get(sandbox_id)
        if sb is None:
            raise RuntimeError(f"Sandbox {sandbox_id} not found")
        return sb

    async def authenticate(self, api_key: str) -> None:
        """Authenticate with E2B."""
        try:
            from e2b_code_interpreter import Sandbox  # noqa: F401
            self._has_code_interpreter = True
            self._api_key = api_key
            os.environ["E2B_API_KEY"] = api_key
            if not api_key:
                raise ValueError("E2B API key required")
        except ImportError:
            try:
                from e2b import Sandbox  # noqa: F401
                self._has_code_interpreter = False
                self._api_key = api_key
                os.environ["E2B_API_KEY"] = api_key
            except ImportError:
                raise ImportError("e2b package required: pip install e2b")

    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create an E2B sandbox."""
        if self._has_code_interpreter:
            from e2b_code_interpreter import Sandbox
        else:
            from e2b import Sandbox

        sb = Sandbox.create(timeout=timeout_seconds)
        self._sandboxes[sb.sandbox_id] = sb
        return sb.sandbox_id

    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in E2B sandbox."""
        sb = self._get(sandbox_id)
        if language == "python" and self._has_code_interpreter:
            execution = sb.run_code(code)
            stdout = ""
            stderr = ""
            if execution.logs:
                stdout = "\n".join(execution.logs.stdout) if execution.logs.stdout else ""
                stderr = "\n".join(execution.logs.stderr) if execution.logs.stderr else ""
            if execution.error:
                stderr += str(execution.error)
            return (stdout, stderr, 0 if not execution.error else 1)
        elif language == "python":
            cmd = f"python3 -c {shlex.quote(code)}"
            result = sb.commands.run(cmd, timeout=timeout_seconds)
            return (result.stdout or "", result.stderr or "", result.exit_code)
        else:
            result = sb.commands.run(code, timeout=timeout_seconds)
            return (result.stdout or "", result.stderr or "", result.exit_code)

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute a shell command directly via E2B commands API."""
        sb = self._get(sandbox_id)
        result = sb.commands.run(command, timeout=timeout_seconds)
        return (result.stdout or "", result.stderr or "", result.exit_code)

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file to E2B sandbox."""
        sb = self._get(sandbox_id)
        if isinstance(content, bytes):
            content = content.decode('utf-8')
        sb.files.write(path, content)

    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from E2B sandbox."""
        sb = self._get(sandbox_id)
        return sb.files.read(path)

    async def destroy(self, sandbox_id: str) -> None:
        """Destroy E2B sandbox."""
        sb = self._sandboxes.pop(sandbox_id, None)
        if sb is not None:
            sb.kill()


# Register the provider
register_provider(E2BProvider)
