"""CodeSandbox provider implementation."""

from typing import Optional

from ..provider import SandboxProvider, ProviderInfo, register_provider


class CodeSandboxProvider(SandboxProvider):
    """CodeSandbox provider."""
    
    name = "codesandbox"
    info = ProviderInfo(
        name="CodeSandbox",
        description="Cloud development environments",
        docs_url="https://codesandbox.io/docs/sdk",
        pricing_url="https://codesandbox.io/pricing",
        mcp_server=False,
        openapi_spec=True,
        llms_txt=False,
    )
    
    def __init__(self):
        self._client = None
        self._api_key = None
    
    async def authenticate(self, api_key: str) -> None:
        """Authenticate with CodeSandbox."""
        try:
            from codesandbox_sdk import CodeSandbox
            self._client = CodeSandbox(api_key=api_key)
            self._api_key = api_key
        except ImportError:
            raise ImportError("codesandbox-sdk package required: pip install codesandbox-sdk")
    
    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a CodeSandbox."""
        sandbox = self._client.sandbox.create(
            template=image or "node",
        )
        return sandbox.id
    
    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in CodeSandbox."""
        sandbox = self._client.sandbox.get(sandbox_id)
        
        # Write code to temp file and execute
        temp_file = f"/tmp/code.{language}"
        sandbox.fs.write_file(temp_file, code)
        
        if language == "python":
            result = sandbox.shells.run(f"python {temp_file}")
        else:
            result = sandbox.shells.run(f"{language} {temp_file}")
        
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
        """Write file to CodeSandbox."""
        sandbox = self._client.sandbox.get(sandbox_id)
        sandbox.fs.write_file(path, content)
    
    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from CodeSandbox."""
        sandbox = self._client.sandbox.get(sandbox_id)
        return sandbox.fs.read_file(path)
    
    async def destroy(self, sandbox_id: str) -> None:
        """Hibernate CodeSandbox."""
        sandbox = self._client.sandbox.get(sandbox_id)
        sandbox.hibernate()


# Register the provider
register_provider(CodeSandboxProvider)
