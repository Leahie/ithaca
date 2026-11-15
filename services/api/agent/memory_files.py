"""Read and write a user's memory files from outside the agent (the /me page, inventory sync).

The agent reaches the same files as /memories/<path> through deepagents' StoreBackend, whose
record format this module follows. Paths here are relative: "profile.md", "properties/<id>.md".
"""

from __future__ import annotations

from datetime import datetime, timezone

from langgraph.store.base import BaseStore

from packages.schema.inventory import InventoryItem

INVENTORY = "inventory.md"


def _ns(owner: str | tuple[str, ...]) -> tuple[str, ...]:
    """A person's files live under (user_id,). A tuple names a shared place as it is, such as a
    house's register (furniture/ledger.py)."""
    return owner if isinstance(owner, tuple) else (owner,)


def list_files(store: BaseStore, user_id: str | tuple[str, ...]) -> list[str]:
    return sorted(item.key.lstrip("/") for item in store.search(_ns(user_id), limit=500))


def read_file(store: BaseStore, user_id: str | tuple[str, ...], path: str) -> str | None:
    item = store.get(_ns(user_id), "/" + path.lstrip("/"))
    if item is None:
        return None
    content = item.value.get("content", "")
    return content if isinstance(content, str) else "\n".join(content)


def write_file(store: BaseStore, user_id: str | tuple[str, ...], path: str, text: str) -> None:
    key = "/" + path.lstrip("/")
    now = datetime.now(timezone.utc).isoformat()
    old = store.get(_ns(user_id), key)
    created = old.value.get("created_at", now) if old else now
    store.put(_ns(user_id), key, {"content": text, "encoding": "utf-8", "created_at": created, "modified_at": now})


def delete_file(store: BaseStore, user_id: str | tuple[str, ...], path: str) -> bool:
    key = "/" + path.lstrip("/")
    found = store.get(_ns(user_id), key) is not None
    store.delete(_ns(user_id), key)
    return found


def add_note(store: BaseStore, user_id: str | tuple[str, ...], path: str, heading: str, line: str, keep: int = 40) -> None:
    """Add one line under a heading of a memory file, making the file or the heading if it is not
    there. For facts the app itself knows happened (a layout was kept), so a person's memory does
    not depend on their guide choosing to write them down. The newest `keep` lines stay."""
    text = read_file(store, user_id, path) or f"# {path.rsplit('/', 1)[-1].removesuffix('.md')}\n"
    lines = text.rstrip("\n").split("\n")
    if heading not in lines:
        lines += ["", heading]
    start = lines.index(heading) + 1
    end = next((k for k in range(start, len(lines)) if lines[k].startswith("#")), len(lines))
    section = [l for l in lines[start:end] if l.strip()] + [line]
    lines[start:end] = section[-keep:] + ([""] if end < len(lines) else [])
    write_file(store, user_id, path, "\n".join(lines) + "\n")


def render_inventory(items: list[InventoryItem]) -> str:
    """inventory.md: a view of the database for the agent's prompt (B3). Never edited by hand."""
    lines = ["# Furniture the user owns", "Generated from the inventory database. Each piece is named by its number (#1234). "
             "Where pieces stand is in the house register, not here.", ""]
    for i in items:
        look = ", ".join(v for v in (i.attributes.style, i.attributes.color, i.attributes.material) if v)
        lines.append(
            f"- #{i.tag} {i.label} ({i.item_class}) · "
            f"{i.dims_m.w:.2f} m wide × {i.dims_m.d:.2f} m deep × {i.dims_m.h:.2f} m high" + (f" · {look}" if look else "")
        )
    if not items:
        lines.append("(nothing yet)")
    return "\n".join(lines) + "\n"


def sync_inventory(store: BaseStore, user_id: str, items: list[InventoryItem]) -> None:
    """Also written for the family the user has on their big screen, if any: that family's shared
    guide keeps its files under the family (AgentContext.memory_user)."""
    from services.api.realtime.households import households

    text = render_inventory(items)
    for owner in {user_id, households().current_for(user_id) or user_id}:
        if read_file(store, owner, INVENTORY) != text:
            write_file(store, owner, INVENTORY, text)
