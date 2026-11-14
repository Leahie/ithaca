# Style decisions

The rules the web app's look follows, and why. Tokens live in `apps/web/src/app/globals.css`
(colours) and `apps/web/src/components/ui/index.tsx` (shared class strings). Add a line to the
log at the bottom whenever a decision changes.

## Two surfaces, never mixed

| Surface | Where | Look |
| --- | --- | --- |
| **Paper** | Pages and chrome that are not over the house: top bar, room rail, Me page, the phone remote | `bg-surface` / `bg-raised`, evergreen text (`text-text`), `border-line` |
| **Glass** | Anything floating over the 3D tour or the Sun Lab: pills, badges, popovers, panels, the floor plan, the sound bar | Dark translucent black with blur, white text |

If it sits on top of the house, it is glass. A white card over the house is a bug.

Glass tokens (`components/ui`):

- `GLASS`: pills and badges. `border-white/30 bg-black/35 text-white backdrop-blur-md`.
- `GLASS_PANEL`: popovers and panels. Darker (`bg-black/55`, `backdrop-blur-xl`) so text and drawings stay readable over a bright room.
- `glassToggle(on)`: buttons on glass. Off is white text with a faint white hover; on is the green accent.

Secondary text on glass is `text-white/75`, tertiary `text-white/60`, dividers `border-white/20`.
Errors on glass use `text-coral`, not `text-danger` (too dark to read).

## Accent: tinted green, not white, not neon

- The highlight on glass is a **leaf tint**, never solid leaf: `bg-leaf/25` with a `leaf/60` inset ring and white text (`GLASS_GREEN` in `components/ui`). Solid `#2bdc86` over a dark room reads as neon.
- Selected tabs, pressed toggles, the primary glass button and the selected room on the plan all use it.
- Hover on glass is a faint tint (`white/15`, or `leaf/15` on the plan), never solid white.
- Solid white is reserved for content that must be white: QR codes (scanners need it) and furniture plan previews.
- Small status dots (connected, here) may stay solid leaf: they are too small to glare.

## Modals

- A modal (the floor plan) renders in a portal to `document.body`, `fixed inset-0 z-50`, above every overlay.
- The backdrop dims and blurs the house: `bg-black/40 backdrop-blur-sm`.
- Clicking the backdrop or pressing Escape closes it, unless the phone is driving it (then only the phone closes it).
- It is centered and sized to its content's shape within the viewport, never pinned to a corner.
- Scrolling inside a modal never moves what is underneath. Use `MODAL_BACKDROP` (`touch-none overscroll-none`), `MODAL_SCROLL` for the scrolling body (`touch-pan-y overflow-y-auto overscroll-contain`) and `useScrollLock()` while it is open.
- The panel around a scrolling body has a definite height (`h-[min(85dvh,44rem)]`), not only a `max-h`: mobile Safari will not shrink a flex child to a max-height, so the list gets clipped instead of scrolling. A sheet without a fixed height puts the `max-h` on the scroller itself.
- On the phone, a modal's panel is paper (`bg-surface`, `rounded-3xl`) over the same dimmed, blurred backdrop. The phone's Add furniture is a modal, not a sheet stacked in the room view.

## Tabs

- When a view has distinct sources, split them into tabs instead of stacking sections ("Your furniture" above "From the catalog" was wrong).
- Tabs are icons in a pill track (`bg-ink`); the active tab is paper (`bg-raised`) and also shows its label. Each tab has an `aria-label` and `title`.
- Icons: backpack = furniture you own, people = the rest of the family's, shopping bag = the catalog.
- The family tab groups pieces by owner under small uppercase headers ("MAYA · 3"); the host's pieces, which have no member name, are "Shared".

## Filtering

- Filter with a search box (`SearchBox` in `CatalogBrowser.tsx`), never a wall of filter chips. Every word typed must match the name, kind or owner.
- Say so when nothing matches, quoting the search.

## Popovers

- Popovers opened from glass or over the house (family code, phone pairing) use `GLASS_PANEL` with `rounded-3xl`.
- Buttons inside are glass buttons; the primary action is leaf.

## Floor plan

- Each floor is framed on its own extent, so it fills the modal; the modal resizes when the floor changes.
- Rooms: faint fill (`white/5`), thin `white/15` outline. Walls: `white/70`, never pure white.
- Labels always white. The selected room is `leaf/30` fill with a thicker `leaf/80` outline.
- While the phone is on its Rooms screen, the phone's Floor switch picks which floor the big screen shows (`remote.view` carries `floor_id`).

## Phone remote

- The phone is paper (it is not over the house). Choices between a few options use `Segmented`, full width.
- Rooms screen: one floor at a time, picked with a Floor switch at the top; defaults to the floor you are on.

## Furniture cards

- A furniture card is itself the button: tapping the piece adds it to the room. No separate "Add to the room" button.
- One look everywhere (catalog on the phone and in Arrange, the guide's suggestions in chat): `CARD` and `CardImage` in `components/design/CatalogBrowser.tsx`.
- The picture carries the state: a small `+` in the top-right corner, "Adding…" over a faded picture while it adds, then a leaf `✓` once added.
- Cards press in slightly when tapped (`active:scale-[0.97]`) and get a darker border on hover.
- Deleting is a mode, not a second button on every card: a Manage pill in the modal header (gear, then a check and "Done") turns the corner `+` into a red trash can. Tapping a card then turns it into an in-place confirmation ("Delete Sofa? It leaves every house." with Delete and Keep). Cards you may not delete fade out (`opacity-45`) and say whose they are.

## Arranging on the big screen

- While the phone is on its furniture screen, the big screen shows the room as a small glass plan in the bottom-left corner (`ArrangeMinimap`): the furniture in `white/25`, the piece in hand in leaf, and a leaf cone for where the camera stands and looks. It is drawn the same way up as the phone's plan.
- A piece dragged on the phone moves live in the 3D view and on the small plan before it is saved (`furniture.drag`).
- The phone's cube button (under the compass) switches the big screen to the room from above (`furniture.overview`); it is a round toggle, leaf when on. Two fingers on the phone's plan turn the big screen's camera; one finger still moves furniture.

## Shape

- Pills and round buttons: `rounded-full`.
- Glass panels and popovers: `rounded-3xl`.
- Paper cards keep `--radius-card` (14px).

## Log

- 2026-11-14: Chose glass for everything over the house (pills already used it; the floor plan and sound bar were white cards). Added `GLASS`, `GLASS_PANEL`, `glassToggle`.
- 2026-11-14: Floor plan centered and framed per floor instead of top-left in a whole-house frame.
- 2026-11-14: Floor plan becomes a modal above everything with a blurred backdrop. Highlights on glass switch from white to leaf green. Family and pairing popovers and tour badges move to glass.
- 2026-11-14: Solid leaf was too neon. Green on glass is now a tint with a leaf ring (`GLASS_GREEN`); selected-room labels stay white.
- 2026-11-14: Phone Rooms screen shows one floor with a Floor switch; the big screen's plan follows it.
- 2026-11-14: Furniture cards became the add button (no "Add to the room" button); catalog and chat suggestion cards share one look.
- 2026-11-14: Phone Add furniture became a modal with icon tabs (backpack: mine, bag: catalog). Catalog kind chips replaced by a search box; your own furniture is searchable too and shows as picture cards, with "From a photo" as the first card.
- 2026-11-14: Fixed modal scrolling on the phone: definite panel height, contained overscroll, touch blocked outside the scroll area, page scroll locked while open. Same fix for the furniture sheets and the floor plan modal.
- 2026-11-14: Add furniture gets a third tab (people: the family's) and a Manage mode for deleting pieces for good. The big screen gets a small glass plan while arranging, live dragging, a from-above toggle and two-finger look-around from the phone.
