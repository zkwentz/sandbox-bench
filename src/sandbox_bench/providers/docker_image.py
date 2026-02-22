"""Generic Docker image provider implementation.

This provider runs any Docker image as a sandbox environment.
It expects the container to expose an HTTP server on port 8000
with /health, /execute, /write_file, /read_file endpoints.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from typing import Optional

import httpx

from ..provider import SandboxProvider, ProviderInfo, register_provider


class DockerImageProvider(SandboxProvider):
    """Generic Docker image sandbox provider."""

    name = "docker-image"
    info = ProviderInfo(
        name="Docker Image",
        description="Run any Docker image as a sandbox",
        docs_url="https://docs.docker.com",
        pricing_url=None,
        mcp_server=False,
        openapi_spec=False,
        llms_txt=False,
    )

    def __init__(self):
        self._container_id: Optional[str] = None
        self._image: Optional[str] = None
        self._port: int = 8000
        self._base_url: Optional[str] = None

    async def authenticate(self, api_key: str) -> None:
        """
        Initialize Docker provider.

        Args:
            api_key: Docker image name (e.g., "myimage:latest")
        """
        # Validate Docker is available
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError("Docker daemon not running")
        except FileNotFoundError:
            raise ImportError("Docker not installed")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Docker daemon not responding")

        self._image = api_key

    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create a Docker container from the image."""
        docker_image = image or self._image
        if not docker_image:
            raise ValueError("No Docker image specified")

        # Find available port
        self._port = self._find_available_port()
        container_name = f"sandbox-bench-{uuid.uuid4().hex[:8]}"

        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-p", f"{self._port}:8000",
            docker_image
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr}")

        self._container_id = result.stdout.strip()
        self._base_url = f"http://localhost:{self._port}"

        # Wait for ready
        await self._wait_for_ready(timeout_seconds=60)
        return self._container_id

    def _find_available_port(self, start: int = 8100, end: int = 8200) -> int:
        """Find an available port."""
        import socket
        for port in range(start, end):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("localhost", port))
                    return port
                except OSError:
                    continue
        raise RuntimeError(f"No available ports in range {start}-{end}")

    async def _wait_for_ready(self, timeout_seconds: int = 60) -> None:
        """Wait for the container to be ready."""
        start = time.time()
        async with httpx.AsyncClient() as client:
            while time.time() - start < timeout_seconds:
                try:
                    resp = await client.get(f"{self._base_url}/health", timeout=2)
                    if resp.status_code == 200:
                        return
                except httpx.RequestError:
                    pass
                await asyncio.sleep(0.5)
        raise RuntimeError(f"Container not ready after {timeout_seconds}s")

    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in the container."""
        # Try HTTP API first
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self._base_url}/execute",
                    json={"code": code, "language": language},
                    timeout=timeout_seconds
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return (
                        data.get("stdout", ""),
                        data.get("stderr", ""),
                        data.get("exit_code", 0)
                    )
            except httpx.RequestError:
                pass

        # Fall back to docker exec
        if language == "python":
            cmd = ["docker", "exec", self._container_id, "python3", "-c", code]
        else:
            cmd = ["docker", "exec", self._container_id, "sh", "-c", code]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
            return (result.stdout, result.stderr, result.returncode)
        except subprocess.TimeoutExpired:
            return ("", f"Timeout after {timeout_seconds}s", 1)

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write a file to the container."""
        if isinstance(content, str):
            content = content.encode('utf-8')

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            cmd = ["docker", "cp", temp_path, f"{self._container_id}:{path}"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to write file: {result.stderr}")
        finally:
            os.remove(temp_path)

    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read a file from the container."""
        cmd = ["docker", "exec", self._container_id, "cat", path]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to read file: {result.stderr.decode()}")
        return result.stdout.decode('utf-8')

    async def destroy(self, sandbox_id: str) -> None:
        """Stop and remove the container."""
        if self._container_id:
            subprocess.run(["docker", "rm", "-f", self._container_id], capture_output=True)
            self._container_id = None

    async def get_status(self, sandbox_id: str) -> str:
        """Get container status."""
        if not self._container_id:
            return "stopped"
        cmd = ["docker", "inspect", "-f", "{{.State.Status}}", self._container_id]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else "unknown"


register_provider(DockerImageProvider)
