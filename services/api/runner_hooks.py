"""Small glue between background work and the agent."""

from __future__ import annotations

from services.api.agent.runner import event_message


def object_ready(bridge, user_id: str, summary: str) -> None:
    """Tell the guide, in every open session of this user, that uploaded furniture is ready."""
    for room in list(bridge.hub.rooms.values()):
        if room.user_id == user_id and room.connections:
            bridge._start(room, event_message("object_ready", summary), proactive=False)


def own_room_ready(bridge, house_user_id: str, person_id: str, summary: str) -> None:
    """A room of the house has been set up from a photo of someone's own room: every screen of
    that house draws its furniture again, and that person's guide is told what came in."""
    from packages.schema.events import make_event
    from services.api.realtime.households import households

    for room in list(bridge.hub.rooms.values()):
        if room.user_id != house_user_id or not room.connections:
            continue
        bridge.hub.publish(make_event(room.session_id, "server", "layout.changed", {}))
        theirs = person_id if room.household_id and households().member(room.household_id, person_id) else None
        bridge._start(room, event_message("own_room_ready", summary), proactive=False, member_id=theirs)

