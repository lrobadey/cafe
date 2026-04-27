"""Simple colored terminal logger for simulation events."""

import time

COLORS = {
    "RUNNER": "\033[90m",
    "barista": "\033[36m",
    "cust": "\033[33m",
}
RESET = "\033[0m"


def log_event(agent_id: str, message: str):
    color = COLORS.get(agent_id, "")
    for prefix, prefix_color in COLORS.items():
        if agent_id.startswith(prefix):
            color = prefix_color
            break
    timestamp = time.strftime("%H:%M:%S")
    print(f"{color}[{timestamp}] [{agent_id}] {message}{RESET}")
