"""Scenario runs of the guide and the designer, with checks taken from the solver and the simulation.

    .venv/bin/python scripts/scenarios/run.py A B C D --runs 1

When the guide asks with picture cards, the script taps the first card. Each run uses its own throwaway furniture file and memory (nothing in data/agent/ is touched), the
real models and the real catalog. It writes scripts/scenarios/out/<scenario>-<run>.log (the
conversation, every tool call and result) and .json (the checks). No agent code is changed here.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.store.memory import InMemoryStore  # noqa: E402

from packages.schema.reports import NoiseScenario  # noqa: E402
from services.api.agent import design_gate, memory_files, prompts, views  # noqa: E402
from services.api.agent.context import AgentContext  # noqa: E402
from services.api.agent.design_tools import BACKGROUND_DESIGN, choice_note  # noqa: E402
from services.api.agent.graph import build_agent, build_designer, run_designer  # noqa: E402
from services.api.agent.runner import event_message, stream_turn  # noqa: E402
from services.api.furniture import furnish as furnishing  # noqa: E402
from services.api.furniture import store as furniture  # noqa: E402
from services.api.sim import layout_solver  # noqa: E402
from services.api.twin import service  # noqa: E402

PROPERTY = "onXQLXhfyQv"
OUT = Path(__file__).resolve().parent / "out"
DRESSING = {"rug", "floor_lamp", "table_lamp", "ceiling_light", "wall_art", "mirror", "plant", "decor"}
SEATS = {"sofa": 3, "sectional": 4, "loveseat": 2, "bench": 2, "armchair": 1, "chair": 1, "stool": 1, "ottoman": 1}

SCENARIOS = {
    "A": ["My parents are moving in with us. Dad uses a walker. Set up a room for them.", "Which room did you pick and why?"],
    "B": ["We sleep in Bedroom 2 and we need a nursery.", "Is it quiet enough at night?"],
    "C": ["Turn Bedroom 3 into my office, I'm on calls all day.", "Any problem with where the desk is?"],
    "E": ["My parents are moving in with us. Dad uses a walker. Set up a room for them.", "What's the walk to the bathroom like for him at night?"],  # demo candidate: A with a walk
    "D": ["Make Office 3 a sitting room for six people.", "Does everyone get a seat?"],
}

twin = service.get_twin(PROPERTY)
NAMES = prompts.room_display_names(twin)
ROOM = {r.room_id: r for r in twin.rooms}


def by_label(label: str) -> str:
    return next(r.room_id for r in twin.rooms if r.label == label or NAMES[r.room_id].startswith(label + " ·"))


def run(scenario: str, n: int) -> dict:
    user = f"scn-{scenario.lower()}-{n}-{int(time.time())}"
    furniture._default = furniture.FurnitureStore(Path(tempfile.mkdtemp()) / "furniture.sqlite")
    design_gate.reset()
    memory = InMemoryStore()
    ctx = AgentContext(user_id=user, property_id=PROPERTY)
    guide = build_agent(PROPERTY, store=memory, checkpointer=InMemorySaver())
    designer = build_designer(PROPERTY, store=memory)
    conversation = design_gate.key(None, user)
    log = (OUT / f"{scenario}-{n}.log").open("w")
    calls: list[tuple[str, dict]] = []
    said: list[str] = []
    pending: list[dict] = []  # picture cards the guide asked a question with, not yet tapped

    def write(line: str) -> None:
        log.write(line + "\n")
        log.flush()

    def turn(text: str) -> None:
        write(f"\n=== USER/EVENT ===\n{text}")
        jobs, reply = [], ""
        t0 = time.time()
        for kind, *rest in stream_turn(guide, text, ctx, ctx.thread_id):
            if kind == "action":
                calls.append((rest[0], rest[1]))
                write(f"  -> {rest[0]}({json.dumps(rest[1], default=str)})")
            elif kind == "ui":
                write(f"  ui {rest[0]['type']}")
                if rest[0]["type"] == BACKGROUND_DESIGN:
                    jobs.append(rest[0]["payload"])
                if rest[0]["type"] == "choice.ask":
                    pending[:] = [rest[0]["payload"]]
            elif kind in ("token", "message"):
                reply += rest[0] if kind == "token" else ("" if reply else rest[0])
        said.append(reply)
        write(f"=== GUIDE ({time.time() - t0:.0f}s) ===\n{reply}")
        for job in jobs:
            design(job)

    def design(job: dict) -> None:
        room_id, words, f, t0 = job["room_id"], job.get("brief", ""), job.get("furnish"), time.time()
        try:
            if f is not None:
                chosen = furnishing.gather(user, ctx.furniture_user, PROPERTY, room_id, f.get("look") or None, f.get("level") or "full",
                                           f.get("use"), f.get("keep_item_ids") or (), f.get("avoid") or ())
                if not chosen.items:
                    raise RuntimeError(chosen.note or "the library had nothing that fits this room")
                memory_files.sync_inventory(memory, user, furniture.furniture_store().list_items(user))
                words = furnishing.brief(chosen, words)
                write(f"  [furnish {time.time() - t0:.0f}s] {len(chosen.items)} pieces, failed: {chosen.failed}, skipped: {chosen.skipped}")
            write(f"  [designer brief]\n{words}")
            report = run_designer(designer, ctx, room_id, words, lambda k, d: write(f"  [designer {k}] {str(d)[:300]}"))
            message = event_message("layouts_ready", f"room_id: {room_id}\n{report}")
        except Exception as e:
            write(traceback.format_exc())
            design_gate.dropped(conversation, room_id)
            message = event_message("layouts_failed", f"room_id: {room_id}\n{e}")
        else:
            design_gate.ready(conversation, room_id, message)
        write(f"  [design job {time.time() - t0:.0f}s]")
        turn(message)

    started = time.time()
    for text in SCENARIOS[scenario]:
        design_gate.user_spoke(conversation)
        pending.clear()
        turn(text)
        taps = 0
        while pending and taps < 3:  # the user taps the first card, as the app would report it (agent_bridge.py)
            card, taps = pending.pop(), taps + 1
            option = card["options"][0]
            note = choice_note(card["choice_id"], option["option_id"])
            design_gate.user_spoke(conversation)
            turn(f'{option["label"]}\n(They tapped this card{f": {note}" if note else ""}.)')

    write("\n=== TOOL RESULTS ===")
    for m in guide.get_state({"configurable": {"thread_id": ctx.thread_id}}).values["messages"]:
        if m.type == "tool":
            write(f"[{m.name}] {str(m.content)[:1500]}\n")

    result = {"scenario": scenario, "run": n, "seconds": round(time.time() - started), "tools": [c[0] for c in calls], **check(scenario, user, calls, said)}
    write("\n=== CHECKS ===\n" + json.dumps(result, indent=1, default=str))
    (OUT / f"{scenario}-{n}.json").write_text(json.dumps(result, indent=1, default=str))
    log.close()
    return result


# --- checks: from the store, the solver and the simulation, never from the guide's words ----------


def layouts_of(user: str) -> dict[str, list]:
    rooms: dict[str, list] = {}
    for layout in furniture.furniture_store().list_proposals(user, PROPERTY):
        rooms.setdefault(layout.room_id, []).append(layout)
    return rooms


def check_layout(user: str, layout) -> dict:
    items = {str(i.item_id): i for i in furniture.furniture_store().list_items(user)}
    placed = [(items[str(p.item_id)], p) for p in layout.placements if str(p.item_id) in items]
    unknown = len(layout.placements) - len(placed)
    table = any(i.item_class == "dining_table" for i, _ in placed)
    floor = [(i, p) for i, p in placed if i.item_class not in DRESSING and not (table and i.item_class == "chair")]
    solver_items = [layout_solver.Item(str(i.item_id), i.label, i.item_class, i.dims_m.w, i.dims_m.d, i.dims_m.h) for i, _ in floor]
    poses = {str(i.item_id): layout_solver.Pose(p.position.x, p.position.z, p.yaw_deg) for i, p in floor}
    broken = layout_solver.violations(ROOM[layout.room_id], twin.openings, solver_items, poses, twin.walls, twin.sweeps)
    classes = sorted(i.item_class for i, _ in placed)
    return {
        "violations": broken, "unknown_pieces": unknown, "classes": classes,
        "pieces": [f"{i.label[:50]} ({i.item_class}, {i.dims_m.w:.2f}x{i.dims_m.d:.2f})" for i, _ in floor],
        "seats": sum(SEATS.get(c, 0) for c in classes),
        "_floor": floor,
    }


def door_widths(a: str, b: str) -> list[float]:
    out = []
    for o in twin.openings:
        if o.kind == "door" and {o.room_id, o.other_room_id} == {a, b}:
            p, q = o.polygon[0], o.polygon[1]
            out.append(round(math.dist((p.x, p.z), (q.x, q.z)), 2))
    return out


def route_facts(a: str, b: str) -> dict | None:
    path = views.route(twin, a, b)
    if path is None:
        return None
    doors = [door_widths(x, y) for x, y in zip(path.room_ids, path.room_ids[1:])]
    return {"to": NAMES[b], "distance_m": round(path.distance_m, 1), "stairs": path.floor_changes, "through": [NAMES[r] for r in path.room_ids],
            "door_widths_m": doors, "unmapped_steps": path.unmapped_steps}


def night_levels() -> dict[str, float]:
    return {r.room_id: r.laeq_dba for r in service.noise_house(PROPERTY, 2, NoiseScenario()).rooms}


def window_centres(room_id: str) -> list[tuple[float, float]]:
    out = []
    for o in twin.openings:
        if o.kind == "window" and room_id in (o.room_id, o.other_room_id):
            p, q = o.polygon[0], o.polygon[1]
            out.append(((p.x + q.x) / 2, (p.z + q.z) / 2))
    return out


def check(scenario: str, user: str, calls: list, said: list[str]) -> dict:
    from datetime import date

    rooms = layouts_of(user)
    names = [c[0] for c in calls]
    out: dict = {"rooms_designed": [NAMES[r] for r in rooms], "replies": said, "checks": {}}
    if not rooms:
        out["checks"]["layout_saved"] = False
        return out
    room_id = max(rooms, key=lambda r: len(rooms[r]))
    results = [check_layout(user, layout) for layout in rooms[room_id]]
    best = results[0]
    floors = [(r.pop("_floor")) for r in results][0]
    out["room"] = NAMES[room_id]
    out["layouts"] = results
    c = out["checks"]
    c["layout_saved"] = True
    c["zero_violations_all_options"] = all(not r["violations"] for r in results)
    c["no_unknown_pieces"] = all(r["unknown_pieces"] == 0 for r in results)

    if scenario in ("A", "E"):
        baths = [r.room_id for r in twin.rooms if r.label.lower().startswith("bathroom")]
        routes = [f for b in baths if (f := route_facts(room_id, b))]
        nearest = min(routes, key=lambda f: f["distance_m"]) if routes else None
        out["nearest_bathroom"] = nearest
        c["room_on_floor_1"] = "Floor 1" in NAMES[room_id]
        c["bathroom_route_no_stairs"] = bool(nearest) and nearest["stairs"] == 0
        c["doors_on_route_at_least_0.8m"] = bool(nearest) and all(w and max(w) >= 0.8 for w in nearest["door_widths_m"])
        c["has_bed"] = "bed" in best["classes"]
        c["called_viewer_walk"] = "viewer_walk" in names
    elif scenario == "B":
        night = night_levels()
        parents = by_label("Bedroom 2")
        candidates = sorted((night[r.room_id], NAMES[r.room_id]) for r in twin.rooms if r.label.startswith("Bedroom") and "Floor 2" in NAMES[r.room_id] and r.room_id != parents and r.room_id in night)
        out["bedrooms_at_2am_dba"] = [(round(v, 1), n) for v, n in candidates]
        out["walk_from_bedroom_2"] = route_facts(parents, room_id) if room_id != parents else None
        out["room_night_report_dba"] = service.noise_report(PROPERTY, room_id).laeq_night_dba
        c["among_two_quietest_floor2_bedrooms"] = NAMES[room_id] in [n for _, n in candidates[:2]]
        c["has_crib_or_bed"] = any(k in best["classes"] for k in ("crib", "bed"))
        c["called_noise_tool"] = any(n in names for n in ("outside_noise", "sound_heard_from"))
        c["called_viewer_walk"] = "viewer_walk" in names
    elif scenario == "C":
        day = date(2026, 11, 16)
        sun = service.sun_room_day(PROPERTY, room_id, day, height_m=1.1)
        out["sun_seated"] = {"hours": round(sun.total_hours, 1), "spans": [f"{i.start:%H:%M}-{i.end:%H:%M}" for i in sun.intervals]}
        hourly = {h.hour: h.laeq_dba for h in service.noise_report(PROPERTY, room_id).hourly}
        out["noise_9_to_17_dba"] = [round(hourly[h], 1) for h in range(9, 18)]
        desk = next(((i, p) for i, p in floors if i.item_class == "desk"), None)
        wins = window_centres(room_id)
        if desk and wins:
            i, p = desk
            front = (math.sin(math.radians(p.yaw_deg)), math.cos(math.radians(p.yaw_deg)))
            w = min(wins, key=lambda w: math.dist(w, (p.position.x, p.position.z)))
            to_window = (w[0] - p.position.x, w[1] - p.position.z)
            norm = math.hypot(*to_window) or 1
            angle = math.degrees(math.acos(max(-1, min(1, (front[0] * to_window[0] + front[1] * to_window[1]) / norm))))
            out["desk"] = {"yaw_deg": p.yaw_deg, "angle_front_to_nearest_window_deg": round(angle), "distance_to_window_m": round(norm, 2)}
        c["has_desk_and_chair"] = "desk" in best["classes"] and any(k in best["classes"] for k in ("chair", "armchair"))
        c["called_sun_tool"] = any(n in names for n in ("sun_by_room", "sun_report", "daylight_at"))
        c["called_noise_tool"] = any(n in names for n in ("outside_noise", "sound_heard_from"))
    elif scenario == "D":
        out["seats_per_option"] = [r["seats"] for r in results]
        c["six_seats_in_some_option"] = max(r["seats"] for r in results) >= 6
        c["room_is_office_3"] = room_id == by_label("Office 3")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenarios", nargs="+", choices=sorted(SCENARIOS))
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--first", type=int, default=1, help="number of the first run (to add runs to earlier ones)")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    for s in args.scenarios:
        for n in range(args.first, args.first + args.runs):
            try:
                r = run(s, n)
                print(s, n, f"{r['seconds']}s", r.get("room"), json.dumps(r["checks"]), flush=True)
            except Exception:
                print(s, n, "CRASHED", flush=True)
                traceback.print_exc()


if __name__ == "__main__":
    main()
