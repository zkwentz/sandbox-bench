"""Fly.io Machines provider implementation."""

from __future__ import annotations

import httpx
from typing import Optional

from ..provider import SandboxProvider, ProviderInfo, register_provider


class FlyProvider(SandboxProvider):
    """Fly.io Machines provider (Firecracker-based)."""

    name = "fly"
    info = ProviderInfo(
        name="Fly.io",
        description="Run apps close to users with Firecracker VMs",
        docs_url="https://fly.io/docs/machines/api/",
        pricing_url="https://fly.io/pricing",
        mcp_server=False,
        openapi_spec=True,
        llms_txt=False,
    )

    BASE_URL = "https://api.machines.dev/v1"

    def __init__(self):
        self._api_key = None
        self._client = None
        self._machine_id = None
        self._app_name = "sandbox-bench"

    async def authenticate(self, api_key: str) -> None:
        """Authenticate with Fly.io using a Bearer API token."""
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        # Validate the token by listing apps
        resp = await self._client.get(
            f"{self.BASE_URL}/apps",
            params={"org_slug": "personal"},
        )
        resp.raise_for_status()

    async def _ensure_app(self) -> None:
        """Create the sandbox-bench app if it doesn't exist."""
        resp = await self._client.get(f"{self.BASE_URL}/apps/{self._app_name}")
        if resp.status_code == 404:
            create_resp = await self._client.post(
                f"{self.BASE_URL}/apps",
                json={
                    "app_name": self._app_name,
                    "org_slug": "personal",
                },
            )
            # 422 means app already exists (race condition), which is fine
            if create_resp.status_code not in (200, 201, 422):
                create_resp.raise_for_status()

    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a Fly.io machine with the given Docker image."""
        await self._ensure_app()

        image = image or "python:3.12-slim"

        resp = await self._client.post(
            f"{self.BASE_URL}/apps/{self._app_name}/machines",
            json={
                "config": {
                    "image": image,
                    "guest": {
                        "cpu_kind": "shared",
                        "cpus": 1,
                        "memory_mb": 256,
                    },
                    "auto_destroy": True,
                    "init": {
                        "exec": ["/bin/sleep", "inf"],
                    },
                    "restart": {
                        "policy": "no",
                    },
                },
            },
        )
        resp.raise_for_status()

        data = resp.json()
        self._machine_id = data["id"]

        # Wait for machine to reach "started" state using the blocking wait endpoint
        wait_resp = await self._client.get(
            f"{self.BASE_URL}/apps/{self._app_name}/machines/{self._machine_id}/wait",
            params={"state": "started", "timeout": 60},
            timeout=70.0,
        )
        wait_resp.raise_for_status()

        return self._machine_id

    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in a Fly.io machine via the exec endpoint."""
        if language == "python":
            cmd = f"python3 -c {_shell_quote(code)}"
        else:
            cmd = code

        resp = await self._client.post(
            f"{self.BASE_URL}/apps/{self._app_name}/machines/{sandbox_id}/exec",
            json={
                "cmd": cmd,
                "timeout": min(timeout_seconds, 60),
            },
            timeout=timeout_seconds + 10,
        )
        resp.raise_for_status()

        data = resp.json()
        return (
            data.get("stdout", ""),
            data.get("stderr", ""),
            data.get("exit_code", 0),
        )

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file to Fly.io machine via exec."""
        if isinstance(content, str):
            content = content.encode()

        import base64
        b64 = base64.b64encode(content).decode()

        await self.execute(
            sandbox_id,
            f"import base64; open('{path}', 'wb').write(base64.b64decode('{b64}'))",
            language="python",
        )

    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from Fly.io machine via exec."""
        stdout, _, _ = await self.execute(
            sandbox_id,
            f"python3 -c \"print(open('{path}').read(), end='')\"",
            language="sh",
        )
        return stdout

    async def destroy(self, sandbox_id: str) -> None:
        """Destroy Fly.io machine."""
        if self._machine_id and self._client:
            try:
                await self._client.delete(
                    f"{self.BASE_URL}/apps/{self._app_name}/machines/{sandbox_id}",
                    params={"force": "true"},
                )
            except Exception:
                pass
        if self._client:
            await self._client.aclose()


def _shell_quote(s: str) -> str:
    """Quote a string for safe shell usage."""
    return "'" + s.replace("'", "'\\''") + "'"


# Register the provider
register_provider(FlyProvider)
