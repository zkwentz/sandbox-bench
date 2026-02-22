"""CodeSandbox provider implementation."""

import httpx
from typing import Optional

from ..provider import SandboxProvider, ProviderInfo, register_provider


class CodeSandboxProvider(SandboxProvider):
    """CodeSandbox provider (Docker-based)."""
    
    name = "codesandbox"
    info = ProviderInfo(
        name="CodeSandbox",
        description="Cloud development environments",
        docs_url="https://codesandbox.io/docs",
        pricing_url="https://codesandbox.io/pricing",
        mcp_server=False,
        openapi_spec=True,
        llms_txt=False,
    )
    
    BASE_URL = "https://api.codesandbox.io/v1"
    
    def __init__(self):
        self._api_key = None
        self._client = None
        self._sandbox_id = None
    
    async def authenticate(self, api_key: str) -> None:
        """Authenticate with CodeSandbox."""
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        # Verify auth by listing sandboxes
        resp = await self._client.get(f"{self.BASE_URL}/sandboxes")
        if resp.status_code == 401:
            raise ValueError("Invalid CodeSandbox API key")
    
    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a CodeSandbox sandbox."""
        payload = {
            "template": image or "node",
        }
        
        resp = await self._client.post(
            f"{self.BASE_URL}/sandboxes",
            json=payload,
        )
        resp.raise_for_status()
        
        data = resp.json()
        self._sandbox_id = data.get("id") or data.get("sandbox", {}).get("id")
        return self._sandbox_id
    
    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in CodeSandbox."""
        # CodeSandbox uses shell commands
        if language == "python":
            cmd = f"python3 -c {repr(code)}"
        else:
            cmd = f"echo {repr(code)} | {language}"
        
        resp = await self._client.post(
            f"{self.BASE_URL}/sandboxes/{sandbox_id}/execute",
            json={"command": cmd},
            timeout=timeout_seconds + 5,
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
        """Write file to CodeSandbox."""
        if isinstance(content, bytes):
            content = content.decode('utf-8')
        
        resp = await self._client.put(
            f"{self.BASE_URL}/sandboxes/{sandbox_id}/files",
            json={"path": path, "content": content},
        )
        resp.raise_for_status()
    
    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from CodeSandbox."""
        resp = await self._client.get(
            f"{self.BASE_URL}/sandboxes/{sandbox_id}/files",
            params={"path": path},
        )
        resp.raise_for_status()
        return resp.json().get("content", "")
    
    async def destroy(self, sandbox_id: str) -> None:
        """Destroy CodeSandbox sandbox."""
        if self._sandbox_id:
            await self._client.delete(f"{self.BASE_URL}/sandboxes/{sandbox_id}")
        if self._client:
            await self._client.aclose()


# Register the provider
register_provider(CodeSandboxProvider)
