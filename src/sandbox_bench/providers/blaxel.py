"""Blaxel provider implementation."""

from __future__ import annotations

import os
import shlex
import uuid
from typing import Optional

from ..provider import SandboxProvider, ProviderInfo, register_provider


class BlaxelProvider(SandboxProvider):
    """Blaxel sandbox provider (persistent sandboxes with near-instant latency)."""

    name = "blaxel"
    info = ProviderInfo(
        name="Blaxel",
        description="Persistent sandboxes with near-instant latency for AI agents",
        docs_url="https://docs.blaxel.ai/Sandboxes/Overview",
        pricing_url="https://blaxel.ai/pricing",
        mcp_server=True,
        openapi_spec=True,
        llms_txt=True,
    )

    def __init__(self):
        self._sandboxes: dict[str, object] = {}
        self._api_key = None
        self._workspace = None

    def _get(self, sandbox_id: str):
        """Get sandbox instance by name."""
        sb = self._sandboxes.get(sandbox_id)
        if sb is None:
            raise RuntimeError(f"Sandbox {sandbox_id} not found")
        return sb

    async def authenticate(self, api_key: str) -> None:
        """Authenticate with Blaxel via API key."""
        try:
            from blaxel.core import SandboxInstance  # noqa: F401
        except ImportError:
            raise ImportError("blaxel package required: pip install blaxel")

        if not api_key:
            raise ValueError("Blaxel API key required")

        self._api_key = api_key
        os.environ["BL_API_KEY"] = api_key

        # Workspace can be set via BL_WORKSPACE env var or we discover it
        if not os.environ.get("BL_WORKSPACE"):
            # Try to discover workspace from API
            import httpx
            resp = httpx.get(
                "https://api.blaxel.ai/v0/workspaces",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            self._count_api_call()
            if resp.status_code == 200:
                workspaces = resp.json()
                if workspaces:
                    self._workspace = workspaces[0]["name"]
                    os.environ["BL_WORKSPACE"] = self._workspace
                else:
                    raise ValueError("No workspaces found for this API key")
            else:
                raise ValueError(f"Failed to discover workspace: {resp.status_code}")
        else:
            self._workspace = os.environ["BL_WORKSPACE"]

    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a Blaxel sandbox."""
        from blaxel.core import SandboxInstance

        name = f"bench-{uuid.uuid4().hex[:8]}"
        sb = await SandboxInstance.create({
            "name": name,
            "image": image or "blaxel/base-image:latest",
            "memory": 4096,
        })
        self._count_api_call()
        self._sandboxes[name] = sb
        return name

    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in Blaxel sandbox."""
        sb = self._get(sandbox_id)

        if language == "python":
            cmd = f"python3 -c {shlex.quote(code)}"
        elif language == "sh":
            cmd = code
        else:
            cmd = f"{language} -c {shlex.quote(code)}"

        result = await sb.process.exec({
            "command": cmd,
            "wait_for_completion": True,
            "timeout": timeout_seconds * 1000,  # Blaxel uses milliseconds
        })
        self._count_api_call()
        return (result.stdout or "", result.stderr or "", result.exit_code)

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute a shell command in Blaxel sandbox."""
        sb = self._get(sandbox_id)
        result = await sb.process.exec({
            "command": command,
            "wait_for_completion": True,
            "timeout": timeout_seconds * 1000,
        })
        self._count_api_call()
        return (result.stdout or "", result.stderr or "", result.exit_code)

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file to Blaxel sandbox."""
        sb = self._get(sandbox_id)
        if isinstance(content, bytes):
            await sb.fs.write_binary(path, content)
        else:
            await sb.fs.write(path, content)
        self._count_api_call()

    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from Blaxel sandbox."""
        sb = self._get(sandbox_id)
        content = await sb.fs.read(path)
        self._count_api_call()
        return content

    async def destroy(self, sandbox_id: str) -> None:
        """Destroy Blaxel sandbox."""
        sb = self._sandboxes.pop(sandbox_id, None)
        if sb is not None:
            try:
                await sb.delete()
                self._count_api_call()
            except Exception:
                # Best effort cleanup
                pass


register_provider(BlaxelProvider)
