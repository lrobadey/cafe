"""Static verification of OpenAI tool schemas (spec §14)."""

import pytest

from agents.barista import BARISTA_TOOLS
from agents.customer import CUSTOMER_TOOLS

EXPECTED_CUSTOMER_NAMES = {
    "enter_cafe",
    "read_menu",
    "place_order",
    "find_seat",
    "check_order",
    "leave",
}

EXPECTED_BARISTA_NAMES = {
    "check_queue",
    "claim_order",
    "prepare_order",
    "mark_ready",
    "idle",
}

EXPECTED_LEAVE_REASONS = [
    "satisfied",
    "impatient",
    "no_seats",
    "nothing_appealing",
    "too_expensive",
]


def _validate_tool(tool: dict):
    assert tool["type"] == "function", tool
    assert tool["strict"] is True, tool
    assert isinstance(tool["name"], str) and tool["name"], tool
    assert isinstance(tool["description"], str) and tool["description"], tool

    params = tool["parameters"]
    assert params["type"] == "object", tool
    assert params["additionalProperties"] is False, tool
    assert set(params["required"]) == set(params["properties"].keys()), tool


@pytest.mark.parametrize("tool", CUSTOMER_TOOLS, ids=lambda t: t["name"])
def test_customer_tool_shape(tool):
    _validate_tool(tool)


@pytest.mark.parametrize("tool", BARISTA_TOOLS, ids=lambda t: t["name"])
def test_barista_tool_shape(tool):
    _validate_tool(tool)


def test_customer_tool_names():
    assert {t["name"] for t in CUSTOMER_TOOLS} == EXPECTED_CUSTOMER_NAMES


def test_barista_tool_names():
    assert {t["name"] for t in BARISTA_TOOLS} == EXPECTED_BARISTA_NAMES


def test_leave_reason_enum_matches_spec():
    leave = next(t for t in CUSTOMER_TOOLS if t["name"] == "leave")
    assert leave["parameters"]["properties"]["reason"]["enum"] == EXPECTED_LEAVE_REASONS
