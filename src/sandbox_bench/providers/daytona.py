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
            config = DaytonaConfig(api_key=api_key)
            self._client = Daytona(config)
        except ImportError:
            raise ImportError("daytona-sdk package required: pip install daytona-sdk")
        except TypeError:
            # Try alternative initialization
            from daytona_sdk import Daytona
            os.environ["DAYTONA_API_KEY"] = api_key
            self._client = Daytona()
    
    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a Daytona workspace."""
        self._sandbox = self._client.create()
        return self._sandbox.id
    
    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in Daytona workspace."""
        if language == "python":
            result = self._sandbox.process.code_run(code)
        else:
            result = self._sandbox.process.exec(f"echo '{code}' | {language}")
        
        return (
            result.stdout or "",
            result.stderr or "",
            result.exit_code if hasattr(result, 'exit_code') else 0,
        )
    
    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file to Daytona workspace."""
        self._sandbox.fs.upload_file(path, content)
    
    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from Daytona workspace."""
        return self._sandbox.fs.download_file(path)
    
    async def destroy(self, sandbox_id: str) -> None:
        """Destroy Daytona workspace."""
        if self._sandbox:
            self._client.remove(self._sandbox)


# Register the provider
register_provider(DaytonaProvider)
