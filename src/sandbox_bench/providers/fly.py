"""Fly.io Machines provider implementation."""

from typing import Optional
import httpx

from ..provider import SandboxProvider, ProviderInfo, register_provider


class FlyProvider(SandboxProvider):
    """Fly.io Machines provider (Firecracker microVMs)."""
    
    name = "fly"
    info = ProviderInfo(
        name="Fly.io Machines",
        description="Firecracker VMs with global deployment",
        docs_url="https://fly.io/docs/machines/",
        pricing_url="https://fly.io/pricing",
        mcp_server=False,
        openapi_spec=True,
        llms_txt=False,
    )
    
    def __init__(self):
        self._token = None
        self._app_name = "sandbox-bench"
        self._base_url = "https://api.machines.dev/v1"
    
    async def authenticate(self, api_key: str) -> None:
        """Authenticate with Fly.io."""
        self._token = api_key
        
        # Verify token works
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/apps",
                headers={"Authorization": f"Bearer {self._token}"},
            )
            if resp.status_code != 200:
                raise ValueError(f"Fly.io auth failed: {resp.text}")
    
    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a Fly Machine."""
        config = {
            "name": f"sandbox-bench-{int(__import__('time').time())}",
            "config": {
                "image": image or "python:3.11-slim",
                "auto_destroy": True,
                "restart": {"policy": "no"},
                "guest": {
                    "cpu_kind": "shared",
                    "cpus": 1,
                    "memory_mb": 256,
                },
            },
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/apps/{self._app_name}/machines",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json=config,
            )
            
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"Failed to create machine: {resp.text}")
            
            data = resp.json()
            machine_id = data["id"]
            
            # Wait for machine to start
            await self._wait_for_state(machine_id, "started")
            
            return machine_id
    
    async def _wait_for_state(
        self,
        machine_id: str,
        target_state: str,
        timeout: int = 60,
    ) -> None:
        """Wait for machine to reach target state."""
        import asyncio
        
        start = __import__("time").time()
        while __import__("time").time() - start < timeout:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._base_url}/apps/{self._app_name}/machines/{machine_id}",
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                
                if resp.status_code == 200:
                    state = resp.json().get("state")
                    if state == target_state:
                        return
            
            await asyncio.sleep(1)
        
        raise TimeoutError(f"Machine did not reach {target_state} state")
    
    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code via Fly Machine exec."""
        import base64
        
        cmd = ["python", "-c", code] if language == "python" else [language, "-c", code]
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/apps/{self._app_name}/machines/{sandbox_id}/exec",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json={
                    "cmd": cmd,
                    "timeout": timeout_seconds,
                },
                timeout=timeout_seconds + 5,
            )
            
            if resp.status_code != 200:
                return ("", f"Exec failed: {resp.text}", 1)
            
            data = resp.json()
            stdout = base64.b64decode(data.get("stdout", "")).decode()
            stderr = base64.b64decode(data.get("stderr", "")).decode()
            exit_code = data.get("exit_code", 0)
            
            return (stdout, stderr, exit_code)
    
    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file via exec."""
        import base64
        
        if isinstance(content, str):
            content = content.encode()
        
        b64 = base64.b64encode(content).decode()
        code = f"import base64; open('{path}', 'wb').write(base64.b64decode('{b64}'))"
        
        await self.execute(sandbox_id, code, "python")
    
    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file via exec."""
        stdout, stderr, exit_code = await self.execute(
            sandbox_id,
            f"cat {path}",
            "sh",
        )
        return stdout
    
    async def destroy(self, sandbox_id: str) -> None:
        """Destroy Fly Machine."""
        async with httpx.AsyncClient() as client:
            await client.delete(
                f"{self._base_url}/apps/{self._app_name}/machines/{sandbox_id}",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"force": "true"},
            )


# Register the provider
register_provider(FlyProvider)
