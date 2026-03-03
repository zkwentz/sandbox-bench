"""Sprites.dev provider implementation (Fly.io Sprites SDK)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from ..provider import SandboxProvider, ProviderInfo, register_provider


class FlyProvider(SandboxProvider):
    """Sprites.dev sandbox provider using the official Python SDK."""

    name = "fly"
    info = ProviderInfo(
        name="Sprites.dev",
        description="Stateful, persistent sandboxes powered by Fly.io",
        docs_url="https://docs.sprites.dev",
        pricing_url="https://fly.io/pricing",
        mcp_server=False,
        openapi_spec=True,
        llms_txt=True,
    )

    def __init__(self):
        self._client = None
        self._sprites: dict[str, object] = {}

    async def authenticate(self, api_key: str) -> None:
        """Authenticate with Sprites.dev using a SPRITE_TOKEN."""
        try:
            from sprites import SpritesClient
        except ImportError:
            raise ImportError("sprites-py package required: pip install sprites-py")

        if not api_key:
            raise ValueError("SPRITE_TOKEN / FLY_API_TOKEN required")

        # SpritesClient is synchronous; create in thread to avoid blocking
        self._client = await asyncio.to_thread(SpritesClient, api_key)
        self._count_api_call()

        # Validate token by listing sprites
        await asyncio.to_thread(self._client.list_sprites)
        self._count_api_call()

    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a new Sprite."""
        name = f"bench-{uuid.uuid4().hex[:8]}"

        sprite = await asyncio.to_thread(self._client.create_sprite, name)
        self._count_api_call()
        self._sprites[name] = sprite

        return name

    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in a Sprite."""
        if language == "python":
            cmd_args = ("python3", "-c", code)
        else:
            cmd_args = ("bash", "-c", code)

        return await self._run_cmd(sandbox_id, cmd_args, timeout_seconds)

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute a shell command in a Sprite."""
        return await self._run_cmd(sandbox_id, ("bash", "-c", command), timeout_seconds)

    async def _run_cmd(
        self,
        sandbox_id: str,
        args: tuple[str, ...],
        timeout: int,
    ) -> tuple[str, str, int]:
        """Run a command and return (stdout, stderr, exit_code)."""
        sprite = self._get_sprite(sandbox_id)

        def _exec():
            cmd = sprite.command(*args, timeout=float(timeout))
            # Capture both stdout and stderr separately
            cmd._capture_stdout = True
            cmd._capture_stderr = True
            # _run_sync sets _started and handles the full lifecycle
            exit_code = cmd._run_sync()
            stdout = (cmd._stdout_data or b"").decode("utf-8", errors="replace")
            stderr = (cmd._stderr_data or b"").decode("utf-8", errors="replace")
            return (stdout, stderr, exit_code)

        result = await asyncio.to_thread(_exec)
        self._count_api_call()
        return result

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write a file to a Sprite using the filesystem API."""
        sprite = self._get_sprite(sandbox_id)

        def _write():
            fs = sprite.filesystem("/")
            p = fs.path(path)
            if isinstance(content, bytes):
                p.write_bytes(content, mkdir_parents=True)
            else:
                p.write_text(content, mkdir_parents=True)

        await asyncio.to_thread(_write)
        self._count_api_call()

    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read a file from a Sprite using the filesystem API."""
        sprite = self._get_sprite(sandbox_id)

        def _read():
            fs = sprite.filesystem("/")
            return fs.path(path).read_text()

        result = await asyncio.to_thread(_read)
        self._count_api_call()
        return result

    async def destroy(self, sandbox_id: str) -> None:
        """Destroy a Sprite."""
        sprite = self._sprites.pop(sandbox_id, None)
        if sprite is not None:
            try:
                await asyncio.to_thread(sprite.destroy)
                self._count_api_call()
            except Exception:
                pass
        if self._client and not self._sprites:
            try:
                await asyncio.to_thread(self._client.close)
            except Exception:
                pass

    def _get_sprite(self, sandbox_id: str):
        """Get a sprite handle by sandbox ID."""
        sprite = self._sprites.get(sandbox_id)
        if sprite is None:
            raise ValueError(f"No sprite found with id: {sandbox_id}")
        return sprite


# Register the provider
register_provider(FlyProvider)
