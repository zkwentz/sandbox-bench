"""E2B provider implementation."""

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
        self._client = None
        self._api_key = None
    
    async def authenticate(self, api_key: str) -> None:
        """Authenticate with E2B."""
        try:
            from e2b import Sandbox
            self._api_key = api_key
            # E2B uses API key per-request, just validate it exists
            if not api_key:
                raise ValueError("E2B API key required")
        except ImportError:
            raise ImportError("e2b package required: pip install e2b")
    
    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create an E2B sandbox."""
        from e2b import Sandbox
        
        template = image or "base"
        sandbox = Sandbox(
            template=template,
            api_key=self._api_key,
            timeout=timeout_seconds,
        )
        
        return sandbox.id
    
    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in E2B sandbox."""
        from e2b import Sandbox
        
        sandbox = Sandbox.connect(sandbox_id, api_key=self._api_key)
        
        if language == "python":
            result = sandbox.run_code(code)
        else:
            # Use process for other languages
            result = sandbox.process.start_and_wait(f"echo '{code}' | {language}")
        
        return (
            result.stdout or "",
            result.stderr or "",
            result.exit_code or 0,
        )
    
    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file to E2B sandbox."""
        from e2b import Sandbox
        
        sandbox = Sandbox.connect(sandbox_id, api_key=self._api_key)
        sandbox.filesystem.write(path, content)
    
    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from E2B sandbox."""
        from e2b import Sandbox
        
        sandbox = Sandbox.connect(sandbox_id, api_key=self._api_key)
        return sandbox.filesystem.read(path)
    
    async def destroy(self, sandbox_id: str) -> None:
        """Destroy E2B sandbox."""
        from e2b import Sandbox
        
        sandbox = Sandbox.connect(sandbox_id, api_key=self._api_key)
        sandbox.close()


# Register the provider
register_provider(E2BProvider)
