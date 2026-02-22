"""Generic MicroVM provider implementation.

This provider runs a MicroVM using a provided command/binary.
It expects the VM to expose an HTTP server with /health, /execute,
/write_file, /read_file endpoints.

Requires Linux with KVM support.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import time
import uuid
from typing import Optional

import httpx

from ..provider import SandboxProvider, ProviderInfo, register_provider


class MicroVMProvider(SandboxProvider):
    """Generic MicroVM sandbox provider."""

    name = "microvm"
    info = ProviderInfo(
        name="MicroVM",
        description="Run a Firecracker MicroVM as a sandbox",
        docs_url="https://firecracker-microvm.github.io",
        pricing_url=None,
        mcp_server=False,
        openapi_spec=False,
        llms_txt=False,
    )

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._vm_id: Optional[str] = None
        self._command: Optional[str] = None
        self._base_url: Optional[str] = None
        self._ip_address: str = "172.16.0.2"
        self._port: int = 8000

    async def authenticate(self, api_key: str) -> None:
        """
        Initialize MicroVM provider.

        Args:
            api_key: Command to run the MicroVM, with {port} and {ip} placeholders.
                     Example: "openenvvm run ./my.microvm --port {port} --ip {ip}"
        """
        if sys.platform != "linux":
            raise RuntimeError(
                f"MicroVMs require Linux with KVM. Current platform: {sys.platform}"
            )

        if not os.path.exists("/dev/kvm"):
            raise RuntimeError("KVM not available (/dev/kvm not found)")

        self._command = api_key

    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create and start a MicroVM."""
        command = image or self._command
        if not command:
            raise ValueError("No MicroVM command specified")

        self._vm_id = f"vm-{uuid.uuid4().hex[:8]}"

        # Calculate unique IP for parallel runs
        vm_num = hash(self._vm_id) % 250 + 2
        self._ip_address = f"172.16.0.{vm_num}"

        # Substitute placeholders in command
        cmd = command.format(port=self._port, ip=self._ip_address)

        # Start the VM process
        self._process = subprocess.Popen(
            ["sudo"] + shlex.split(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self._base_url = f"http://{self._ip_address}:{self._port}"

        # Wait for ready
        await self._wait_for_ready(timeout_seconds=60)
        return self._vm_id

    async def _wait_for_ready(self, timeout_seconds: int = 60) -> None:
        """Wait for the VM to be ready."""
        start = time.time()
        async with httpx.AsyncClient() as client:
            while time.time() - start < timeout_seconds:
                # Check if process died
                if self._process and self._process.poll() is not None:
                    stderr = self._process.stderr.read().decode() if self._process.stderr else ""
                    raise RuntimeError(f"VM process died: {stderr}")

                try:
                    resp = await client.get(f"{self._base_url}/health", timeout=2)
                    if resp.status_code == 200:
                        return
                except httpx.RequestError:
                    pass
                await asyncio.sleep(0.5)
        raise RuntimeError(f"VM not ready after {timeout_seconds}s")

    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in the VM."""
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

        # Fall back to SSH
        ssh_cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=5",
            f"root@{self._ip_address}",
            f"python3 -c {repr(code)}" if language == "python" else code
        ]

        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout_seconds)
            return (result.stdout, result.stderr, result.returncode)
        except subprocess.TimeoutExpired:
            return ("", f"Timeout after {timeout_seconds}s", 1)
        except Exception as e:
            return ("", str(e), 1)

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write a file to the VM."""
        if isinstance(content, str):
            content = content.encode('utf-8')

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            cmd = [
                "scp", "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                temp_path, f"root@{self._ip_address}:{path}"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to write file: {result.stderr}")
        finally:
            os.remove(temp_path)

    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read a file from the VM."""
        cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            f"root@{self._ip_address}", f"cat {path}"
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to read file: {result.stderr.decode()}")
        return result.stdout.decode('utf-8')

    async def destroy(self, sandbox_id: str) -> None:
        """Stop the VM."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        self._vm_id = None

    async def get_status(self, sandbox_id: str) -> str:
        """Get VM status."""
        if not self._process:
            return "stopped"
        if self._process.poll() is None:
            return "running"
        return "stopped"


register_provider(MicroVMProvider)
