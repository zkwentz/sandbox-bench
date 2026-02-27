"""Networking protocol test suite.

Comprehensively maps the networking surface area of each sandbox provider:
DNS, ICMP, TCP, UDP, HTTP, WebSocket, IPv6, inbound listening, SSH,
bandwidth, and concurrent connections.  All tests use Python stdlib only.
"""

import time
from typing import List

from . import PhaseResult, TestSuite, register_suite
from ..provider import SandboxProvider


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


class NetworkingSuite(TestSuite):
    """Networking protocol and connectivity tests across L3/L4/L7."""

    name = "networking"
    description = "DNS, ICMP, TCP, UDP, HTTP, WebSocket, IPv6, inbound, SSH, bandwidth, concurrency"

    async def run(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
    ) -> List[PhaseResult]:
        results: list[PhaseResult] = []

        # All 14 phases are independent
        results.append(await self._dns_resolution(provider, sandbox_id))
        results.append(await self._dns_record_types(provider, sandbox_id))
        results.append(await self._icmp_ping(provider, sandbox_id))
        results.append(await self._tcp_outbound_443(provider, sandbox_id))
        results.append(await self._tcp_nonstandard_port(provider, sandbox_id))
        results.append(await self._udp_outbound(provider, sandbox_id))
        results.append(await self._https_get(provider, sandbox_id))
        results.append(await self._http_post(provider, sandbox_id))
        results.append(await self._websocket_connect(provider, sandbox_id))
        results.append(await self._ipv6_connectivity(provider, sandbox_id))
        results.append(await self._inbound_listen(provider, sandbox_id))
        results.append(await self._ssh_outbound(provider, sandbox_id))
        results.append(await self._bandwidth_estimate(provider, sandbox_id))
        results.append(await self._concurrent_connections(provider, sandbox_id))

        return results

    # ── helpers ───────────────────────────────────────────────────────

    async def _run_python_test(
        self,
        provider: SandboxProvider,
        sandbox_id: str,
        phase_name: str,
        capability: str,
        script: str,
        timeout: int = 30,
    ) -> PhaseResult:
        """Write a Python script, execute it, parse JSON output."""
        t0 = time.time()
        try:
            filename = f"/tmp/net_{phase_name}.py"
            await provider.write_file(sandbox_id, filename, script)
            stdout, stderr, exit_code = await provider.execute_command(
                sandbox_id,
                f"python3 {filename} || python {filename}",
                timeout_seconds=timeout,
            )
            parsed = _parse_json_output(stdout)
            success = parsed.get("success", False)
            return PhaseResult(
                name=phase_name,
                success=success,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=0 if success else 1,
                capability_tested=capability,
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
                name=phase_name,
                success=False,
                duration_seconds=time.time() - t0,
                tool_calls=2,
                friction_points=1,
                capability_tested=capability,
                capability_supported=False,
                error_messages=[str(e)],
            )

    # ── Phase 1: DNS A-record resolution ─────────────────────────────

    async def _dns_resolution(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import socket, json
try:
    results = socket.getaddrinfo("example.com", 80, socket.AF_INET, socket.SOCK_STREAM)
    addrs = [r[4][0] for r in results]
    success = len(addrs) > 0
    print(json.dumps({"success": success, "addresses": addrs}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "dns_resolution", "dns_resolution", script
        )

    # ── Phase 2: DNS AAAA records (IPv6 DNS) ─────────────────────────

    async def _dns_record_types(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import socket, json
try:
    results = socket.getaddrinfo("example.com", 80, socket.AF_INET6, socket.SOCK_STREAM)
    addrs = [r[4][0] for r in results]
    success = len(addrs) > 0
    print(json.dumps({"success": success, "ipv6_addresses": addrs}))
except socket.gaierror as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": "gaierror"}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "dns_record_types", "dns_records", script
        )

    # ── Phase 3: ICMP ping ───────────────────────────────────────────

    async def _icmp_ping(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import subprocess, json
try:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", "3", "8.8.8.8"],
        capture_output=True, text=True, timeout=10
    )
    success = result.returncode == 0
    print(json.dumps({
        "success": success,
        "stdout": result.stdout[:500],
        "stderr": result.stderr[:500],
        "returncode": result.returncode
    }))
except FileNotFoundError:
    print(json.dumps({"success": False, "error": "ping not found", "error_type": "not_found"}))
except subprocess.TimeoutExpired:
    print(json.dumps({"success": False, "error": "ping timed out", "error_type": "timeout"}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "icmp_ping", "icmp_ping", script
        )

    # ── Phase 4: TCP outbound port 443 ───────────────────────────────

    async def _tcp_outbound_443(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import socket, json, time
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    t0 = time.time()
    sock.connect(("httpbin.org", 443))
    latency_ms = (time.time() - t0) * 1000
    peer = sock.getpeername()
    sock.close()
    print(json.dumps({"success": True, "peer": list(peer), "latency_ms": round(latency_ms, 1)}))
except socket.timeout:
    print(json.dumps({"success": False, "error": "connection timed out", "error_type": "timeout"}))
except ConnectionRefusedError:
    print(json.dumps({"success": False, "error": "connection refused", "error_type": "refused"}))
except PermissionError as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": "permission_denied"}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "tcp_outbound_443", "tcp_outbound", script
        )

    # ── Phase 5: TCP non-standard port ───────────────────────────────

    async def _tcp_nonstandard_port(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import socket, json, time
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    t0 = time.time()
    sock.connect(("portquiz.net", 8080))
    latency_ms = (time.time() - t0) * 1000
    peer = sock.getpeername()
    sock.close()
    print(json.dumps({"success": True, "peer": list(peer), "latency_ms": round(latency_ms, 1)}))
except socket.timeout:
    print(json.dumps({"success": False, "error": "connection timed out", "error_type": "timeout"}))
except ConnectionRefusedError:
    print(json.dumps({"success": False, "error": "connection refused", "error_type": "refused"}))
except PermissionError as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": "permission_denied"}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "tcp_nonstandard_port", "tcp_nonstandard_port", script
        )

    # ── Phase 6: UDP outbound (DNS query) ────────────────────────────

    async def _udp_outbound(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import socket, json, struct, time
try:
    # Build a minimal DNS query for example.com A record
    txn_id = 0x1234
    flags = 0x0100  # standard query, recursion desired
    header = struct.pack("!HHHHHH", txn_id, flags, 1, 0, 0, 0)
    # Encode "example.com"
    qname = b""
    for part in "example.com".split("."):
        qname += bytes([len(part)]) + part.encode()
    qname += b"\x00"
    question = qname + struct.pack("!HH", 1, 1)  # A record, IN class
    query = header + question

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    t0 = time.time()
    sock.sendto(query, ("8.8.8.8", 53))
    data, addr = sock.recvfrom(512)
    latency_ms = (time.time() - t0) * 1000
    sock.close()

    # Parse response: check we got an answer
    resp_id = struct.unpack("!H", data[:2])[0]
    ancount = struct.unpack("!H", data[6:8])[0]
    success = resp_id == txn_id and ancount > 0
    print(json.dumps({
        "success": success,
        "response_id": resp_id,
        "answer_count": ancount,
        "response_bytes": len(data),
        "latency_ms": round(latency_ms, 1),
    }))
except socket.timeout:
    print(json.dumps({"success": False, "error": "UDP timed out", "error_type": "timeout"}))
except PermissionError as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": "permission_denied"}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "udp_outbound", "udp_outbound", script
        )

    # ── Phase 7: HTTPS GET ───────────────────────────────────────────

    async def _https_get(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import urllib.request, json, time, ssl
try:
    t0 = time.time()
    ctx = ssl.create_default_context()
    req = urllib.request.Request("https://httpbin.org/get", headers={"User-Agent": "sandbox-bench/1.0"})
    resp = urllib.request.urlopen(req, timeout=15, context=ctx)
    body = resp.read().decode()
    latency_ms = (time.time() - t0) * 1000
    data = json.loads(body)
    success = "url" in data and resp.status == 200
    print(json.dumps({
        "success": success,
        "status": resp.status,
        "url": data.get("url", ""),
        "latency_ms": round(latency_ms, 1),
        "tls_version": ctx.protocol.name if hasattr(ctx, "protocol") else "unknown",
    }))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "https_get", "https_outbound", script
        )

    # ── Phase 8: HTTP POST ───────────────────────────────────────────

    async def _http_post(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import urllib.request, json, time
try:
    payload = json.dumps({"test": "sandbox-bench", "value": 42}).encode()
    req = urllib.request.Request(
        "https://httpbin.org/post",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "sandbox-bench/1.0"},
        method="POST",
    )
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=15)
    body = resp.read().decode()
    latency_ms = (time.time() - t0) * 1000
    data = json.loads(body)
    echo_json = json.loads(data.get("data", "{}"))
    success = echo_json.get("value") == 42 and resp.status == 200
    print(json.dumps({
        "success": success,
        "status": resp.status,
        "echoed_value": echo_json.get("value"),
        "latency_ms": round(latency_ms, 1),
    }))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "http_post", "http_post", script
        )

    # ── Phase 9: WebSocket connect ───────────────────────────────────

    async def _websocket_connect(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import socket, ssl, json, hashlib, base64, os, time
try:
    t0 = time.time()
    host = "echo.websocket.events"
    port = 443

    # TCP + TLS connect
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.settimeout(10)
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(raw, server_hostname=host)
    sock.connect((host, port))

    # WebSocket upgrade
    key = base64.b64encode(os.urandom(16)).decode()
    upgrade = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(upgrade.encode())

    # Read HTTP response
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    status_line = response.split(b"\r\n")[0].decode()
    success = "101" in status_line

    latency_ms = (time.time() - t0) * 1000
    sock.close()
    print(json.dumps({
        "success": success,
        "status_line": status_line,
        "latency_ms": round(latency_ms, 1),
    }))
except socket.timeout:
    print(json.dumps({"success": False, "error": "connection timed out", "error_type": "timeout"}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "websocket_connect", "websocket", script
        )

    # ── Phase 10: IPv6 connectivity ──────────────────────────────────

    async def _ipv6_connectivity(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import socket, json, time
try:
    # First resolve AAAA record
    results = socket.getaddrinfo("example.com", 80, socket.AF_INET6, socket.SOCK_STREAM)
    if not results:
        print(json.dumps({"success": False, "error": "no AAAA record", "error_type": "no_aaaa"}))
    else:
        addr_info = results[0]
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.settimeout(10)
        t0 = time.time()
        sock.connect(addr_info[4])
        latency_ms = (time.time() - t0) * 1000
        peer = sock.getpeername()
        sock.close()
        print(json.dumps({
            "success": True,
            "peer_address": peer[0],
            "latency_ms": round(latency_ms, 1),
        }))
except socket.timeout:
    print(json.dumps({"success": False, "error": "IPv6 connection timed out", "error_type": "timeout"}))
except OSError as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": "os_error"}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "ipv6_connectivity", "ipv6", script
        )

    # ── Phase 11: Inbound listen ─────────────────────────────────────

    async def _inbound_listen(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import socket, json, threading, time
try:
    PORT = 8765
    received = []

    def server():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", PORT))
        srv.listen(1)
        srv.settimeout(5)
        conn, addr = srv.accept()
        data = conn.recv(1024)
        conn.sendall(data)  # echo
        received.append(data)
        conn.close()
        srv.close()

    t = threading.Thread(target=server, daemon=True)
    t.start()
    time.sleep(0.2)  # let server start

    # Client connects to localhost
    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    cli.settimeout(5)
    cli.connect(("127.0.0.1", PORT))
    cli.sendall(b"hello-sandbox-bench")
    echo = cli.recv(1024)
    cli.close()
    t.join(timeout=3)

    success = echo == b"hello-sandbox-bench"
    print(json.dumps({"success": success, "echoed": echo.decode(errors="replace")}))
except PermissionError as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": "permission_denied"}))
except OSError as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": "os_error"}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "inbound_listen", "inbound_listen", script
        )

    # ── Phase 12: SSH outbound ───────────────────────────────────────

    async def _ssh_outbound(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import socket, json, time
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    t0 = time.time()
    sock.connect(("github.com", 22))
    banner = sock.recv(256)
    latency_ms = (time.time() - t0) * 1000
    sock.close()
    banner_str = banner.decode(errors="replace").strip()
    success = banner_str.startswith("SSH-")
    print(json.dumps({
        "success": success,
        "banner": banner_str[:100],
        "latency_ms": round(latency_ms, 1),
    }))
except socket.timeout:
    print(json.dumps({"success": False, "error": "connection timed out", "error_type": "timeout"}))
except ConnectionRefusedError:
    print(json.dumps({"success": False, "error": "connection refused", "error_type": "refused"}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "ssh_outbound", "ssh_outbound", script
        )

    # ── Phase 13: Bandwidth estimate ─────────────────────────────────

    async def _bandwidth_estimate(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import urllib.request, json, time
try:
    url = "https://speed.hetzner.de/100KB.bin"
    t0 = time.time()
    resp = urllib.request.urlopen(url, timeout=30)
    data = resp.read()
    elapsed = time.time() - t0
    size_bytes = len(data)
    mbps = (size_bytes * 8) / (elapsed * 1_000_000) if elapsed > 0 else 0
    print(json.dumps({
        "success": size_bytes > 50000,
        "size_bytes": size_bytes,
        "elapsed_seconds": round(elapsed, 3),
        "mbps": round(mbps, 2),
    }))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "bandwidth_estimate", "bandwidth", script,
            timeout=45,
        )

    # ── Phase 14: Concurrent connections ─────────────────────────────

    async def _concurrent_connections(
        self, provider: SandboxProvider, sandbox_id: str
    ) -> PhaseResult:
        script = r'''
import socket, json, threading, time
TARGET = ("httpbin.org", 443)
NUM = 20
results = [None] * NUM

def connect(idx):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(TARGET)
        results[idx] = True
        sock.close()
    except Exception as e:
        results[idx] = str(e)

try:
    t0 = time.time()
    threads = []
    for i in range(NUM):
        t = threading.Thread(target=connect, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=15)
    elapsed = time.time() - t0

    successes = sum(1 for r in results if r is True)
    failures = [str(r) for r in results if r is not True and r is not None]
    print(json.dumps({
        "success": successes >= 15,
        "total": NUM,
        "successes": successes,
        "failures": len(failures),
        "failure_samples": failures[:3],
        "elapsed_seconds": round(elapsed, 3),
    }))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e), "error_type": type(e).__name__}))
'''
        return await self._run_python_test(
            provider, sandbox_id, "concurrent_connections", "concurrent_connections", script,
            timeout=45,
        )


register_suite(NetworkingSuite)
