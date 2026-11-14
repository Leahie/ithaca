"""Agent tools over Stream C's engines, on the real fixtures."""

from __future__ import annotations

from types import SimpleNamespace

from services.api.agent import tools

EAST_BEDROOM, WEST_BEDROOM = "peina19mpqedsku6a3huu38md", "q5n62dpd3mrc1exy2hr84132b"


def runtime(events: list):
    return SimpleNamespace(context=SimpleNamespace(property_id="onXQLXhfyQv"), stream_writer=events.append)


def test_area_profile_carries_the_price_caveat():
    text = tools.area_profile.func(runtime=runtime([]))
    assert "$/sq ft" in text and "last ask" in text
    assert "Caveats" in text and "asking price" in text  # the agent must not say "sold for"


def test_sound_tools_report_levels_and_place_a_source_by_room():
    events: list = []
    heard = tools.sound_heard_from.func(source_room_id=EAST_BEDROOM, sound="baby", runtime=runtime(events), listener_room_id=WEST_BEDROOM)
    assert "dBA" in heard and events == []  # a question only: nothing moves on screen
    tools.sound_place_source.func(room_id=EAST_BEDROOM, sound="tv", runtime=runtime(events))
    assert [e["type"] for e in events] == ["viewer.setMode", "sound.placeSource"]
    assert events[1]["payload"]["room_id"] == EAST_BEDROOM and events[1]["payload"]["preset_id"] == "tv"
