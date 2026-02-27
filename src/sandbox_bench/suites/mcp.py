"""MCP (Model Context Protocol) server test suite.

Tests whether sandbox providers can run MCP servers — the infrastructure
foundation for AI tool-use workflows.  Each test launches an MCP server
via stdin/stdout, sends JSON-RPC messages, and verifies responses.
"""

import time
from typing import List

from . import PhaseResult, TestSuite, register_suite
from ..provider import SandboxProvider

# ---------------------------------------------------------------------------
# Shared Python harness helpers injected into every sandbox script
# ---------------------------------------------------------------------------

_MCP_HARNESS_HEADER = r'''
import subprocess, json, sys, os, signal, threading, select

# Ensure uv/uvx is on PATH (may have been installed to ~/.local/bin)
_local_bin = os.path.expanduser("~/.local/bin")
if _local_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _local_bin + ":" + os.environ.get("PATH", "")

_stderr_lines = []
_use_content_length = None  # Auto-detected on first recv

def _drain_stderr(proc):
    """Drain stderr in a background thread to prevent deadlock."""
    try:
        for line in proc.stderr:
            _stderr_lines.append(line.decode(errors="replace"))
    except Exception:
        pass

def _start_server(cmd):
    """Start an MCP server and drain its stderr."""
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    t = threading.Thread(target=_drain_stderr, args=(proc,), daemon=True)
    t.start()
    return proc

def _mcp_send(proc, msg):
    """Send a JSON-RPC message.

    Newer MCP servers (Python SDK) use newline-delimited JSON.
    Older ones (Node SDK) use Content-Length framing.
    We auto-detect on first recv and match the server's protocol.
    """
    global _use_content_length
    data = json.dumps(msg)
    if _use_content_length:
        header = f"Content-Length: {len(data)}\r\n\r\n"
        proc.stdin.write(header.encode() + data.encode())
    else:
        # Newline-delimited JSON (default for Python MCP SDK)
        proc.stdin.write(data.encode() + b"\n")
    proc.stdin.flush()

def _mcp_recv(proc, timeout=30):
    """Read one JSON-RPC response, auto-detecting protocol on first call."""
    global _use_content_length

    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if not ready:
        raise RuntimeError(f"Timeout waiting for server response ({timeout}s). stderr: {''.join(_stderr_lines)[:300]}")

    first = proc.stdout.read(1)
    if not first:
        raise RuntimeError(f"Server closed stdout. stderr: {''.join(_stderr_lines)[:300]}")

    if first == b"C" and _use_content_length is None:
        # Content-Length framing detected
        _use_content_length = True

    if _use_content_length or first == b"C":
        # Read rest of Content-Length header
        headers = first
        while not headers.endswith(b"\r\n\r\n"):
            ready, _, _ = select.select([proc.stdout], [], [], timeout)
            if not ready:
                raise RuntimeError(f"Timeout reading headers ({timeout}s)")
            ch = proc.stdout.read(1)
            if not ch:
                raise RuntimeError("Server closed stdout during headers")
            headers += ch
        length = None
        for line in headers.decode().split("\r\n"):
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
        if length is None:
            raise RuntimeError(f"No Content-Length in: {headers!r}")
        body = b""
        while len(body) < length:
            ready, _, _ = select.select([proc.stdout], [], [], timeout)
            if not ready:
                raise RuntimeError(f"Timeout reading body ({timeout}s)")
            chunk = proc.stdout.read(length - len(body))
            if not chunk:
                raise RuntimeError("Server closed stdout mid-body")
            body += chunk
        return json.loads(body)
    else:
        # Newline-delimited JSON
        if _use_content_length is None:
            _use_content_length = False
        rest = proc.stdout.readline()
        line = (first + rest).decode().strip()
        if not line:
            raise RuntimeError("Empty line from server")
        return json.loads(line)

def _mcp_recv_response(proc, expected_id, timeout=30):
    """Read responses until we get one with the expected id (skip notifications)."""
    deadline = __import__("time").time() + timeout
    while True:
        remaining = deadline - __import__("time").time()
        if remaining <= 0:
            raise RuntimeError(f"Timeout waiting for response id={expected_id}")
        msg = _mcp_recv(proc, timeout=remaining)
        if "id" in msg and msg["id"] == expected_id:
            return msg
        # else it's a notification, skip it

def _mcp_init(proc):
    """Send initialize + initialized notification."""
    global _use_content_length
    # First message: try newline-delimited JSON (most common for Python MCP SDK)
    # The auto-detect in _mcp_recv will correct if server uses Content-Length
    if _use_content_length is None:
        _use_content_length = False
    _mcp_send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "sandbox-bench", "version": "1.0.0"}
        }
    })
    resp = _mcp_recv_response(proc, 1)
    _mcp_send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    return resp
'''


class McpSuite(TestSuite):
    """MCP server infrastructure tests: npx, uvx, stdio protocol,
    filesystem, fetch, and multi-server concurrency."""

    name = "mcp"
    description = "MCP server launch, JSON-RPC stdio, filesystem, fetch, multi-server"

    async def run(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> List[PhaseResult]:
        results: list[PhaseResult] = []

        # Phase 1 & 2: check/install tool runners
        results.append(await self._npx_available(provider, sandbox_id))
        results.append(await self._uvx_available(provider, sandbox_id))

        # Determine what's available for downstream tests
        npx_ok = results[0].success
        uvx_ok = results[1].success

        # Pre-warm: install MCP server packages so the protocol tests
        # don't spend their timeout on downloads.
        await self._prewarm_packages(provider, sandbox_id, npx_ok, uvx_ok)

        # Phase 3: calculator (requires uvx)
        results.append(await self._mcp_stdio_calculator(provider, sandbox_id, uvx_ok))

        # Phase 4: filesystem (requires npx)
        results.append(await self._mcp_filesystem(provider, sandbox_id, npx_ok))

        # Phase 5: fetch (requires uvx + network)
        results.append(await self._mcp_fetch(provider, sandbox_id, uvx_ok))

        # Phase 6: multi-server (requires both)
        results.append(await self._mcp_multi_server(provider, sandbox_id, npx_ok, uvx_ok))

        return results

    async def _prewarm_packages(
        self, provider: SandboxProvider, sandbox_id: str,
        npx_ok: bool, uvx_ok: bool,
    ) -> None:
        """Pre-install MCP server packages so protocol tests are fast."""
        try:
            if npx_ok:
                await provider.execute_command(
                    sandbox_id,
                    "npm install -g @modelcontextprotocol/server-filesystem@2025.1.14 2>&1",
                    timeout_seconds=60,
                )
            if uvx_ok:
                # Install MCP server tools persistently so they're
                # available as commands in ~/.local/bin/
                await provider.execute_command(
                    sandbox_id,
                    "export PATH=$HOME/.local/bin:$PATH && "
                    "uv tool install mcp-server-calculator==0.2.0 2>&1; "
                    "uv tool install mcp-server-fetch==2025.1.17 2>&1; "
                    "true",
                    timeout_seconds=90,
                )
        except Exception:
            pass  # Best-effort; tests will handle failures

    # ── Phase 1: npx available ────────────────────────────────────────

    async def _npx_available(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        try:
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, "npx --version"
            )
            success = exit_code == 0 and stdout.strip() != ""
            return PhaseResult(
                name="npx_available",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=0 if success else 1,
                capability_tested="npx_mcp",
                capability_supported=success,
                details={"version": stdout.strip(), "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="npx_available",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=1,
                friction_points=1,
                capability_tested="npx_mcp",
                capability_supported=False,
                error_messages=[str(e)],
            )

    # ── Phase 2: uvx available ────────────────────────────────────────

    async def _uvx_available(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        t0 = time.time()
        tool_calls = 0
        try:
            # Check if uvx is already available
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id, "uvx --version"
            )
            tool_calls += 1

            if exit_code != 0:
                # Install uv standalone (provides the real uvx binary).
                # Try curl first, then wget as fallback.
                stdout2, stderr2, exit_code2 = await provider.execute_command(
                    sandbox_id,
                    "( curl -LsSf https://astral.sh/uv/install.sh 2>/dev/null "
                    "|| wget -qO- https://astral.sh/uv/install.sh ) | sh 2>&1 "
                    "&& export PATH=$HOME/.local/bin:$PATH "
                    "&& uvx --version",
                    timeout_seconds=60,
                )
                tool_calls += 1
                if exit_code2 == 0:
                    # Extract just the version line from the output
                    for line in stdout2.strip().splitlines():
                        if line.startswith("uvx"):
                            stdout = line
                            break
                    else:
                        stdout = stdout2.strip().splitlines()[-1] if stdout2.strip() else ""
                    exit_code = 0

            success = exit_code == 0 and stdout.strip() != ""
            return PhaseResult(
                name="uvx_available",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=tool_calls,
                friction_points=0 if success else 1,
                capability_tested="uvx_mcp",
                capability_supported=success,
                details={"version": stdout.strip(), "exit_code": exit_code},
            )
        except Exception as e:
            return PhaseResult(
                name="uvx_available",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=tool_calls,
                friction_points=1,
                capability_tested="uvx_mcp",
                capability_supported=False,
                error_messages=[str(e)],
            )

    # ── Phase 3: MCP stdio calculator ─────────────────────────────────

    async def _mcp_stdio_calculator(
        self, provider: SandboxProvider, sandbox_id: str, uvx_ok: bool
    ) -> PhaseResult:
        t0 = time.time()
        if not uvx_ok:
            return PhaseResult(
                name="mcp_stdio_calculator",
                success=False,
                duration_seconds=time.time() - t0,
                friction_points=1,
                capability_tested="mcp_stdio",
                capability_supported=False,
                error_messages=["Skipped: uvx not available"],
            )

        script = _MCP_HARNESS_HEADER + r'''
try:
    # Use installed binary (from uv tool install) or fall back to uvx
    import shutil
    calc_bin = shutil.which("mcp-server-calculator")
    if not calc_bin:
        calc_bin = shutil.which("uvx")
        cmd = [calc_bin, "mcp-server-calculator@0.2.0"] if calc_bin else None
    else:
        cmd = [calc_bin]
    if not cmd:
        raise RuntimeError("Neither mcp-server-calculator nor uvx found on PATH")
    proc = _start_server(cmd)
    _mcp_init(proc)
    # Call the calculate tool: evaluate "2 + 3"
    _mcp_send(proc, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "calculate", "arguments": {"expression": "2 + 3"}}
    })
    result = _mcp_recv_response(proc, 2)
    proc.kill()
    proc.wait()
    content = result.get("result", {}).get("content", [{}])
    text = content[0].get("text", "") if content else ""
    # Result should contain "5" (may be "5" or "5.0" depending on server)
    success = "5" in text
    print(json.dumps({"success": success, "result_text": text, "response": result}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
'''
        try:
            await provider.write_file(sandbox_id, "/tmp/mcp_calc_test.py", script)
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id,
                "python3 /tmp/mcp_calc_test.py || python /tmp/mcp_calc_test.py",
                timeout_seconds=60,
            )
            parsed = _parse_json_output(stdout)
            success = parsed.get("success", False)
            return PhaseResult(
                name="mcp_stdio_calculator",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=0 if success else 1,
                capability_tested="mcp_stdio",
                capability_supported=success,
                details={
                    "stdout": stdout[:1000],
                    "stderr": stderr[:500],
                    "exit_code": exit_code,
                    "parsed": parsed,
                },
            )
        except Exception as e:
            return PhaseResult(
                name="mcp_stdio_calculator",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=1,
                capability_tested="mcp_stdio",
                capability_supported=False,
                error_messages=[str(e)],
            )

    # ── Phase 4: MCP filesystem ───────────────────────────────────────

    async def _mcp_filesystem(
        self, provider: SandboxProvider, sandbox_id: str, npx_ok: bool
    ) -> PhaseResult:
        t0 = time.time()
        if not npx_ok:
            return PhaseResult(
                name="mcp_filesystem",
                success=False,
                duration_seconds=time.time() - t0,
                friction_points=1,
                capability_tested="mcp_filesystem",
                capability_supported=False,
                error_messages=["Skipped: npx not available"],
            )

        # Pre-install the npm package in a separate step so we can
        # distinguish install failures from MCP protocol failures.
        try:
            await provider.execute_command(
                sandbox_id,
                "npm install -g @modelcontextprotocol/server-filesystem@2025.1.14 2>&1",
                timeout_seconds=60,
            )
        except Exception:
            pass  # Best-effort; npx -y will retry below

        script = _MCP_HARNESS_HEADER + r'''
try:
    # Create a test file so we have something to list
    os.makedirs("/tmp/mcp-fs-test", exist_ok=True)
    with open("/tmp/mcp-fs-test/hello.txt", "w") as f:
        f.write("hello from sandbox-bench")

    proc = _start_server(
        ["npx", "-y", "@modelcontextprotocol/server-filesystem@2025.1.14", "/tmp/mcp-fs-test"]
    )
    _mcp_init(proc)
    # List the directory
    _mcp_send(proc, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "list_directory", "arguments": {"path": "/tmp/mcp-fs-test"}}
    })
    result = _mcp_recv_response(proc, 2)
    proc.kill()
    proc.wait()
    content = result.get("result", {}).get("content", [{}])
    text = content[0].get("text", "") if content else ""
    success = "hello.txt" in text
    print(json.dumps({"success": success, "result_text": text, "response": result}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
'''
        try:
            await provider.write_file(sandbox_id, "/tmp/mcp_fs_test.py", script)
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id,
                "python3 /tmp/mcp_fs_test.py || python /tmp/mcp_fs_test.py",
                timeout_seconds=120,
            )
            parsed = _parse_json_output(stdout)
            success = parsed.get("success", False)
            return PhaseResult(
                name="mcp_filesystem",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=3,  # npm install + write_file + execute
                friction_points=0 if success else 1,
                capability_tested="mcp_filesystem",
                capability_supported=success,
                details={
                    "stdout": stdout[:1000],
                    "stderr": stderr[:500],
                    "exit_code": exit_code,
                    "parsed": parsed,
                },
            )
        except Exception as e:
            return PhaseResult(
                name="mcp_filesystem",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=3,
                friction_points=1,
                capability_tested="mcp_filesystem",
                capability_supported=False,
                error_messages=[str(e)],
            )

    # ── Phase 5: MCP fetch ────────────────────────────────────────────

    async def _mcp_fetch(
        self, provider: SandboxProvider, sandbox_id: str, uvx_ok: bool
    ) -> PhaseResult:
        t0 = time.time()
        if not uvx_ok:
            return PhaseResult(
                name="mcp_fetch",
                success=False,
                duration_seconds=time.time() - t0,
                friction_points=1,
                capability_tested="mcp_network",
                capability_supported=False,
                error_messages=["Skipped: uvx not available"],
            )

        script = _MCP_HARNESS_HEADER + r'''
try:
    import shutil
    fetch_bin = shutil.which("mcp-server-fetch")
    if not fetch_bin:
        fetch_bin = shutil.which("uvx")
        cmd = [fetch_bin, "mcp-server-fetch@2025.1.17"] if fetch_bin else None
    else:
        cmd = [fetch_bin]
    if not cmd:
        raise RuntimeError("Neither mcp-server-fetch nor uvx found on PATH")
    proc = _start_server(cmd)
    _mcp_init(proc)
    _mcp_send(proc, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "fetch", "arguments": {"url": "https://httpbin.org/get"}}
    })
    result = _mcp_recv_response(proc, 2, timeout=30)
    proc.kill()
    proc.wait()
    content = result.get("result", {}).get("content", [{}])
    text = content[0].get("text", "") if content else ""
    # httpbin /get returns JSON with "url" field
    success = "httpbin.org" in text
    print(json.dumps({"success": success, "result_text": text[:500], "response_keys": list(result.keys())}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
'''
        try:
            await provider.write_file(sandbox_id, "/tmp/mcp_fetch_test.py", script)
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id,
                "python3 /tmp/mcp_fetch_test.py || python /tmp/mcp_fetch_test.py",
                timeout_seconds=90,
            )
            parsed = _parse_json_output(stdout)
            success = parsed.get("success", False)
            return PhaseResult(
                name="mcp_fetch",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=0 if success else 1,
                capability_tested="mcp_network",
                capability_supported=success,
                details={
                    "stdout": stdout[:1000],
                    "stderr": stderr[:500],
                    "exit_code": exit_code,
                    "parsed": parsed,
                },
            )
        except Exception as e:
            return PhaseResult(
                name="mcp_fetch",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=1,
                capability_tested="mcp_network",
                capability_supported=False,
                error_messages=[str(e)],
            )

    # ── Phase 6: MCP multi-server ─────────────────────────────────────

    async def _mcp_multi_server(
        self, provider: SandboxProvider, sandbox_id: str,
        npx_ok: bool, uvx_ok: bool
    ) -> PhaseResult:
        t0 = time.time()
        if not (npx_ok and uvx_ok):
            missing = []
            if not npx_ok:
                missing.append("npx")
            if not uvx_ok:
                missing.append("uvx")
            return PhaseResult(
                name="mcp_multi_server",
                success=False,
                duration_seconds=time.time() - t0,
                friction_points=1,
                capability_tested="mcp_multi",
                capability_supported=False,
                error_messages=[f"Skipped: {', '.join(missing)} not available"],
            )

        script = _MCP_HARNESS_HEADER + r'''
try:
    # Create test directory for filesystem server
    os.makedirs("/tmp/mcp-multi-test", exist_ok=True)
    with open("/tmp/mcp-multi-test/data.txt", "w") as f:
        f.write("multi-server-test")

    import shutil
    # Find calculator binary
    calc_bin = shutil.which("mcp-server-calculator")
    if not calc_bin:
        calc_bin = shutil.which("uvx")
        calc_cmd = [calc_bin, "mcp-server-calculator@0.2.0"] if calc_bin else None
    else:
        calc_cmd = [calc_bin]
    if not calc_cmd:
        raise RuntimeError("Neither mcp-server-calculator nor uvx found on PATH")

    # Launch two servers concurrently
    calc_proc = _start_server(calc_cmd)
    fs_proc = _start_server(
        ["npx", "-y", "@modelcontextprotocol/server-filesystem@2025.1.14", "/tmp/mcp-multi-test"]
    )

    # Initialize both
    _mcp_init(calc_proc)
    _mcp_init(fs_proc)

    # Call calculator: evaluate 10 + 20
    _mcp_send(calc_proc, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "calculate", "arguments": {"expression": "10 + 20"}}
    })
    calc_result = _mcp_recv_response(calc_proc, 2)

    # Call filesystem: list_directory
    _mcp_send(fs_proc, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "list_directory", "arguments": {"path": "/tmp/mcp-multi-test"}}
    })
    fs_result = _mcp_recv_response(fs_proc, 2)

    # Cleanup
    calc_proc.kill()
    calc_proc.wait()
    fs_proc.kill()
    fs_proc.wait()

    # Verify both
    calc_content = calc_result.get("result", {}).get("content", [{}])
    calc_text = calc_content[0].get("text", "") if calc_content else ""
    calc_ok = "30" in calc_text

    fs_content = fs_result.get("result", {}).get("content", [{}])
    fs_text = fs_content[0].get("text", "") if fs_content else ""
    fs_ok = "data.txt" in fs_text

    success = calc_ok and fs_ok
    print(json.dumps({
        "success": success,
        "calc_ok": calc_ok, "calc_text": calc_text,
        "fs_ok": fs_ok, "fs_text": fs_text,
    }))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
'''
        try:
            await provider.write_file(
                sandbox_id, "/tmp/mcp_multi_test.py", script
            )
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id,
                "python3 /tmp/mcp_multi_test.py || python /tmp/mcp_multi_test.py",
                timeout_seconds=120,
            )
            parsed = _parse_json_output(stdout)
            success = parsed.get("success", False)
            return PhaseResult(
                name="mcp_multi_server",
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=0 if success else 1,
                capability_tested="mcp_multi",
                capability_supported=success,
                details={
                    "stdout": stdout[:1000],
                    "stderr": stderr[:500],
                    "exit_code": exit_code,
                    "parsed": parsed,
                },
            )
        except Exception as e:
            return PhaseResult(
                name="mcp_multi_server",
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=1,
                capability_tested="mcp_multi",
                capability_supported=False,
                error_messages=[str(e)],
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_output(stdout: str) -> dict:
    """Parse JSON from the last line of stdout (scripts may emit warnings before)."""
    import json as _json
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return _json.loads(line)
            except _json.JSONDecodeError:
                continue
    return {}


register_suite(McpSuite)
