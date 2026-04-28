"""Entry points for terminal simulation and dashboard mode."""

import argparse
import asyncio

import uvicorn

from runner import run_simulation


def parse_args():
    parser = argparse.ArgumentParser(description="Run cafe simulation.")
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Run web dashboard server instead of terminal-only simulation.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Dashboard host (used with --dashboard).",
    )
    parser.add_argument(
        "--port",
        default=8000,
        type=int,
        help="Dashboard port (used with --dashboard).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.dashboard:
        uvicorn.run("api:app", host=args.host, port=args.port, reload=False)
    else:
        asyncio.run(run_simulation())
