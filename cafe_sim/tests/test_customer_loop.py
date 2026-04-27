"""End-to-end tests of run_customer with a scripted fake OpenAI client."""

import pytest

from agents import customer as customer_module
from agents.customer import run_customer

from tests.conftest import fc, scripted_responses


async def test_customer_happy_path(world, monkeypatch):
    fake_create = scripted_responses(
        [fc("enter_cafe", call_id="c1")],
        [fc("read_menu", call_id="c2")],
        [fc("place_order", {"items": ["latte"]}, call_id="c3")],
        [fc("find_seat", call_id="c4")],
        [fc("check_order", {"order_id": ""}, call_id="c5")],
        [fc("leave", {"reason": "satisfied"}, call_id="c6")],
    )
    monkeypatch.setattr(customer_module.client.responses, "create", fake_create)

    persona = {"name": "Test", "mood": "calm", "budget": 10.0, "blurb": "A test."}
    await run_customer(persona, world, "cust_test")

    # Order placed, table claimed and released.
    pending = world.get_pending_unclaimed_orders()
    assert len(pending) == 1
    assert pending[0]["customer_id"] == "cust_test"
    # No table is held by the test customer after leaving.
    for table in world._state["tables"].values():
        assert table["customer_id"] != "cust_test"

    actions = [e["action"] for e in world._state["event_log"]]
    assert "place_order" in actions
    assert "claim_table" in actions
    assert "release_table" in actions
    assert "leave" in actions

    # Every scripted response was consumed.
    assert fake_create.remaining() == 0


async def test_customer_invalid_item_does_not_corrupt_world(world, monkeypatch):
    fake_create = scripted_responses(
        [fc("enter_cafe", call_id="c1")],
        [fc("place_order", {"items": ["nonexistent"]}, call_id="c2")],
        [fc("leave", {"reason": "nothing_appealing"}, call_id="c3")],
    )
    monkeypatch.setattr(customer_module.client.responses, "create", fake_create)

    persona = {"name": "Test", "mood": "picky", "budget": 10.0, "blurb": "A test."}
    await run_customer(persona, world, "cust_test")

    # No order ever made it onto the queue.
    assert world.queue_length() == 0
    assert world.get_pending_unclaimed_orders() == []
    assert fake_create.remaining() == 0


async def test_customer_no_function_call_prompts_leave(world, monkeypatch):
    """If the model returns text without function calls, the loop nudges it
    toward `leave` and the next response complies (agents/customer.py:249-254)."""
    fake_create = scripted_responses(
        [],  # empty output, no function calls
        [fc("leave", {"reason": "satisfied"}, call_id="c1")],
    )
    monkeypatch.setattr(customer_module.client.responses, "create", fake_create)

    persona = {"name": "Test", "mood": "quiet", "budget": 10.0, "blurb": "A test."}
    await run_customer(persona, world, "cust_test")

    actions = [e["action"] for e in world._state["event_log"]]
    assert "leave" in actions
    assert fake_create.remaining() == 0


async def test_customer_hop_limit_exceeded_releases_table(world, monkeypatch):
    """Stuck-in-a-loop customers hit MAX_CUSTOMER_HOPS, drop their table, and
    are logged with reason=hop_limit_exceeded."""
    monkeypatch.setattr(customer_module, "MAX_CUSTOMER_HOPS", 3)

    fake_create = scripted_responses(
        [fc("find_seat", call_id="c1")],   # claim a table
        [fc("read_menu", call_id="c2")],
        [fc("read_menu", call_id="c3")],   # 3rd hop: loop exits without done
    )
    monkeypatch.setattr(customer_module.client.responses, "create", fake_create)

    persona = {"name": "Test", "mood": "stuck", "budget": 10.0, "blurb": "A test."}
    await run_customer(persona, world, "cust_test")

    # Table released by the cleanup branch.
    for table in world._state["tables"].values():
        assert table["customer_id"] != "cust_test"

    log = world._state["event_log"]
    assert any(
        e["action"] == "leave" and e["detail"] == "hop_limit_exceeded" for e in log
    )
    assert fake_create.remaining() == 0
