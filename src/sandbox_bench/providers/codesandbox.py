"""CodeSandbox provider implementation using the pitcher WebSocket protocol."""

import asyncio
import httpx
import msgpack
import websockets
from typing import Optional

from ..provider import SandboxProvider, ProviderInfo, register_provider

# Default template for VM-backed Devbox sandboxes
DEFAULT_TEMPLATE_ID = "pcz35m"

DEFAULT_SUBSCRIPTIONS = {
    "client": {"status": True},
    "shell": {"status": True},
    "fs": {"operations": True},
}


class PitcherClient:
    """Minimal pitcher protocol client over WebSocket."""

    def __init__(self):
        self._ws = None
        self._msg_id = 0
        self._pending = {}  # id -> Future
        self._notifications = {}  # method -> list of callbacks
        self._listener_task = None

    async def connect(self, url: str, token: str):
        """Connect WebSocket and perform client/join handshake."""
        ws_url = f"{url}/?token={token}"
        self._ws = await websockets.connect(ws_url, max_size=10 * 1024 * 1024)
        self._listener_task = asyncio.create_task(self._listen())

        # Perform client/join handshake
        result = await self.request("client/join", {
            "clientInfo": {
                "protocolVersion": "2.0.0",
                "appId": "sdk",
            },
            "asyncProgress": True,
            "subscriptions": DEFAULT_SUBSCRIPTIONS,
        })
        return result

    async def _listen(self):
        """Background listener for WebSocket messages."""
        try:
            async for raw in self._ws:
                if isinstance(raw, str):
                    # Ping/pong - empty string is heartbeat
                    if raw == "":
                        try:
                            await self._ws.send("")
                        except Exception:
                            pass
                    continue

                try:
                    msg = msgpack.unpackb(raw, raw=False)
                except Exception:
                    continue

                if "id" in msg and msg["id"] in self._pending:
                    # Response to a request
                    fut = self._pending.pop(msg["id"])
                    if msg.get("status") == 0:  # RESOLVED
                        fut.set_result(msg.get("result"))
                    else:
                        error = msg.get("error", {})
                        fut.set_exception(
                            Exception(error.get("message", "Unknown error"))
                        )
                elif "method" in msg and "params" in msg and "id" not in msg:
                    # Notification
                    method = msg["method"]
                    if method in self._notifications:
                        for cb in self._notifications[method]:
                            cb(msg["params"])
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            pass

    def on_notification(self, method: str, callback):
        """Register a notification listener."""
        self._notifications.setdefault(method, []).append(callback)

    async def request(self, method: str, params, timeout: float = 30.0):
        """Send a request and wait for the response."""
        self._msg_id += 1
        msg_id = self._msg_id

        payload = msgpack.packb({
            "id": msg_id,
            "method": method,
            "params": params,
        })

        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._pending[msg_id] = fut

        await self._ws.send(payload)
        return await asyncio.wait_for(fut, timeout=timeout)

    async def close(self):
        """Close the WebSocket connection."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws:
            await self._ws.close()


class CodeSandboxProvider(SandboxProvider):
    """CodeSandbox provider (Firecracker microVM-based)."""

    name = "codesandbox"
    info = ProviderInfo(
        name="CodeSandbox",
        description="Cloud development environments powered by microVMs",
        docs_url="https://codesandbox.io/docs/sdk",
        pricing_url="https://codesandbox.io/pricing",
        mcp_server=False,
        openapi_spec=True,
        llms_txt=False,
    )

    MANAGEMENT_URL = "https://api.codesandbox.io"

    def __init__(self):
        self._api_key = None
        self._mgmt_client = None
        self._pitcher = None
        self._sandbox_id = None
        self._workspace_path = "/project/sandbox"

    async def authenticate(self, api_key: str) -> None:
        """Authenticate with CodeSandbox using a Bearer API token."""
        self._api_key = api_key
        self._mgmt_client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        # Validate the token
        resp = await self._mgmt_client.get(
            f"{self.MANAGEMENT_URL}/sandbox",
            params={"page_size": 1},
        )
        resp.raise_for_status()

    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """Create and start a CodeSandbox Devbox, connecting via pitcher."""
        template_id = image or DEFAULT_TEMPLATE_ID

        # Fork from template (creates a VM-backed Devbox)
        fork_resp = await self._mgmt_client.post(
            f"{self.MANAGEMENT_URL}/sandbox/{template_id}/fork",
            json={},
        )
        fork_resp.raise_for_status()
        fork_data = fork_resp.json()
        self._sandbox_id = fork_data["data"]["id"]

        # Start the VM
        start_resp = await self._mgmt_client.post(
            f"{self.MANAGEMENT_URL}/vm/{self._sandbox_id}/start",
            json={},
        )
        start_resp.raise_for_status()
        start_data = start_resp.json()["data"]

        pitcher_url = start_data["pitcher_url"]
        pitcher_token = start_data["pitcher_token"]
        self._workspace_path = start_data.get(
            "user_workspace_path", "/project/sandbox"
        )

        # Connect via pitcher WebSocket protocol
        self._pitcher = PitcherClient()
        await self._pitcher.connect(pitcher_url, pitcher_token)

        return self._sandbox_id

    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """Execute code via pitcher shell/create."""
        if language == "python":
            command = f"python3 -c {_shell_quote(code)}"
        else:
            command = code

        # Collect output from notifications
        output_parts = []
        exit_info = {"code": None}
        output_event = asyncio.Event()

        def on_shell_out(params):
            output_parts.append(params.get("out", ""))

        def on_shell_exit(params):
            exit_info["code"] = params.get("exitCode", 0)
            output_event.set()

        self._pitcher.on_notification("shell/out", on_shell_out)
        self._pitcher.on_notification("shell/exit", on_shell_exit)

        try:
            # Create a COMMAND shell (runs to completion)
            result = await self._pitcher.request("shell/create", {
                "command": command,
                "cwd": self._workspace_path,
                "type": "COMMAND",
                "size": {"cols": 200, "rows": 50},
            })

            # The initial buffer may contain output
            buffer = result.get("buffer", [])
            if buffer:
                output_parts.extend(buffer)

            # If already finished, exit event might already have fired or
            # the status might be in the result
            status = result.get("status", "")
            if status in ("FINISHED", "ERROR", "KILLED"):
                exit_code = result.get("exitCode", 0 if status == "FINISHED" else 1)
            else:
                # Wait for exit notification
                try:
                    await asyncio.wait_for(
                        output_event.wait(), timeout=timeout_seconds
                    )
                    exit_code = exit_info["code"] if exit_info["code"] is not None else 0
                except asyncio.TimeoutError:
                    exit_code = -1
        finally:
            # Clean up listeners
            self._pitcher._notifications.get("shell/out", []).clear()
            self._pitcher._notifications.get("shell/exit", []).clear()

        stdout = "".join(output_parts)
        return (stdout, "", exit_code)

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """Write file via pitcher fs/writeFile."""
        if isinstance(content, str):
            content = content.encode("utf-8")

        await self._pitcher.request("fs/writeFile", {
            "path": path,
            "content": content,
            "create": True,
            "overwrite": True,
        })

    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """Read file via pitcher fs/readFile."""
        result = await self._pitcher.request("fs/readFile", {
            "path": path,
        })
        content = result.get("content", b"")
        if isinstance(content, bytes):
            return content.decode("utf-8")
        return content

    async def destroy(self, sandbox_id: str) -> None:
        """Shutdown and delete the CodeSandbox sandbox."""
        try:
            if self._pitcher:
                await self._pitcher.close()
        except Exception:
            pass
        try:
            if self._mgmt_client and self._sandbox_id:
                try:
                    await self._mgmt_client.post(
                        f"{self.MANAGEMENT_URL}/vm/{self._sandbox_id}/shutdown",
                    )
                except Exception:
                    pass
                await self._mgmt_client.delete(
                    f"{self.MANAGEMENT_URL}/vm/{self._sandbox_id}",
                )
        except Exception:
            pass
        finally:
            if self._mgmt_client:
                await self._mgmt_client.aclose()


def _shell_quote(s: str) -> str:
    """Quote a string for safe shell usage."""
    return "'" + s.replace("'", "'\\''") + "'"


# Register the provider
register_provider(CodeSandboxProvider)
