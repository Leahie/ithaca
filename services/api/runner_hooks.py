"""Small glue between background work and the agent."""

from __future__ import annotations

from services.api.agent.runner import event_message


def object_ready(bridge, user_id: str, summary: str) -> None:
    """Tell the guide, in every open session of this user, that uploaded furniture is ready."""
    for room in list(bridge.hub.rooms.values()):
        if room.user_id == user_id and room.connections:
            bridge._start(room, event_message("object_ready", summary), proactive=False)
