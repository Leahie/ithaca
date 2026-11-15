# Style decisions

The rules the web app's look follows, and why. Tokens live in `apps/web/src/app/globals.css`
(colours) and `apps/web/src/components/ui/index.tsx` (shared class strings). Add a line to the
log at the bottom whenever a decision changes.

## Two surfaces, never mixed

| Surface | Where | Look |
| --- | --- | --- |
| **Paper** | Pages and chrome that are not over the house: top bar, room rail, Me page, the phone remote | `bg-surface` / `bg-raised`, evergreen text (`text-text`), `border-line` |
| **Glass** | Anything floating over the 3D tour: pills, badges, popovers, panels, the floor plan, the sound bar | Dark translucent black with blur, white text |

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
- A modal that deletes for good (Reset everything, on the Me page) says in plain words what goes, what is kept, and that it cannot be undone. The way out ("Keep my data") has the focus, so Enter keeps. The delete button is a filled danger pill (`bg-danger`, white text); while it runs it says so, and Escape and the backdrop do nothing. An error is shown in the modal, where the click was.
- The big screen's furniture menu is a paper modal too. Glass is for what floats over a house you are still looking at; a window that dims and blurs the whole house is a page of its own, and a page is paper.

## Tabs

- When a view has distinct sources, split them into tabs instead of stacking sections ("Your furniture" above "From the catalog" was wrong).
- Tabs are icons in a pill track (`bg-ink`); the active tab is paper (`bg-raised`) and also shows its label. Each tab has an `aria-label` and `title`.
- Icons: backpack = furniture you own, people = the rest of the family's, shopping bag = the catalog and the library, sparkle = a whole room furnished in a look, camera = from a photo.
- Where there is room (the top bar, the furniture menu from `lg` up) the other tabs show their labels too.
- The family tab groups pieces by owner under small uppercase headers ("MAYA · 3"); the host's pieces, which have no member name, are "Shared".

## Filtering

- Filter with a search box (`SearchBox` in `CatalogBrowser.tsx`), never a wall of filter chips. Every word typed must match the name, kind or owner.
- The library (thousands of pieces) is too large for a search box alone, so its sections are a menu: a rail on the big screen (an icon, the name and the count on each row), one row of pills that scrolls sideways on the phone, "All" first. The collections are a second, shorter list under the rail. That is the whole menu: looks, colours and materials are words in the search box, never chips.
- The search covers the whole library, waits for a pause in typing, and keeps the chosen section and collection. The counts follow the search; a section the search finds nothing in fades, it does not go away, so the menu never moves.
- Say so when nothing matches, quoting the search, and name the section and collection it was kept to, with one button to drop them.

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
- A library card (`LibraryCard`) also shows up to three small dots from the piece's palette, at the right of the size line. A design that comes in several colours is one card with a small "5 colours" pill on the bottom-left of its picture; the pill opens those colours in place of the grid, with Back, and the grid comes back where it was. The pill sits on the picture so every card keeps the same height.
- Small things are sized in centimetres ("12×12×30 cm"), furniture in meters.
- Pictures sit in fixed square boxes and load lazily; the first page shows as card outlines of the same size, more pages load before the end of the grid is reached, and a new search dims the old cards until its own arrive. Nothing jumps.
- A piece's model is fetched the first time it is used, which can take 20 seconds: the card says "Adding…" and one line under the grid says what is being fetched. On the big screen cards tapped meanwhile wait their turn; an added card keeps its check and can be tapped again for a second one.

## Furniture menu

- The way in is one solid pill, "Furniture" with a sofa icon, the first thing in the top bar's right group and the bar's only solid button. Its word always shows. On the clean screen (no top bar) it is a glass pill with the same icon and word, beside "Controls".
- It opens a large paper modal (`FurnitureMenu`): the title, one line that states the size of the library from the data ("7,412 pieces from 2 open 3D libraries"), a wide search box, "Adding to: Living Room", Close. Under it four icon tabs: Browse, Furnish this room, Yours, From a photo.
- Typing in the search box always shows Browse, except under Yours, where it filters your own pieces (the placeholder says which).
- It stays open while pieces are added, and it is hidden, not thrown away, when closed: the search, the section and the scroll are there the next time.
- A piece added from a menu or from storage goes into the room in view at a spot the server picks by what it is (`auto`). In no room it goes to storage, and one line says so.
- One line under the grid is always there (so nothing moves when it changes): what is being fetched, what became of the last piece, or where pieces will go.
- Furnish this room: the looks are large cards (four pictures, the name, palette dots, two lines of text); how full the room should be is a `Segmented` (Essentials, Fully furnished, Lavish); a look in your own words is one line with the modal's one solid button. The progress replaces the looks; when the room is done the menu closes and the Arrange panel opens with the options to keep.
- A job the menu started (a room being furnished, a photo being rebuilt) shows as a glass pill at the top of the house while the menu is closed; pressing it opens the menu on that tab.
- The Arrange panel keeps the plan, the Keep bar and storage as chips. Its "Add furniture" opens the menu. With several options on show for the room, its Keep bar letters them (A, B, C) to try each before keeping one, as the phone does.

## Arranging on the big screen

- While the phone is on its furniture screen, the big screen shows the room as a small glass plan in the bottom-left corner (`ArrangeMinimap`): the furniture in `white/25`, the piece in hand in leaf, and a leaf cone for where the camera stands and looks. It is drawn the same way up as the phone's plan.
- A piece dragged on the phone moves live in the 3D view and on the small plan before it is saved (`furniture.drag`).
- The selected piece in the 3D view is tinted clearly blue (`SELECTED` in `components/house3d/House3D.tsx`: colours 45% of the way to blue; 10% was too faint to see). No pulsing or scaling: it was distracting.
- The tour draws the house rebuilt from the floor plan, which has none of the seller's furniture: only the family's own pieces stand in it.
- The phone's furniture screen reads top to bottom: the island (Add / In room / Suggest, or the piece's tools, or the layout options) with any sheet dropping below it, then the plan, then a bottom row with the compass and cube on the left and the look stick on the right. The plan keeps a fixed gap the height of the plain island; taller islands float over its top edge so the plan never jumps mid-drag. No loose hint text floats over the plan.
- The room name in the phone's top bar (with a small map icon) is a button: it opens the Rooms map, and picking a room returns to the screen you came from.
- The phone's cube button (beside the compass) switches the big screen to the room from above (`furniture.overview`); it is a round toggle, leaf when on. Only then does the big screen look down on the selected piece and follow it.
- Inside (cube off), tapping the plan moves the big screen to the scan point nearest the spot (at least a meter away) and faces it; tapping a piece does the same for the piece.
- In the bottom-right corner, a look stick (`LookStick`) turns the big screen. It is drawn exactly like the Move screen's walk stick (`bg-ink/80` base with a `border-line-strong/50` ring, `bg-main` knob with `shadow-float`), just a little smaller. Push it and the view keeps turning that way, faster the further it is pushed. It replaced a two-finger drag on the plan, which fought with moving furniture.

## Family board

- The Family view is a paper page, like Design and Neighborhood: it is about people, not something over the house. Its tab is the people icon, after Design.
- One paper card per person (`--radius-card`), the big screen's own card first. A member's card has a dot for their phone: leaf and "here", or grey and "away".
- A card reads in a fixed order: Must-haves, Style and likes, Avoid, About, then Rooms, with each wish under its room's name. Section titles are small uppercase labels.
- An entry is one line, with who wrote it ("guide" or "by hand") and when under it in faint text. Its trash can turns the line into a question in place ("Delete …?" with Delete and Keep), as on furniture cards.
- Each section ends in a one-line box that adds an entry; Rooms has a room picker before the box. Nothing opens a modal.

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
- 2026-11-14: The catalog grew to nine looks. Its grid is grouped by look with section headings; the search box matches looks and set names. No style chips, by the filtering rule above.
- 2026-11-14: When the guide asks the user to choose (a room, a look, a piece, a layout), the options come up on the phone as a modal of picture cards over whatever screen it is on (`GuidePopup`): paper panel, dimmed backdrop, rises into place. A room is drawn as its plan, a furniture set as four of its pieces. The same cards show in the chat on the big screen and in the phone's Guide screen. A piece's own number (`#4821`) is shown in grey monospace beside its name wherever pieces are listed.
- 2026-11-14: The Sun Lab page is gone. The Tour already draws the house under the sun of the moment; the time bar under it, the phone and the guide set the moment.
- 2026-11-14: "My room from a photo" is a paper card with two steps (take or pick the photo; look at it and say yes), because it replaces what stands in the room. On the phone it is the first thing under the backpack tab of Add furniture; on the big screen it is at the top of Arrange, Add furniture. Its progress stays in view after the Add window is closed.
- 2026-11-15: A Family view (people icon, after Design): a paper board with one card per person, saying what each wants from a home. The guides write the entries; anyone at the big screen adds a line in place or deletes one with an in-place confirmation.
- 2026-11-15: Furniture gets its own way in: a solid "Furniture" pill with a sofa icon in the top bar (a glass pill on the clean screen), opening a large paper modal, the furniture menu. Adding furniture was hidden behind Arrange, Add furniture; that button now opens the menu.
- 2026-11-15: The menu is paper although it is over the house: a modal that dims and blurs the whole house is a page of its own. What still floats over a visible house stays glass (the pill for a running job).
- 2026-11-15: The menu browses the library (thousands of 3D models from open collections), not the 160-piece catalog. Its sections are a rail with icons and counts, its collections a second short list; on the phone the sections are one row of pills that scrolls sideways. This is the one exception to "a search box, never chips", because nobody can search for what they do not know is there. The catalog's grouping by look is gone from the browser: a look is a word in the search box, or a whole room under Furnish this room.
- 2026-11-15: Library cards show colour dots, and a "5 colours" pill on the picture that opens a design's colours in place. Small things are sized in centimetres. Grids load a page of 60 at a time as they are scrolled, behind card outlines of the same size.
- 2026-11-15: The menu stays open after an add (the card keeps its check) and is only hidden when closed, so it comes back as it was left. Pieces go into the room in view where the server sees fit for what they are (`auto`), from the menu, from storage and on the phone; in no room they go to storage.
- 2026-11-15: Furnish this room: looks as large cards, a three-way "how full" switch, a look in your own words. Its progress shows in place of the looks; done, the menu closes and Arrange opens. The Arrange panel's Keep bar letters several options (A, B, C), as the phone's does.
- 2026-11-15: "My room from a photo" and a single piece from a photo moved from Arrange, Add furniture into the menu's From a photo tab, side by side. Their progress, and a room being furnished, show as a glass pill at the top of the house while the menu is closed.
- 2026-11-15: Storage on the big screen is the menu's Yours tab: cards to place with a tap, grouped by owner when there are several, with the Manage mode for deleting. The Arrange panel keeps its storage chips as a shortcut.
- 2026-11-15: Reset everything: a danger button in the Me page's header opens a paper modal that lists what goes (with counts), what is kept, and that it cannot be undone. "Keep my data" has the focus; "Delete everything" is a filled danger pill, says "Deleting…" while it runs and cannot be closed or pressed again meanwhile. Every other screen then shows a plain paper page ("Everything was reset") in place of what was there. The family popover carries a quiet text link to it, last, in tertiary white.
