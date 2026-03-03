"""Modal provider implementation."""

from __future__ import annotations

import asyncio
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
        self._sandboxes: dict[str, object] = {}

    async def authenticate(self, api_key: str) -> None:
        """Authenticate with Modal."""
        try:
            import modal
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

        def _create():
            app = modal.App.lookup("sandbox-bench", create_if_missing=True)
            img = modal.Image.debian_slim(python_version="3.12").pip_install("numpy")
            if image:
                img = modal.Image.from_registry(image)
            return modal.Sandbox.create(
                "sleep", "infinity",
                image=img,
                timeout=timeout_seconds,
                app=app,
            )

        sandbox = await asyncio.to_thread(_create)
        self._count_api_call()
        self._count_api_call()
        sid = sandbox.object_id
        self._sandboxes[sid] = sandbox
        return sid

    def _get(self, sandbox_id: str):
        sb = self._sandboxes.get(sandbox_id)
        if sb is None:
            raise ValueError(f"No sandbox: {sandbox_id}")
        return sb

    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in Modal sandbox."""
        sb = self._get(sandbox_id)

        def _exec():
            if language == "python":
                process = sb.exec("python", "-c", code)
            else:
                process = sb.exec(language, "-c", code)
            process.wait()
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            return (stdout, stderr, process.returncode)

        result = await asyncio.to_thread(_exec)
        self._count_api_call()
        return result

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute a shell command in Modal sandbox."""
        sb = self._get(sandbox_id)

        def _exec():
            process = sb.exec("bash", "-c", command)
            process.wait()
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            return (stdout, stderr, process.returncode)

        result = await asyncio.to_thread(_exec)
        self._count_api_call()
        return result

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file to Modal sandbox."""
        sb = self._get(sandbox_id)
        if isinstance(content, str):
            content = content.encode()

        import base64
        b64 = base64.b64encode(content).decode()

        def _write():
            process = sb.exec(
                "python", "-c",
                f"import base64,os; os.makedirs(os.path.dirname('{path}') or '.', exist_ok=True); open('{path}', 'wb').write(base64.b64decode('{b64}'))"
            )
            process.wait()

        await asyncio.to_thread(_write)
        self._count_api_call()

    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file from Modal sandbox."""
        sb = self._get(sandbox_id)

        def _read():
            process = sb.exec("cat", path)
            process.wait()
            return process.stdout.read()

        result = await asyncio.to_thread(_read)
        self._count_api_call()
        return result

    async def destroy(self, sandbox_id: str) -> None:
        """Terminate Modal sandbox."""
        sb = self._sandboxes.pop(sandbox_id, None)
        if sb is not None:
            try:
                await asyncio.to_thread(sb.terminate)
                self._count_api_call()
            except Exception:
                pass


# Register the provider
register_provider(ModalProvider)
