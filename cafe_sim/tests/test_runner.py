"""Smoke test of run_simulation with both agents driven by fakes."""

import asyncio

import pytest

import runner as runner_module
from agents import barista as barista_module
from agents import customer as customer_module

from tests.conftest import FakeResponse, fc


async def _always_leave(**kwargs):
    return FakeResponse(output=[fc("leave", {"reason": "satisfied"})])


async def _always_idle(**kwargs):
    return FakeResponse(output=[fc("idle")])


async def test_run_simulation_smoke(monkeypatch, capsys):
    # Squeeze the runner's clock so the test finishes in well under a second.
    monkeypatch.setattr(runner_module, "SIM_DURATION", 0.15)
    monkeypatch.setattr(runner_module, "CUSTOMER_SPAWN_INTERVAL", 0.02)
    monkeypatch.setattr(runner_module, "MAX_CONCURRENT_CUSTOMERS", 2)

    # Drive every spawned customer to immediate `leave`, every barista cycle
    # to `idle` (the runner cancels the barista task explicitly at the end).
    monkeypatch.setattr(customer_module.client.responses, "create", _always_leave)
    monkeypatch.setattr(barista_module.client.responses, "create", _always_idle)

    # Make the barista's idle (and any future prepare) instantaneous.
    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(barista_module.asyncio, "sleep", fake_sleep)

    await runner_module.run_simulation()

    out = capsys.readouterr().out
    assert "Simulation started" in out
    assert "Simulation complete" in out
    # At least one customer was spawned and ran to completion.
    assert "Spawning customer" in out


async def test_runner_awaits_cancelled_barista(monkeypatch):
    """The runner cancels and then awaits the barista task — proves no
    'Task was destroyed but it is pending' on shutdown."""
    monkeypatch.setattr(runner_module, "SIM_DURATION", 0.05)
    monkeypatch.setattr(runner_module, "CUSTOMER_SPAWN_INTERVAL", 1.0)  # never spawn
    monkeypatch.setattr(runner_module, "MAX_CONCURRENT_CUSTOMERS", 0)

    captured: dict = {}
    original_create_task = asyncio.create_task

    def tracking_create_task(coro, *args, **kwargs):
        task = original_create_task(coro, *args, **kwargs)
        captured.setdefault("first_task", task)
        return task

    monkeypatch.setattr(runner_module.asyncio, "create_task", tracking_create_task)

    monkeypatch.setattr(barista_module.client.responses, "create", _always_idle)

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(barista_module.asyncio, "sleep", fake_sleep)

    await runner_module.run_simulation()

    barista_task = captured["first_task"]
    assert barista_task.done()
    assert barista_task.cancelled() or isinstance(
        barista_task.exception(), asyncio.CancelledError
    )
