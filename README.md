# sandbox-bench

Open-source benchmark suite for AI agent sandbox providers.

**Measure what matters:** Time, cost, errors, friction, and capabilities when an AI agent uses your sandbox.

**[View live results dashboard](https://zkwentz.github.io/sandbox-bench/)**

## Why?

AI agents are the new power users. They don't read your UI — they parse your docs, call your API, and move on. Whether you're building RL training loops, agentic coding assistants, or autonomous sub-agent pipelines, the sandbox is the bottleneck. This benchmark measures how well sandbox providers serve AI agents across real-world workloads.

## Providers

| Provider | Type | Status |
|----------|------|--------|
| [E2B](https://e2b.dev) | Firecracker microVM | Supported |
| [Daytona](https://daytona.io) | Docker | Supported |
| [Modal](https://modal.com) | Container | Supported |
| [CodeSandbox](https://codesandbox.io) | Docker | Supported |
| [Fly.io Machines](https://fly.io) | Firecracker | Supported |
| Docker Image | Local container | Supported |
| MicroVM | Local microVM | Supported |

## Test Suites

sandbox-bench runs modular test suites inside a single sandbox lifecycle. Pick what you need or run them all.

| Suite | What it tests | Example phases |
|-------|--------------|----------------|
| **basic** | Hello-world execution, file I/O | `execute_hello`, `file_io` |
| **competitive** | Baekjoon/CP-style: stdin piping, compilation, timeouts | `stdin_piping`, `gcc`, `g++`, `exec_timeout` |
| **swe** | SWE-bench-style: package install, git, pytest, network | `network_access`, `pip_install`, `git_clone`, `pytest` |
| **environment** | Complex onramp: Node.js, npm, venv, multi-step builds | `nodejs`, `npm`, `project_clone`, `multi_step_build` |
| **performance** | Agent spawn latency, warm start, file I/O throughput | `agent_spawn`, `warm_start`, `rapid_exec`, `file_io_10mb` |
| **full** | All of the above | — |

Default is `basic` for fast iteration. Use `--suite full` for comprehensive benchmarking.

## Scoring

Metrics are weighted and normalized to produce a 0–100 score.

**Base weights** (basic suite only):

| Metric | Weight | Lower is better? |
|--------|--------|-------------------|
| Time | 30% | Yes |
| Errors | 20% | Yes |
| Friction | 15% | Yes |
| Tool Calls | 15% | Yes |
| Cost | 10% | Yes |
| Discoverability | 10% | No (higher = better) |

**Full weights** (when extended suites run and capabilities data is present):

| Metric | Weight | Description |
|--------|--------|-------------|
| Time | 25% | Seconds from API key to working sandbox |
| Errors | 20% | Errors encountered during the run |
| Friction | 15% | Manual steps or workarounds needed |
| Tool Calls | 10% | Number of API/SDK calls required |
| Cost | 10% | Provider-specific sandbox cost per run |
| Discoverability | 10% | How easy to find correct API usage (1–5) |
| Capabilities | 10% | Fraction of tested capabilities supported |

Grades: **A** (85–100), **B** (70–84), **C** (55–69), **D** (40–54), **F** (0–39)

## Quick Start

```bash
# Install
pip install sandbox-bench

# Or install from source
git clone https://github.com/zkwentz/sandbox-bench.git
cd sandbox-bench
pip install -e ".[e2b,daytona,modal]"

# List available providers and suites
sandbox-bench list
sandbox-bench suites

# Run basic suite against all providers with API keys
sandbox-bench run --all

# Run against a specific provider
sandbox-bench run -p e2b

# Run specific suites
sandbox-bench run -p e2b -s competitive -s swe

# Run everything
sandbox-bench run --all --suite full

# Output JSON results
sandbox-bench run --all -o results.json

# Control number of benchmark runs (default: 3)
sandbox-bench run --all -n 5
```

## Configuration

Set API keys via environment variables:

```bash
export E2B_API_KEY="..."
export DAYTONA_API_KEY="..."
export MODAL_TOKEN_ID="..."
export MODAL_TOKEN_SECRET="..."
export CODESANDBOX_API_KEY="..."
export FLY_API_TOKEN="..."
```

Or use a `.env` file:

```bash
sandbox-bench run --all --env-file .env
```

## Adding a Provider

Implement the `SandboxProvider` interface:

```python
from sandbox_bench.provider import SandboxProvider, ProviderInfo, register_provider

class MyProvider(SandboxProvider):
    name = "my-provider"
    info = ProviderInfo(
        name="my-provider",
        description="My sandbox provider",
        docs_url="https://docs.example.com",
    )

    async def authenticate(self, api_key: str) -> None:
        """Connect to the provider."""
        ...

    async def create_sandbox(self, image=None, timeout_seconds=300) -> str:
        """Create a new sandbox, return its ID."""
        ...

    async def execute(self, sandbox_id, code, language="python", timeout_seconds=30):
        """Execute code, return (stdout, stderr, exit_code)."""
        ...

    async def execute_command(self, sandbox_id, command, timeout_seconds=30):
        """Execute a shell command, return (stdout, stderr, exit_code)."""
        ...

    async def write_file(self, sandbox_id, path, content) -> None:
        """Write a file to the sandbox."""
        ...

    async def read_file(self, sandbox_id, path):
        """Read a file from the sandbox."""
        ...

    async def destroy(self, sandbox_id) -> None:
        """Destroy the sandbox."""
        ...

register_provider(MyProvider)
```

`execute_command()` has a default implementation that delegates to `execute(code, language="sh")`, so you only need to override it if your provider has a more efficient shell execution path.

## Agent Mode

The benchmark can run in "agent mode" where an AI agent attempts to use each provider from scratch:

```bash
sandbox-bench run --all --agent-mode --model claude-opus-4
```

This measures real-world agent experience — not just API performance.

## CI Integration

```yaml
# .github/workflows/benchmark.yml
name: Sandbox Benchmark
on:
  schedule:
    - cron: '0 0 * * 0'  # Weekly
  workflow_dispatch:

jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install sandbox-bench
      - run: sandbox-bench run --all --suite full --output results.json
        env:
          E2B_API_KEY: ${{ secrets.E2B_API_KEY }}
          DAYTONA_API_KEY: ${{ secrets.DAYTONA_API_KEY }}
      - uses: actions/upload-artifact@v4
        with:
          name: benchmark-results
          path: results.json
```

## Contributing

PRs welcome! Especially for:
- New provider implementations
- New test suites or phases
- Improved scoring algorithms
- Dashboard visualizations

## License

MIT

## Acknowledgments

Inspired by [2027.dev/arena](https://2027.dev/arena) — we wanted an open-source version anyone can run and extend.
