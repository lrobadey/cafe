"""Sanity checks for the PERSONAS list (spec §5)."""

from personas import PERSONAS


def test_persona_count_is_twelve():
    assert len(PERSONAS) == 12


def test_persona_required_fields():
    required = {"name", "mood", "budget", "blurb"}
    for persona in PERSONAS:
        assert required.issubset(persona.keys()), persona
        assert isinstance(persona["name"], str) and persona["name"]
        assert isinstance(persona["mood"], str) and persona["mood"]
        assert isinstance(persona["budget"], (int, float))
        assert persona["budget"] > 0
        assert isinstance(persona["blurb"], str) and persona["blurb"]


def test_persona_names_are_unique():
    names = [p["name"] for p in PERSONAS]
    assert len(names) == len(set(names))
