"""CLI for sandbox-bench."""

import argparse
import asyncio
import json
import os
import sys
from typing import Dict

from .benchmark import BenchmarkConfig, run_benchmark
from .provider import list_providers


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
    }


def print_results_table(results):
    """Print results in a nice table."""
    # Header
    print()
    print("┌" + "─" * 78 + "┐")
    print("│" + " sandbox-bench results".center(78) + "│")
    print("├" + "─" * 14 + "┬" + "─" * 8 + "┬" + "─" * 7 + "┬" + "─" * 10 + "┬" + "─" * 8 + "┬" + "─" * 8 + "┬" + "─" * 7 + "┬" + "─" * 7 + "┤")
    print("│" + " Provider".ljust(14) + "│" + " Time".ljust(8) + "│" + " Calls".ljust(7) + "│" + " Friction".ljust(10) + "│" + " Errors".ljust(8) + "│" + " Cost".ljust(8) + "│" + " Score".ljust(7) + "│" + " Grade".ljust(7) + "│")
    print("├" + "─" * 14 + "┼" + "─" * 8 + "┼" + "─" * 7 + "┼" + "─" * 10 + "┼" + "─" * 8 + "┼" + "─" * 8 + "┼" + "─" * 7 + "┼" + "─" * 7 + "┤")
    
    for r in results:
        # Format time
        if r.total_time_seconds >= 60:
            time_str = f"{int(r.total_time_seconds // 60)}m {int(r.total_time_seconds % 60)}s"
        else:
            time_str = f"{r.total_time_seconds:.0f}s"
        
        print(
            "│" +
            f" {r.provider}".ljust(14) + "│" +
            f" {time_str}".ljust(8) + "│" +
            f" {r.tool_calls}".ljust(7) + "│" +
            f" {r.friction_points}".ljust(10) + "│" +
            f" {r.errors}".ljust(8) + "│" +
            f" ${r.estimated_cost_usd:.2f}".ljust(8) + "│" +
            f" {r.score:.0f}".ljust(7) + "│" +
            f" {r.grade}".ljust(7) + "│"
        )
    
    print("└" + "─" * 14 + "┴" + "─" * 8 + "┴" + "─" * 7 + "┴" + "─" * 10 + "┴" + "─" * 8 + "┴" + "─" * 8 + "┴" + "─" * 7 + "┴" + "─" * 7 + "┘")
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
    list_parser = subparsers.add_parser("list", help="List available providers")
    
    args = parser.parse_args()
    
    if args.command == "list":
        print("Available providers:")
        for name in list_providers():
            print(f"  - {name}")
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
        
        # Create config
        config = BenchmarkConfig(
            providers=providers,
            agent_mode=args.agent_mode,
            model=args.model,
            benchmark_runs=args.runs,
        )
        
        print(f"Benchmarking: {', '.join(providers)}")
        print(f"Runs per provider: {config.benchmark_runs}")
        print()
        
        # Run benchmarks
        results = asyncio.run(run_benchmark(config, api_keys))
        
        # Print results
        print_results_table(results)
        
        # Output JSON if requested
        if args.output:
            output = {
                "config": {
                    "providers": providers,
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
                        "discoverability_score": r.discoverability_score,
                        "score": r.score,
                        "grade": r.grade,
                        "trace": r.trace,
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
