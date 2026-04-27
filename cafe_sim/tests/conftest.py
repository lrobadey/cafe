"""Shared test fixtures and helpers for the cafe simulation."""

import os
import sys
from pathlib import Path

# Both must be set BEFORE any cafe_sim modules are imported, because
# agents/customer.py and agents/barista.py construct an OpenAI client at
# module import time via build_openai_client(), which raises if the key
# is missing.
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")

# The production modules use bare imports ("from config import ...",
# "from agents.barista import ..."), so cafe_sim/ has to be on sys.path.
_CAFE_SIM_ROOT = Path(__file__).resolve().parent.parent
if str(_CAFE_SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(_CAFE_SIM_ROOT))

import pytest


class FakeFunctionCall:
    """Mimics a Responses API function_call output item."""

    type = "function_call"

    def __init__(self, name: str, arguments: str = "{}", call_id: str | None = None):
        self.name = name
        self.arguments = arguments
        self.call_id = call_id or f"call_{name}"


class FakeMessage:
    """Mimics a Responses API non-function-call output item (text turn)."""

    type = "message"

    def __init__(self, text: str = ""):
        self.text = text


class FakeResponse:
    """Mimics a Responses API result with .output list."""

    def __init__(self, output: list):
        self.output = output


def fc(name: str, arguments: dict | str | None = None, call_id: str | None = None) -> FakeFunctionCall:
    """Build a FakeFunctionCall. arguments may be a dict (auto-json'd) or a raw string."""
    import json

    if arguments is None:
        args_str = "{}"
    elif isinstance(arguments, dict):
        args_str = json.dumps(arguments)
    else:
        args_str = arguments
    return FakeFunctionCall(name=name, arguments=args_str, call_id=call_id)


def scripted_responses(*scripts: list, cancel_when_exhausted: bool = False):
    """Return an async function suitable for monkey-patching client.responses.create.

    Each element of *scripts is a list of fake output items. Each call to the
    returned fake pops the next script and wraps it in a FakeResponse.

    By default, calling beyond the script length raises AssertionError so
    misbehaving loops fail loudly. Pass `cancel_when_exhausted=True` to raise
    asyncio.CancelledError instead — useful for terminating the barista's
    infinite outer loop in tests.
    """
    import asyncio as _asyncio

    queue = list(scripts)

    async def _fake_create(**kwargs):
        if not queue:
            if cancel_when_exhausted:
                raise _asyncio.CancelledError()
            raise AssertionError("scripted_responses exhausted: extra responses.create call")
        next_output = queue.pop(0)
        return FakeResponse(output=list(next_output))

    _fake_create.remaining = lambda: len(queue)
    return _fake_create


@pytest.fixture
def world():
    """Fresh WorldState per test."""
    from world import WorldState

    return WorldState()
