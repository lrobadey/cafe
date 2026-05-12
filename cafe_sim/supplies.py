"""Shared supply read-model helpers."""


def supply_status(supply: dict) -> str:
    quantity = int(supply.get("quantity", 0) or 0)
    if quantity <= 0:
        return "out"
    if quantity <= int(supply.get("low_threshold", 0) or 0):
        return "low"
    return "normal"


def copy_supply(supply: dict, *, include_status: bool = False) -> dict:
    copied = dict(supply)
    copied.pop("status", None)
    if include_status:
        copied["status"] = supply_status(copied)
    return copied


def copy_supplies(supplies: dict, *, include_status: bool = False) -> dict:
    return {
        supply_id: copy_supply(supply, include_status=include_status)
        for supply_id, supply in supplies.items()
    }
