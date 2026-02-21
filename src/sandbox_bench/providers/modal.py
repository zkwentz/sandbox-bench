"""Modal provider implementation."""

from typing import Optional

from ..provider import SandboxProvider, ProviderInfo, register_provider


class ModalProvider(SandboxProvider):
    """Modal sandbox provider."""
    
    name = "modal"
    info = ProviderInfo(
        name="Modal",
        description="Serverless cloud for AI/ML",
        docs_url="https://modal.com/docs",
        pricing_url="https://modal.com/pricing",
        mcp_server=False,
        openapi_spec=False,
        llms_txt=True,
    )
    
    def __init__(self):
        self._app = None
        self._sandbox = None
    
    async def authenticate(self, api_key: str) -> None:
        """Authenticate with Modal."""
        try:
            import modal
            # Modal uses token ID and secret from environment
            # or ~/.modal/credentials
            self._app = modal.App("sandbox-bench")
        except ImportError:
            raise ImportError("modal package required: pip install modal")
    
    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a Modal sandbox."""
        import modal
        
        img = modal.Image.debian_slim().pip_install("numpy")
        if image:
            img = modal.Image.from_registry(image)
        
        self._sandbox = modal.Sandbox.create(
            app=self._app,
            image=img,
            timeout=timeout_seconds,
        )
        
        return self._sandbox.object_id
    
    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in Modal sandbox."""
        if language == "python":
            process = self._sandbox.exec("python", "-c", code)
        else:
            process = self._sandbox.exec(language, "-c", code)
        
        process.wait()
        
        return (
            process.stdout.read(),
            process.stderr.read(),
            process.returncode,
        )
    
    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file to Modal sandbox."""
        if isinstance(content, str):
            content = content.encode()
        
        # Use exec to write file
        import base64
        b64 = base64.b64encode(content).decode()
        self._sandbox.exec(
            "python", "-c",
            f"import base64; open('{path}', 'wb').write(base64.b64decode('{b64}'))"
        ).wait()
    
    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from Modal sandbox."""
        process = self._sandbox.exec("cat", path)
        process.wait()
        return process.stdout.read()
    
    async def destroy(self, sandbox_id: str) -> None:
        """Terminate Modal sandbox."""
        if self._sandbox:
            self._sandbox.terminate()


# Register the provider
register_provider(ModalProvider)
