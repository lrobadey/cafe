"""Helpers for extracting OpenAI reasoning summaries."""


def _field(item, name, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def extract_reasoning_summary_text(response) -> str:
    """Return all reasoning summary text emitted by a Responses API response."""
    pieces = []
    for item in _field(response, "output", []) or []:
        if _field(item, "type") != "reasoning":
            continue
        for summary_item in _field(item, "summary", []) or []:
            text = _field(summary_item, "text")
            if text:
                pieces.append(str(text).strip())
    return "\n\n".join(piece for piece in pieces if piece)
