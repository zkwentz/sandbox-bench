"""Daytona provider implementation."""

from typing import Optional

from ..provider import SandboxProvider, ProviderInfo, register_provider


class DaytonaProvider(SandboxProvider):
    """Daytona sandbox provider (Docker-based)."""
    
    name = "daytona"
    info = ProviderInfo(
        name="Daytona",
        description="Standardized development environments",
        docs_url="https://daytona.io/docs",
        pricing_url="https://daytona.io/pricing",
        mcp_server=False,
        openapi_spec=True,
        llms_txt=False,
    )
    
    def __init__(self):
        self._client = None
        self._api_key = None
    
    async def authenticate(self, api_key: str) -> None:
        """Authenticate with Daytona."""
        try:
            from daytona_sdk import Daytona
            self._client = Daytona(api_key=api_key)
            self._api_key = api_key
        except ImportError:
            raise ImportError("daytona-sdk package required: pip install daytona-sdk")
    
    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a Daytona workspace."""
        workspace = self._client.create()
        return workspace.id
    
    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in Daytona workspace."""
        workspace = self._client.get_workspace(sandbox_id)
        
        if language == "python":
            result = workspace.process.code_run(code)
        else:
            result = workspace.process.exec(f"echo '{code}' | {language}")
        
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
        """Write file to Daytona workspace."""
        workspace = self._client.get_workspace(sandbox_id)
        workspace.fs.upload_file(path, content)
    
    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from Daytona workspace."""
        workspace = self._client.get_workspace(sandbox_id)
        return workspace.fs.download_file(path)
    
    async def destroy(self, sandbox_id: str) -> None:
        """Destroy Daytona workspace."""
        self._client.remove(sandbox_id)


# Register the provider
register_provider(DaytonaProvider)
