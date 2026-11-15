# Furniture: storage, in the house, proposals

Owner: B. Code: `services/api/furniture/` (store, rules, and the register the guides read),
`services/api/routers/furniture.py` (routes), `services/api/agent/design_tools.py` (agent tools).
Tests: `services/api/furniture/tests/`.

Two things are true about a piece of furniture, and they live in two tables:

1. **storage**: the piece exists and belongs to someone (its size, its 3D model, its owner).
2. **in_house**: the piece stands somewhere in a house (which room, where, which way it faces).

A third table, **proposals**, holds arrangements someone suggested (the designer agent, or the
"where could it go?" options). A proposal is never furniture in the house; applying one writes
`in_house` rows.

A fourth, **house_log**, is the history of each house: one line for every piece placed, moved,
taken out or deleted, with who did it and how. The guides read the house and its log back as the
**register** (below), so none of them answers from what it remembers.

## Who is who

| Id | What it is | Sent as |
|---|---|---|
| person (`user_id`) | Whoever is acting. The desktop is `config.userId` (`demo`). A phone that joined the family is its `member_id` (`m-…`), saved on the phone; scanning the family code again on the same phone, or typing the same name on a new one, comes back as the same member. | `X-User-Id` on storage routes |
| house user (`house_user_id`) | The big screen's user, i.e. the session's user. Every phone in the session sees and edits this one house. Callers always send the screen's user; the store keys the rows by the family that screen has on now (`store.house_key`), so each family has its own house and a screen with no family has its own. | `X-User-Id` on in-house and proposal routes |
| family of a person | The person, plus the host and all members of the household they host now (the one made or opened last; a host has one per family name) and of every household they belong to. | derived (`households.family_of`) |

IP addresses are not used: through the Cloudflare tunnel every phone arrives from Cloudflare's
addresses, and phones on one Wi-Fi share one public address.

A household's host is the user who created it (`households.host_user_id`); a session opened for a
household also sets it if it is missing.

## Tables (SQLite, `data/agent/furniture.sqlite`)

```sql
CREATE TABLE storage (
    item_id    TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL CHECK (owner_id <> ''),
    label      TEXT NOT NULL,
    item_class TEXT NOT NULL,
    w_m REAL NOT NULL CHECK (w_m > 0),
    d_m REAL NOT NULL CHECK (d_m > 0),
    h_m REAL NOT NULL CHECK (h_m > 0),
    body       TEXT NOT NULL,              -- the InventoryItem as JSON (urls, attributes, price)
    created_at TEXT NOT NULL,
    tag        TEXT UNIQUE                 -- the piece's own number, e.g. "4821" (F10)
);

CREATE TABLE in_house (
    house_user_id TEXT NOT NULL,
    property_id   TEXT NOT NULL,
    item_id       TEXT NOT NULL REFERENCES storage(item_id) ON DELETE CASCADE,
    room_id       TEXT NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,   -- interior frame, metres; y = floor height
    yaw_deg       REAL NOT NULL CHECK (yaw_deg >= 0 AND yaw_deg < 360),
    placed_by     TEXT NOT NULL,           -- the person who put it there last
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (house_user_id, property_id, item_id)
);

CREATE TABLE proposals (
    proposal_id   TEXT PRIMARY KEY,        -- = LayoutVariant.layout_id
    house_user_id TEXT NOT NULL,
    property_id   TEXT NOT NULL,
    room_id       TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('proposed', 'chosen', 'rejected')),
    created       REAL NOT NULL,
    body          TEXT NOT NULL            -- the LayoutVariant as JSON
);

CREATE TABLE house_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    house_key   TEXT NOT NULL,             -- the family's house, or a screen's own (store.house_key)
    property_id TEXT NOT NULL,
    at          TEXT NOT NULL,
    action      TEXT NOT NULL,             -- placed | moved | removed | deleted
    item_id     TEXT NOT NULL,
    tag         TEXT,                      -- kept here so a deleted piece can still be named
    label       TEXT NOT NULL,
    room_id     TEXT,
    by          TEXT NOT NULL,             -- the person
    via         TEXT NOT NULL              -- hand | layout (a kept proposal) | guide (an agent's tool call)
);
```

Tables from before this spec (`inventory_items`, `layouts`) are migrated once on start:
items go to `storage`, every layout becomes a proposal, and each room's chosen layout is written
into `in_house`. The old tables are then renamed `*_old`. A `storage` table from before F10 gets
the `tag` column, and every piece in it a number, the first time it is opened.

## Invariants

- **F1. One owner.** Every stored piece has exactly one owner. Only the owner, or the host of a
  household the owner belongs to, may delete it from storage.
- **F2. Once per house.** A stored piece is in a given house at most once (primary key). The same
  piece may stand in several houses: you can picture your sofa in each house you tour.
- **F3. Nothing dangles.** Every `in_house` row points at a stored piece. Deleting a piece from
  storage removes it from every house (foreign key, `ON DELETE CASCADE`) and from every proposal.
- **F4. The room comes from the coordinates.** `room_id` is never taken from the client. It is
  the room on the piece's floor whose outline contains `(x, z)` (the smallest, if outlines nest),
  and `y` is that room's floor height. A spot inside no room is refused (409 `outside_room`).
  Moving a piece through a doorway changes its room. The floor is the request's `floor_id`, else
  the hinted room's floor, else the floor the piece stands on now; with none of these, a spot
  under rooms on two floors is refused (409 `which_floor`) rather than guessed.
- **F5. Yaw is normalised.** `0 <= yaw_deg < 360` after rounding (I-G12).
- **F6. The family pool.** A house holds only pieces from the house user's family (F1's owners).
  Everyone in the family sees and places everyone's pieces; each piece shows its owner.
- **F7. Removing removes.** Taking a piece out of the house deletes its one `in_house` row. By F2
  and F8 nothing else can still show it in that house.
- **F8. Proposals are not furniture.** Nothing reads a proposal as furniture in the house.
  Applying one (`POST /layouts/{id}/select`, or `layout.confirm`) replaces its room's `in_house`
  rows in one transaction: the proposal's pieces stand exactly at its poses (moved out of other
  rooms if they were elsewhere), and the room's other pieces go back to storage. The applied
  proposal becomes `chosen`; any other chosen proposal of that room goes back to `proposed`.
- **F9. Hand edits are the user's call.** A move or turn by hand is saved anywhere inside a room
  (F4). What a designer would flag (an overlap, a blocked door or path) comes back in `warnings`
  and is never stored. `snap: true` instead asks the solver for the nearest spot within 2 m that
  breaks no rule (409 `no_room` if there is none).
- **F10. Every piece has a number.** A stored piece gets a random number when it is first saved
  (`InventoryItem.tag`: four digits while they last, then more). It is unique among all pieces,
  it never changes, and a deleted piece's number is not handed out again while the log still
  names it. People and guides call a piece by it ("#4821"); `item_id` stays the database key.
  Agent tools take the number wherever they ask for an `item_id` (the full id works too), and
  show layouts by the first eight characters of their id.
- **F11. Every change is logged, and the register is rewritten.** Each write to `in_house`, by
  any path (a hand move, a tap on a catalog card, a kept layout, a guide's tool call, a deleted
  piece leaving its rooms), adds a `house_log` line in the same transaction. Nudging one piece
  again within two minutes updates its last line rather than adding one. After the transaction
  the house's register is written again (next section).

## The register

`furniture/ledger.py` reads a house back as one document: every room with a piece in it; each
piece with its number, size, owner, where in the room it stands in words ("against the north
wall, facing south", worked out from the room's outline) and who placed it last; the family's
pieces that stand in no room; and the last ten changes from the log ("Maya's guide put in Living
Room: #3067 Floor lamp").

- **Every agent reads it on every model call.** The guide of each family member, the family's
  shared guide and the designer are shown the register at the end of their instructions, read
  from the database at that moment (`HouseRegisterMiddleware`). A guide is never told about
  furniture any other way, so what it says matches the house even after a hand move, another
  person's change or a restart.
- **It is saved on every change.** The store tells the ledger after each change (F11), and the
  ledger writes the register to the agents' memory store as `/memories/house/<property_id>.md`,
  in one place per house (`("house", <house key>)`), shared by everyone furnishing that house.
  Guides can read the file and cannot write it; the /me page lists it, read-only.
- **As data:** `GET /api/properties/{pid}/furniture/register`.
- **Someone else's piece.** `place_piece` and `remove_from_room` refuse, the first time, to touch
  a piece that another member of the family owns or placed: the guide must tell its own person
  whose it is and get a yes, then call again with `go_ahead_with_others_piece`. The house's own
  pieces (the big screen's) are everyone's.

## What a screen draws

Every screen draws the house's `in_house` pieces. The one exception is a preview: while the
selected layout (`viewer.selected_layout_id`) is a proposal still `proposed`, its room shows the
proposal's pieces instead. Keeping it (F8) or selecting nothing (`layout.select` with
`layout_id: null`) ends the preview. Hand edits end it too, so the user always edits what is
really in the house.

After any change to storage or the house, the client that made it sends `layout.changed`; after
applying a proposal on `layout.confirm`, the server sends it. Every screen then reloads.

## Routes (prefix `/api`)

Storage, as the person (`X-User-Id` = person):

| Route | Does |
|---|---|
| `GET /inventory` | The person's family pool (F6). `user_id` is the owner, `owner_name` their name. |
| `GET /inventory/{item_id}` | One piece from the pool. |
| `DELETE /inventory/{item_id}` | Delete for good (F1, F3). 403 for someone else's piece. |
| `POST /catalog/{id}/add`, `POST /objects/ingest`, `POST /objects/import` | New piece owned by the person. |

In the house, as the house user (`X-User-Id` = house user):

| Route | Does |
|---|---|
| `GET /properties/{pid}/furniture[?room_id=]` | The house's pieces (`HousePiece[]`). |
| `PUT /properties/{pid}/furniture/{item_id}` | Place or move one piece (`PlacePiece`: `x`, `z`, optional `yaw_deg`, `floor_id`, `room_id`, `snap`). Returns the `HousePiece` with `warnings`. |
| `DELETE /properties/{pid}/furniture/{item_id}` | Take it out of the house (F7). 204, or 404 if it isn't there. |
| `GET /properties/{pid}/furniture/register` | The register: rooms, pieces with their numbers and places in words, storage, last changes (F10, F11). |

Proposals, as the house user:

| Route | Does |
|---|---|
| `GET /layouts?property_id=&room_id=` | Proposals, newest first. |
| `GET /layouts/{id}` | One proposal (the sample layouts in `fixtures/layouts/` resolve too). |
| `POST /layouts/{id}/select` | Apply it (F8). |
| `POST /layouts/{id}/feedback` | Store the user's words about it. |
| `POST /layouts/options` | Up to three proposals that add one piece to a room at different spots. |
