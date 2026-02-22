"""CLI for sandbox-bench."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Dict

from .benchmark import BenchmarkConfig, run_benchmark
from .provider import list_providers
from .suites import list_suites
from .capabilities import CAPABILITY_DESCRIPTIONS


SUITE_CHOICES = ["basic", "competitive", "swe", "environment", "performance", "full"]


def load_api_keys(env_file: str | None = None) -> Dict[str, str]:
    """Load API keys from environment or file."""
    if env_file:
        from dotenv import load_dotenv
        load_dotenv(env_file)

    return {
        "e2b": os.environ.get("E2B_API_KEY", ""),
        "daytona": os.environ.get("DAYTONA_API_KEY", ""),
        "modal": os.environ.get("MODAL_TOKEN_ID", ""),  # Modal uses token ID
        "codesandbox": os.environ.get("CODESANDBOX_API_KEY", ""),
        "fly": os.environ.get("FLY_API_TOKEN", ""),
        # Generic providers - pass image name or VM command
        "docker-image": os.environ.get("DOCKER_IMAGE", ""),
        "microvm": os.environ.get("MICROVM_COMMAND", ""),
    }


def print_results_table(results):
    """Print results in a nice table."""
    # Header
    print()
    print("\u250c" + "\u2500" * 78 + "\u2510")
    print("\u2502" + " sandbox-bench results".center(78) + "\u2502")
    print("\u251c" + "\u2500" * 14 + "\u252c" + "\u2500" * 8 + "\u252c" + "\u2500" * 7 + "\u252c" + "\u2500" * 10 + "\u252c" + "\u2500" * 8 + "\u252c" + "\u2500" * 8 + "\u252c" + "\u2500" * 7 + "\u252c" + "\u2500" * 7 + "\u2524")
    print("\u2502" + " Provider".ljust(14) + "\u2502" + " Time".ljust(8) + "\u2502" + " Calls".ljust(7) + "\u2502" + " Friction".ljust(10) + "\u2502" + " Errors".ljust(8) + "\u2502" + " Cost".ljust(8) + "\u2502" + " Score".ljust(7) + "\u2502" + " Grade".ljust(7) + "\u2502")
    print("\u251c" + "\u2500" * 14 + "\u253c" + "\u2500" * 8 + "\u253c" + "\u2500" * 7 + "\u253c" + "\u2500" * 10 + "\u253c" + "\u2500" * 8 + "\u253c" + "\u2500" * 8 + "\u253c" + "\u2500" * 7 + "\u253c" + "\u2500" * 7 + "\u2524")

    for r in results:
        # Format time
        if r.total_time_seconds >= 60:
            time_str = f"{int(r.total_time_seconds // 60)}m {int(r.total_time_seconds % 60)}s"
        else:
            time_str = f"{r.total_time_seconds:.0f}s"

        print(
            "\u2502" +
            f" {r.provider}".ljust(14) + "\u2502" +
            f" {time_str}".ljust(8) + "\u2502" +
            f" {r.tool_calls}".ljust(7) + "\u2502" +
            f" {r.friction_points}".ljust(10) + "\u2502" +
            f" {r.errors}".ljust(8) + "\u2502" +
            f" ${r.estimated_cost_usd:.4f}".ljust(8) + "\u2502" +
            f" {r.score:.0f}".ljust(7) + "\u2502" +
            f" {r.grade}".ljust(7) + "\u2502"
        )

    print("\u2514" + "\u2500" * 14 + "\u2534" + "\u2500" * 8 + "\u2534" + "\u2500" * 7 + "\u2534" + "\u2500" * 10 + "\u2534" + "\u2500" * 8 + "\u2534" + "\u2500" * 8 + "\u2534" + "\u2500" * 7 + "\u2534" + "\u2500" * 7 + "\u2518")
    print()


def print_capabilities_matrix(results):
    """Print capabilities matrix if any results have capabilities."""
    has_caps = any(r.capabilities for r in results)
    if not has_caps:
        return

    # Gather all capability names across results
    all_caps = set()
    for r in results:
        all_caps.update(r.capabilities.keys())

    if not all_caps:
        return

    all_caps = sorted(all_caps)

    print("Capabilities Matrix:")
    print()

    # Header
    cap_width = max(len(c) for c in all_caps) + 2
    header = "  " + "Provider".ljust(14)
    for cap in all_caps:
        header += cap.ljust(cap_width)
    print(header)
    print("  " + "-" * (14 + cap_width * len(all_caps)))

    for r in results:
        row = "  " + r.provider.ljust(14)
        for cap in all_caps:
            if cap in r.capabilities:
                sym = "Y" if r.capabilities[cap] else "N"
            else:
                sym = "-"
            row += sym.ljust(cap_width)
        print(row)

    print()


def main():
    """Main CLI entrypoint."""
    # Import providers to register them
    from . import providers  # noqa

    parser = argparse.ArgumentParser(
        prog="sandbox-bench",
        description="Benchmark AI agent sandbox providers",
    )

    subparsers = parser.add_subparsers(dest="command")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run benchmarks")
    run_parser.add_argument(
        "--all",
        action="store_true",
        help="Run against all providers with API keys",
    )
    run_parser.add_argument(
        "--provider", "-p",
        action="append",
        help="Provider to benchmark (can repeat)",
    )
    run_parser.add_argument(
        "--suite", "-s",
        action="append",
        choices=SUITE_CHOICES,
        help="Test suite to run (can repeat). Choices: basic, competitive, swe, environment, performance, full. Default: basic",
    )
    run_parser.add_argument(
        "--model", "-m",
        default="claude-opus-4",
        help="Model to use for agent mode",
    )
    run_parser.add_argument(
        "--agent-mode",
        action="store_true",
        help="Use AI agent to discover and use SDK",
    )
    run_parser.add_argument(
        "--env-file",
        help="Path to .env file with API keys",
    )
    run_parser.add_argument(
        "--output", "-o",
        help="Output JSON file for results",
    )
    run_parser.add_argument(
        "--runs", "-n",
        type=int,
        default=3,
        help="Number of benchmark runs (default: 3)",
    )

    # List command
    subparsers.add_parser("list", help="List available providers")

    # Suites command
    subparsers.add_parser("suites", help="List available test suites")

    args = parser.parse_args()

    if args.command == "list":
        print("Available providers:")
        for name in list_providers():
            print(f"  - {name}")
        return

    if args.command == "suites":
        print("Available test suites:")
        for name in list_suites():
            print(f"  - {name}")
        print("  - full (all suites)")
        return

    if args.command == "run":
        # Load API keys
        api_keys = load_api_keys(args.env_file)

        # Determine providers to test
        if args.all:
            providers = [p for p in list_providers() if api_keys.get(p)]
        elif args.provider:
            providers = args.provider
        else:
            print("Error: specify --all or --provider", file=sys.stderr)
            sys.exit(1)

        if not providers:
            print("Error: no providers to test (check API keys)", file=sys.stderr)
            sys.exit(1)

        # Determine suites
        suites = args.suite if args.suite else ["basic"]

        # Create config
        config = BenchmarkConfig(
            providers=providers,
            suites=suites,
            agent_mode=args.agent_mode,
            model=args.model,
            benchmark_runs=args.runs,
        )

        print(f"Benchmarking: {', '.join(providers)}")
        print(f"Suites: {', '.join(suites)}")
        print(f"Runs per provider: {config.benchmark_runs}")
        print()

        # Run benchmarks
        results = asyncio.run(run_benchmark(config, api_keys))

        # Print results
        print_results_table(results)
        print_capabilities_matrix(results)

        # Output JSON if requested
        if args.output:
            output = {
                "config": {
                    "providers": providers,
                    "suites": suites,
                    "agent_mode": args.agent_mode,
                    "model": args.model,
                    "runs": args.runs,
                },
                "results": [
                    {
                        "provider": r.provider,
                        "success": r.success,
                        "total_time_seconds": r.total_time_seconds,
                        "tool_calls": r.tool_calls,
                        "friction_points": r.friction_points,
                        "errors": r.errors,
                        "error_messages": r.error_messages,
                        "estimated_cost_usd": r.estimated_cost_usd,
                        "sandbox_cost_usd": r.sandbox_cost_usd,
                        "discoverability_score": r.discoverability_score,
                        "score": r.score,
                        "grade": r.grade,
                        "trace": r.trace,
                        "suites_run": r.suites_run,
                        "suite_results": r.suite_results,
                        "capabilities": r.capabilities,
                        "capability_score": r.capability_score,
                        "cold_start_seconds": r.cold_start_seconds,
                        "warm_start_seconds": r.warm_start_seconds,
                        "agent_spawn_seconds": r.agent_spawn_seconds,
                        "file_io_throughput_mbps": r.file_io_throughput_mbps,
                    }
                    for r in results
                ],
            }

            with open(args.output, "w") as f:
                json.dump(output, f, indent=2)

            print(f"Results written to {args.output}")

        return

    parser.print_help()


if __name__ == "__main__":
    main()
