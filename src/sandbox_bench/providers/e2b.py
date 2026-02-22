"""E2B provider implementation."""

from __future__ import annotations

import os
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
        self._sandbox = None
        self._api_key = None
    
    async def authenticate(self, api_key: str) -> None:
        """Authenticate with E2B."""
        try:
            from e2b_code_interpreter import Sandbox
            self._api_key = api_key
            os.environ["E2B_API_KEY"] = api_key
            if not api_key:
                raise ValueError("E2B API key required")
        except ImportError:
            try:
                from e2b import Sandbox
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
        try:
            from e2b_code_interpreter import Sandbox
        except ImportError:
            from e2b import Sandbox
        
        self._sandbox = Sandbox.create()
        return self._sandbox.sandbox_id
    
    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in E2B sandbox."""
        if language == "python":
            execution = self._sandbox.run_code(code)
            stdout = ""
            stderr = ""
            if execution.logs:
                stdout = "\n".join(execution.logs.stdout) if execution.logs.stdout else ""
                stderr = "\n".join(execution.logs.stderr) if execution.logs.stderr else ""
            if execution.error:
                stderr += str(execution.error)
            return (stdout, stderr, 0 if not execution.error else 1)
        else:
            result = self._sandbox.commands.run(f"echo '{code}' | {language}")
            return (result.stdout or "", result.stderr or "", result.exit_code)
    
    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file to E2B sandbox."""
        if isinstance(content, bytes):
            content = content.decode('utf-8')
        self._sandbox.files.write(path, content)
    
    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from E2B sandbox."""
        return self._sandbox.files.read(path)
    
    async def destroy(self, sandbox_id: str) -> None:
        """Destroy E2B sandbox."""
        if self._sandbox:
            self._sandbox.kill()


# Register the provider
register_provider(E2BProvider)
