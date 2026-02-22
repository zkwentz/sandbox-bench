"""Daytona provider implementation."""

import os
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
        self._sandbox = None
        self._api_key = None
    
    async def authenticate(self, api_key: str) -> None:
        """Authenticate with Daytona."""
        try:
            from daytona_sdk import Daytona, DaytonaConfig
            self._api_key = api_key
            os.environ["DAYTONA_API_KEY"] = api_key
            config = DaytonaConfig(api_key=api_key)
            self._client = Daytona(config)
        except ImportError:
            raise ImportError("daytona-sdk package required: pip install daytona-sdk")
    
    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a Daytona sandbox."""
        self._sandbox = self._client.create()
        return self._sandbox.id
    
    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in Daytona sandbox."""
        if language == "python":
            response = self._sandbox.process.code_run(code)
        else:
            response = self._sandbox.process.exec(f"echo '{code}' | {language}")
        
        # Handle the response based on available attributes
        stdout = getattr(response, 'result', '') or ''
        stderr = ''
        exit_code = getattr(response, 'exit_code', 0) or 0
        
        return (stdout, stderr, exit_code)
    
    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file to Daytona sandbox."""
        if isinstance(content, str):
            content = content.encode('utf-8')
        self._sandbox.fs.upload_file(content, path)
    
    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from Daytona sandbox."""
        content = self._sandbox.fs.download_file(path)
        # Return as string for comparison
        if isinstance(content, bytes):
            return content.decode('utf-8')
        return content
    
    async def destroy(self, sandbox_id: str) -> None:
        """Destroy Daytona sandbox."""
        if self._sandbox:
            self._client.delete(self._sandbox)


# Register the provider
register_provider(DaytonaProvider)
