"""Entry point for the cafe simulation."""

import asyncio

from runner import run_simulation


if __name__ == "__main__":
    asyncio.run(run_simulation())
