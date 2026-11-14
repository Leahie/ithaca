"""Prompt rendering against the real twin fixture."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from packages.schema.twin import PropertyTwin
from services.api.agent import prompts

FIXTURE = Path(__file__).resolve().parents[4] / "fixtures" / "twin" / "onXQLXhfyQv.json"


def _twin() -> PropertyTwin:
    return PropertyTwin.model_validate_json(FIXTURE.read_text(encoding="utf-8"))


def test_room_directory_is_unambiguous():
    twin = _twin()
    names = prompts.room_display_names(twin)
    assert len(set(names.values())) == len(twin.rooms)  # duplicate labels told apart
    ctx = prompts.render_property_context(twin)
    assert all(r.room_id in ctx for r in twin.rooms)


def test_local_time_uses_property_offset():
    # I-G13: 14:00 UTC on Dec 21 is 08:00 in Houston (UTC-6)
    ctx = prompts.render_property_context(_twin(), now=datetime(2026, 12, 21, 14, tzinfo=timezone.utc))
    assert "2026-12-21T08:00-06:00" in ctx


def test_persona_is_property_agnostic():
    # I-G19
    assert "onXQLXhfyQv" not in prompts.MAIN_PERSONA
