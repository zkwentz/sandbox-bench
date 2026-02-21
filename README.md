# sandbox-bench 🏎️

Open source benchmark suite for AI agent sandbox providers.

**Measure what matters:** Time, cost, errors, and friction when an AI agent onboards to your sandbox.

## Why?

AI agents are the new power users. They don't read your UI — they parse your docs, call your API, and move on. This benchmark measures how well sandbox providers serve AI agents.

## Providers Tested

| Provider | Type | Status |
|----------|------|--------|
| [E2B](https://e2b.dev) | Firecracker microVM | ✅ Supported |
| [Daytona](https://daytona.io) | Docker | ✅ Supported |
| [Modal](https://modal.com) | Container | ✅ Supported |
| [CodeSandbox](https://codesandbox.io) | Docker | ✅ Supported |
| [Fly.io Machines](https://fly.io) | Firecracker | ✅ Supported |
| [Freestyle](https://freestyle.sh) | Container | ✅ Supported |
| [Blaxel](https://blaxel.ai) | Container | ✅ Supported |
| Custom | Any | ✅ Pluggable |

## Metrics

| Metric | Description | Weight |
|--------|-------------|--------|
| **Time** | Seconds from API key to working sandbox | 30% |
| **Tool Calls** | Number of API/SDK calls required | 15% |
| **Friction** | Manual steps or workarounds needed | 15% |
| **Errors** | Errors encountered during onboarding | 20% |
| **Cost** | USD cost per benchmark run | 10% |
| **Discoverability** | How easy to find correct API usage | 10% |

## Quick Start

```bash
# Install
pip install sandbox-bench

# Run benchmark against all providers (needs API keys in env)
sandbox-bench run --all

# Run against specific provider
sandbox-bench run --provider e2b

# Run with specific model
sandbox-bench run --all --model claude-opus-4

# Output JSON results
sandbox-bench run --all --output results.json
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
export ANTHROPIC_API_KEY="..."  # For the benchmark agent
```

Or use a `.env` file:

```bash
sandbox-bench run --all --env-file .env
```

## The Benchmark Task

The benchmark measures how quickly an AI agent can:

1. **Authenticate** — Use the API key to connect
2. **Create sandbox** — Spin up a new isolated environment
3. **Execute code** — Run a simple Python script
4. **Read output** — Capture stdout/stderr
5. **File I/O** — Write and read a file
6. **Cleanup** — Destroy the sandbox

This represents a minimal "hello world" for sandbox providers — the baseline any AI coding agent needs.

## Sample Output

```
┌─────────────────────────────────────────────────────────────────┐
│                    sandbox-bench results                         │
│                    Model: claude-opus-4                          │
│                    Date: 2026-02-21                              │
├──────────────┬────────┬───────┬──────────┬────────┬──────┬──────┤
│ Provider     │ Time   │ Calls │ Friction │ Errors │ Cost │ Grade│
├──────────────┼────────┼───────┼──────────┼────────┼──────┼──────┤
│ E2B          │ 43s    │ 13    │ 1        │ 0      │ $0.47│ A    │
│ Modal        │ 52s    │ 15    │ 1        │ 0      │ $0.38│ A    │
│ Daytona      │ 2m 8s  │ 19    │ 1        │ 1      │ $0.52│ B    │
│ Fly.io       │ 2m 45s │ 22    │ 2        │ 1      │ $0.61│ B    │
│ CodeSandbox  │ 3m 25s │ 32    │ 2        │ 1      │ $2.11│ C    │
│ Blaxel       │ 3m 46s │ 34    │ 1        │ 1      │ $1.01│ C    │
└──────────────┴────────┴───────┴──────────┴────────┴──────┴──────┘
```

## Adding a Provider

Implement the `SandboxProvider` interface:

```python
from sandbox_bench import SandboxProvider, BenchmarkResult

class MyProvider(SandboxProvider):
    name = "my-provider"
    
    async def authenticate(self, api_key: str) -> None:
        """Connect to the provider."""
        ...
    
    async def create_sandbox(self) -> str:
        """Create a new sandbox, return its ID."""
        ...
    
    async def execute(self, sandbox_id: str, code: str) -> tuple[str, str]:
        """Execute code, return (stdout, stderr)."""
        ...
    
    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        """Write a file to the sandbox."""
        ...
    
    async def read_file(self, sandbox_id: str, path: str) -> str:
        """Read a file from the sandbox."""
        ...
    
    async def destroy(self, sandbox_id: str) -> None:
        """Destroy the sandbox."""
        ...

# Register it
from sandbox_bench import register_provider
register_provider(MyProvider)
```

## How Scoring Works

### Grade Calculation

```
Score = (
    (1 - time_normalized) * 0.30 +
    (1 - calls_normalized) * 0.15 +
    (1 - friction_normalized) * 0.15 +
    (1 - errors_normalized) * 0.20 +
    (1 - cost_normalized) * 0.10 +
    discoverability * 0.10
) * 100

Grade:
  A  = 85-100
  B  = 70-84
  C  = 55-69
  D  = 40-54
  F  = 0-39
```

### Discoverability Score

Rated 1-5 based on:
- **5/5**: MCP server, OpenAPI spec, or llms.txt
- **4/5**: Well-structured docs with examples
- **3/5**: Docs exist but scattered or incomplete
- **2/5**: Minimal docs, mostly code comments
- **1/5**: No docs, reverse-engineer required

## Agent Mode

The benchmark can run in "agent mode" where an actual AI agent (Claude, GPT-4, etc.) attempts to use each provider from scratch:

```bash
# Let Claude figure out each SDK from docs alone
sandbox-bench run --all --agent-mode --model claude-opus-4

# Compare how different models perform
sandbox-bench run --provider e2b --agent-mode --model gpt-4o
sandbox-bench run --provider e2b --agent-mode --model claude-opus-4
```

This measures real-world agent experience, not just API performance.

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
      - run: sandbox-bench run --all --output results.json
        env:
          E2B_API_KEY: ${{ secrets.E2B_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - uses: actions/upload-artifact@v4
        with:
          name: benchmark-results
          path: results.json
```

## Contributing

PRs welcome! Especially for:
- New provider implementations
- Improved scoring algorithms
- Better agent prompts
- Dashboard/visualization

## License

MIT

## Acknowledgments

Inspired by [2027.dev/arena](https://2027.dev/arena) — we wanted an open source version anyone can run and extend.
