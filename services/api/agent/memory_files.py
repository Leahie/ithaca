"""Read and write a user's memory files from outside the agent (the /me page, inventory sync).

The agent reaches the same files as /memories/<path> through deepagents' StoreBackend, whose
record format this module follows. Paths here are relative: "profile.md", "properties/<id>.md".
"""

from __future__ import annotations

from datetime import datetime, timezone

from langgraph.store.base import BaseStore

from packages.schema.inventory import InventoryItem

INVENTORY = "inventory.md"


def _ns(user_id: str) -> tuple[str, ...]:
    return (user_id,)


def list_files(store: BaseStore, user_id: str) -> list[str]:
    return sorted(item.key.lstrip("/") for item in store.search(_ns(user_id), limit=500))


def read_file(store: BaseStore, user_id: str, path: str) -> str | None:
    item = store.get(_ns(user_id), "/" + path.lstrip("/"))
    if item is None:
        return None
    content = item.value.get("content", "")
    return content if isinstance(content, str) else "\n".join(content)


def write_file(store: BaseStore, user_id: str, path: str, text: str) -> None:
    key = "/" + path.lstrip("/")
    now = datetime.now(timezone.utc).isoformat()
    old = store.get(_ns(user_id), key)
    created = old.value.get("created_at", now) if old else now
    store.put(_ns(user_id), key, {"content": text, "encoding": "utf-8", "created_at": created, "modified_at": now})


def delete_file(store: BaseStore, user_id: str, path: str) -> bool:
    key = "/" + path.lstrip("/")
    found = store.get(_ns(user_id), key) is not None
    store.delete(_ns(user_id), key)
    return found


def render_inventory(items: list[InventoryItem]) -> str:
    """inventory.md: a view of the database for the agent's prompt (B3). Never edited by hand."""
    lines = ["# Furniture the user owns", "Generated from the inventory database. Use `inventory_search` for details.", ""]
    for i in items:
        look = ", ".join(v for v in (i.attributes.style, i.attributes.color, i.attributes.material) if v)
        lines.append(
            f"- {i.label} ({i.item_class}) · item_id `{i.item_id}` · "
            f"{i.dims_m.w:.2f} m wide × {i.dims_m.d:.2f} m deep × {i.dims_m.h:.2f} m high" + (f" · {look}" if look else "")
        )
    if not items:
        lines.append("(nothing yet)")
    return "\n".join(lines) + "\n"


def sync_inventory(store: BaseStore, user_id: str, items: list[InventoryItem]) -> None:
    text = render_inventory(items)
    if read_file(store, user_id, INVENTORY) != text:
        write_file(store, user_id, INVENTORY, text)
