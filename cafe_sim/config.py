"""Configuration for the OpenAI cafe simulation MVP."""

import os
from pathlib import Path

from openai import AsyncOpenAI

# Model routing
BARISTA_MODEL = "gpt-5.4-mini"
CUSTOMER_MODEL = "gpt-5.4-mini"

# OpenAI Responses API controls
REASONING_EFFORT = "high"
REASONING_SUMMARY = "auto"
STORE_RESPONSES = True

# Timing (real seconds)
CUSTOMER_SPAWN_INTERVAL = 30
CUSTOMER_SPAWN_JITTER = 0.5
BARISTA_POLL_INTERVAL = 5
CUSTOMER_MAX_WAIT = 90
SIM_DURATION = 600
CLOSING_GRACE_SECONDS = 20

# Concurrency
MAX_CONCURRENT_CUSTOMERS = 4
MAX_CUSTOMER_HOPS = 16

# Menu (name, price, prep_seconds, category)
MENU = {
    "espresso": {"name": "Espresso", "price": 3.00, "prep_seconds": 4, "category": "drink", "available": True},
    "latte": {"name": "Latte", "price": 5.50, "prep_seconds": 8, "category": "drink", "available": True},
    "cold_brew": {"name": "Cold Brew", "price": 5.00, "prep_seconds": 3, "category": "drink", "available": True},
    "tea": {"name": "Tea", "price": 3.50, "prep_seconds": 5, "category": "drink", "available": True},
    "muffin": {"name": "Blueberry Muffin", "price": 4.00, "prep_seconds": 2, "category": "food", "available": True},
}

# Physical supplies and menu recipes. Counts are intentionally modest so stockouts
# can appear during normal runs.
SUPPLIES = {
    "coffee_beans": {"name": "Coffee beans", "quantity": 10, "low_threshold": 3},
    "milk": {"name": "Milk", "quantity": 6, "low_threshold": 2},
    "cups": {"name": "Cups", "quantity": 14, "low_threshold": 4},
    "cold_brew_servings": {"name": "Cold brew servings", "quantity": 6, "low_threshold": 2},
    "tea_bags": {"name": "Tea bags", "quantity": 6, "low_threshold": 2},
    "muffins": {"name": "Muffins", "quantity": 5, "low_threshold": 2},
}

MENU_RECIPES = {
    "espresso": {"coffee_beans": 1, "cups": 1},
    "latte": {"coffee_beans": 1, "milk": 1, "cups": 1},
    "cold_brew": {"cold_brew_servings": 1, "cups": 1},
    "tea": {"tea_bags": 1, "cups": 1},
    "muffin": {"muffins": 1},
}

# Tables
TABLE_IDS = ["t1", "t2", "t3", "t4"]


def load_local_env() -> None:
    """Load simple KEY=VALUE pairs from the repo-local .env file."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def build_openai_client() -> AsyncOpenAI:
    """Build OpenAI async client and fail fast if key is missing."""
    load_local_env()
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY must be set before running the cafe simulation.")
    return AsyncOpenAI()
