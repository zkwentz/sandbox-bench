"""Modal provider implementation."""

from __future__ import annotations

import os
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
            # Modal reads from MODAL_TOKEN_ID and MODAL_TOKEN_SECRET env vars
            # or uses the passed credentials
            self._modal = modal
        except ImportError:
            raise ImportError("modal package required: pip install modal")
    
    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a Modal sandbox."""
        import modal
        
        # Use App.lookup pattern as required by current Modal SDK
        app = modal.App.lookup("sandbox-bench", create_if_missing=True)
        
        img = modal.Image.debian_slim(python_version="3.12").pip_install("numpy")
        if image:
            img = modal.Image.from_registry(image)
        
        self._sandbox = modal.Sandbox.create(
            "sleep", "infinity",
            image=img,
            timeout=timeout_seconds,
            app=app,
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
        
        stdout = process.stdout.read()
        stderr = process.stderr.read()
        
        return (stdout, stderr, process.returncode)
    
    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file to Modal sandbox."""
        if isinstance(content, str):
            content = content.encode()
        
        import base64
        b64 = base64.b64encode(content).decode()
        process = self._sandbox.exec(
            "python", "-c",
            f"import base64; open('{path}', 'wb').write(base64.b64decode('{b64}'))"
        )
        process.wait()
    
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
