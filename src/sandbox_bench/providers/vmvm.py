"""VMVM (Virtual Machine Vending Machine) provider implementation.

Meta's internal platform for secure, ephemeral code execution in isolated
virtual machines. Uses vacli to lease VMs and paramiko for SSH command
execution and file I/O through tunneled ports.

Requires:
  - vacli on PATH
  - paramiko (pip install paramiko)
  - Valid Meta TLS credentials (THRIFT_TLS_CL_CERT_PATH, THRIFT_TLS_CL_KEY_PATH)
  - FaaS tenant ID passed as the API key (set VMVM_TENANT_ID)
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shlex
import subprocess
import time
from typing import Optional

import paramiko

from ..provider import SandboxProvider, ProviderInfo, register_provider


class VMVMProvider(SandboxProvider):
    """VMVM sandbox provider (Meta internal Firecracker VMs via FaaS)."""

    name = "vmvm"
    info = ProviderInfo(
        name="VMVM",
        description="Meta's ephemeral VM platform for secure code execution",
        docs_url=os.environ.get("VMVM_DOCS_URL", ""),
        pricing_url=None,
        mcp_server=False,
        openapi_spec=False,
        llms_txt=False,
    )

    def __init__(self):
        self._tenant_id: Optional[str] = None
        self._vacli_processes: dict[str, subprocess.Popen] = {}
        self._ssh_ports: dict[str, int] = {}
        self._ssh_clients: dict[str, paramiko.SSHClient] = {}

    def _get_ssh(self, sandbox_id: str) -> paramiko.SSHClient:
        """Get SSH client for a sandbox, reconnecting if needed."""
        client = self._ssh_clients.get(sandbox_id)
        if client is None:
            raise RuntimeError(f"No SSH connection for sandbox {sandbox_id}")

        # Check if transport is still active
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            # Reconnect
            port = self._ssh_ports[sandbox_id]
            client = self._connect_ssh(port)
            self._ssh_clients[sandbox_id] = client

        return client

    def _connect_ssh(self, port: int) -> paramiko.SSHClient:
        """Create a new SSH connection to the VM."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            "localhost",
            port=port,
            username="root",
            password="",
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )
        return client

    async def authenticate(self, api_key: str) -> None:
        """Authenticate with VMVM.

        Args:
            api_key: FaaS tenant ID
        """
        # Verify vacli is available
        try:
            result = subprocess.run(
                ["vacli", "--help"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode not in (0, 2):  # --help may return 2
                raise RuntimeError("vacli not working")
        except FileNotFoundError:
            raise RuntimeError(
                "vacli not found on PATH"
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("vacli timed out")

        self._tenant_id = api_key
        self._count_api_call()

    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Lease a VMVM virtual machine.

        Starts vacli in the background with --auto-renew --release-on-exit,
        tunneling port 22 (SSH) to a local port, then establishes a persistent
        paramiko SSH connection.
        """
        if not self._tenant_id:
            raise RuntimeError("Not authenticated - call authenticate() first")

        # Build vacli command
        cmd = [
            "vacli",
            "--faas-tenant-id", self._tenant_id,
            "lease",
            "--ttl", "5min",
            "--auto-renew",
            "--release-on-exit",
            "--tunnel-ports", "22",
        ]

        # Start vacli as a background process
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Parse the output to get session ID and port mappings.
        # vacli outputs JSON lines: first line is build info, second is
        # lease response (session + auth), third is port mappings.
        try:
            session_id_str = None
            ssh_port = None

            deadline = time.time() + timeout_seconds
            lines_read = 0

            while time.time() < deadline and lines_read < 3:
                # Check if process died
                if proc.poll() is not None:
                    stderr = proc.stderr.read().decode() if proc.stderr else ""
                    stdout = proc.stdout.read().decode() if proc.stdout else ""
                    raise RuntimeError(
                        f"vacli exited unexpectedly (code {proc.returncode}): "
                        f"{stderr or stdout}"
                    )

                # Non-blocking readline
                line = await asyncio.get_event_loop().run_in_executor(
                    None, proc.stdout.readline
                )
                if not line:
                    await asyncio.sleep(0.1)
                    continue

                line_str = line.decode().strip()
                if not line_str:
                    continue

                lines_read += 1

                try:
                    data = json.loads(line_str)
                except json.JSONDecodeError:
                    continue

                # Line 1: build info (has "build_info" key)
                if isinstance(data, dict) and "build_info" in data:
                    continue

                # Line 2: session + auth (has "sessionId" key)
                if isinstance(data, dict) and "sessionId" in data:
                    session_id_str = data["sessionId"]["id"]
                    continue

                # Line 3: port mappings (list of dicts with vm_port, local_port)
                if isinstance(data, list):
                    for mapping in data:
                        if mapping.get("vm_port") == 22:
                            ssh_port = mapping["local_port"]
                    break

            if not session_id_str:
                proc.terminate()
                raise RuntimeError("Failed to get session ID from vacli")

            if not ssh_port:
                proc.terminate()
                raise RuntimeError("Failed to get SSH port mapping from vacli")

            self._vacli_processes[session_id_str] = proc
            self._ssh_ports[session_id_str] = ssh_port
            self._count_api_call()

            # Wait for SSH to be ready and establish persistent connection
            await self._wait_for_ssh(session_id_str, timeout_seconds=60)

            return session_id_str

        except Exception:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise

    async def _wait_for_ssh(
        self, sandbox_id: str, timeout_seconds: int = 60
    ) -> None:
        """Wait for SSH to become available and establish a persistent connection."""
        port = self._ssh_ports[sandbox_id]
        start = time.time()

        while time.time() - start < timeout_seconds:
            try:
                client = await asyncio.get_event_loop().run_in_executor(
                    None, self._connect_ssh, port
                )
                self._ssh_clients[sandbox_id] = client
                self._count_api_call()
                return
            except Exception:
                await asyncio.sleep(0.5)

        raise RuntimeError(f"SSH not ready after {timeout_seconds}s")

    def _exec_ssh_command(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute a command via SSH (blocking, runs in executor)."""
        client = self._get_ssh(sandbox_id)
        stdin_ch, stdout_ch, stderr_ch = client.exec_command(
            command, timeout=timeout_seconds
        )
        # Wait for command to complete
        exit_code = stdout_ch.channel.recv_exit_status()
        stdout = stdout_ch.read().decode("utf-8", errors="replace")
        stderr = stderr_ch.read().decode("utf-8", errors="replace")
        return (stdout, stderr, exit_code)

    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code in the VM via SSH."""
        if language == "python":
            remote_cmd = f"python3 -c {shlex.quote(code)}"
        else:
            remote_cmd = code

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._exec_ssh_command(
                    sandbox_id, remote_cmd, timeout_seconds
                ),
            )
            self._count_api_call()
            return result
        except Exception as e:
            if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                self._count_api_call()
                return ("", f"Timeout after {timeout_seconds}s", 1)
            return ("", str(e), 1)

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute a shell command in the VM via SSH."""
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._exec_ssh_command(
                    sandbox_id, command, timeout_seconds
                ),
            )
            self._count_api_call()
            return result
        except Exception as e:
            if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                self._count_api_call()
                return ("", f"Timeout after {timeout_seconds}s", 1)
            return ("", str(e), 1)

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write a file to the VM via SFTP."""
        if isinstance(content, str):
            content = content.encode("utf-8")

        # Ensure parent directory exists
        parent_dir = "/".join(path.rsplit("/", 1)[:-1]) or "/"
        if parent_dir != "/":
            await self.execute_command(
                sandbox_id, f"mkdir -p {shlex.quote(parent_dir)}"
            )

        def _write():
            client = self._get_ssh(sandbox_id)
            sftp = client.open_sftp()
            try:
                with sftp.file(path, "wb") as f:
                    f.write(content)
            finally:
                sftp.close()

        await asyncio.get_event_loop().run_in_executor(None, _write)
        self._count_api_call()

    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read a file from the VM via SFTP."""

        def _read():
            client = self._get_ssh(sandbox_id)
            sftp = client.open_sftp()
            try:
                with sftp.file(path, "rb") as f:
                    return f.read().decode("utf-8")
            finally:
                sftp.close()

        result = await asyncio.get_event_loop().run_in_executor(None, _read)
        self._count_api_call()
        return result

    async def destroy(self, sandbox_id: str) -> None:
        """Release the VMVM lease by closing SSH and terminating vacli."""
        # Close SSH connection
        client = self._ssh_clients.pop(sandbox_id, None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

        # Terminate vacli (which triggers --release-on-exit)
        proc = self._vacli_processes.pop(sandbox_id, None)
        self._ssh_ports.pop(sandbox_id, None)

        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            self._count_api_call()

    async def get_status(self, sandbox_id: str) -> str:
        """Get VM status."""
        proc = self._vacli_processes.get(sandbox_id)
        if proc is None:
            return "stopped"
        if proc.poll() is None:
            return "running"
        return "stopped"

    def get_discoverability_score(self) -> float:
        """VMVM discoverability score.

        VMVM has extensive internal docs and wiki pages but no public-facing
        MCP server, OpenAPI spec, or llms.txt. Internal wiki + examples = 3.5.
        """
        return 3.5


register_provider(VMVMProvider)
