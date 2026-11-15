'use client';

import type { InventoryItem, LayoutVariant, PhotoPoint, PropertyTwin, Room } from '@ithaca/schema';
import { useMemo, useState } from 'react';
import { CatalogBrowser, reason } from '@/components/design/CatalogBrowser';
import { OwnRoom } from '@/components/design/OwnRoom';
import { PhotoButtons, TapToPick, preparePhoto } from '@/components/design/PhotoPiece';
import { PlanEditor } from '@/components/design/PlanEditor';
import { JobRows, useObjectUpload } from '@/components/me/InventorySection';
import { cn } from '@/components/ui';
import { api } from '@/lib/api';
import { useHouseFurniture, useInventory, useLayouts } from '@/lib/data/hooks';
import { invalidate } from '@/lib/data/resource';
import { bounds } from '@/lib/geometry/polygon';
import { previewLayout } from '@/lib/layouts';
import { roomName } from '@/lib/rooms';
import { floorSweeps } from '@/lib/showcase/rooms';
import { dispatch as sessionDispatch, useSession } from '@/lib/store/session';

/** Everything that shows furniture loads it again: here, and (through layout.changed) on every screen. */
export function furnitureChanged(propertyId: string, layoutId: string | null = null): void {
  invalidate('inventory');
  invalidate(`furniture:${propertyId}`);
  invalidate('layouts:');
  invalidate('layout:');
  sessionDispatch('layout.changed', { layout_id: layoutId });
}

/**
 * "Where could it go?" Asks the server for up to three spots for a piece in a room and shows them
 * as layout options on every screen (the big screen draws them; the phone lists them to pick).
 * One spot only: it is simply put in the house. Returns how many options there were.
 */
export async function offerPlacement(propertyId: string, roomId: string, itemId: string): Promise<number> {
  const options = await api().placementOptions({ property_id: propertyId, room_id: roomId, item_id: itemId });
  invalidate('layouts:');
  invalidate('layout:');
  if (options.length === 1) {
    await api().selectLayout(options[0]!.layout_id);
    furnitureChanged(propertyId, options[0]!.layout_id);
  } else if (options.length > 1) {
    sessionDispatch('design.showVariants', { layout_ids: options.map((o) => o.layout_id) });
  }
  return options.length;
}

const LETTERS = 'ABCDEFGH';

/**
 * Floating panel over the 3D views: the furniture standing in the room the user is in, as a plan
 * they can rearrange by hand (docs/specs/furniture.md). The Tour redraws from the
 * house after each change. While a suggested layout is being tried in this room, the panel offers
 * to keep it or go back to what is really there (several options: to switch between them first);
 * any edit goes back first.
 * `onAddFurniture`: the big screen. "Add furniture" opens the furniture menu (FurnitureMenu),
 * where browsing, the photo flows and storage as cards live; this panel keeps the plan, the Keep
 * bar and storage as chips.
 * `inline`: the same editor as a plain block, for the phone remote; every change is announced to
 * the session (`layout.changed`) so the big screen redraws, and "Look at it" turns the big screen
 * to the selected piece. Without `onAddFurniture` it adds from inside itself (AddInline).
 */
export function ArrangePanel({ twin, onClose, inline = false, onAddFurniture }: { twin: PropertyTwin; onClose?(): void; inline?: boolean; onAddFurniture?(): void }) {
  const selectedId = useSession((s) => s.viewer.selected_layout_id);
  const shown = useSession((s) => s.viewer.shown_layout_ids);
  const viewRoomId = useSession((s) => s.viewer.room_id);
  const dispatch = useSession((s) => s.dispatch);
  const layouts = useLayouts(twin.property_id);
  const house = useHouseFurniture(twin.property_id);
  const inventory = useInventory();
  const preview = previewLayout(layouts.data, selectedId);
  const room = twin.rooms.find((r) => r.room_id === (viewRoomId ?? preview?.room_id));
  const pieces = useMemo(() => (house.data ?? []).filter((p) => p.room_id === room?.room_id), [house.data, room?.room_id]);
  const items = useMemo(() => new Map<string, InventoryItem>((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);
  const openings = useMemo(() => (room ? twin.openings.filter((o) => o.room_id === room.room_id || o.other_room_id === room.room_id) : []), [twin.openings, room]);
  const trying = preview && preview.room_id === room?.room_id ? preview : undefined;
  // The options on show for this room (the designer's, the guide's), lettered in the order they were given.
  const options = useMemo(
    () =>
      room
        ? (layouts.data ?? [])
            .filter((l) => l.room_id === room.room_id && l.status !== 'chosen' && shown.includes(l.layout_id))
            .sort((a, b) => shown.indexOf(a.layout_id) - shown.indexOf(b.layout_id))
        : [],
    [layouts.data, shown, room],
  );
  const several = options.length > 1;

  const [browsing, setBrowsing] = useState(false);
  const [placeError, setPlaceError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [offered, setOffered] = useState<string | null>(null);

  /** The user edits what is really in the house: a layout being tried makes way. */
  const backToHouse = () => {
    if (preview) dispatch('layout.select', { layout_id: null });
  };
  const keep = (layout: LayoutVariant) => {
    dispatch('layout.confirm', { layout_id: layout.layout_id });
    // The choice is made: the other options go away on every screen, as when a phone keeps one.
    if (several) dispatch('design.showVariants', { layout_ids: [] });
  };

  /** Delete a piece from the family's furniture for good (it also leaves the house). */
  const discard = async (item: InventoryItem) => {
    setDeleting(null);
    try {
      await api().deleteInventoryItem(item.item_id);
      furnitureChanged(twin.property_id);
    } catch (err) {
      setPlaceError(err instanceof Error ? err.message : String(err));
    }
  };
  const inHouse = new Set((house.data ?? []).map((p) => p.item_id));
  const unplaced = (inventory.data ?? []).filter((i) => !inHouse.has(i.item_id));

  /** Put a stored piece into the room: the server picks the spot by what the piece is. */
  const place = async (item: InventoryItem) => {
    if (!room) {
      setPlaceError('Walk into a room first, then add the piece.');
      return;
    }
    setPlaceError(null);
    if (inline) {
      // On the phone: show where it could go, on the big screen, and let the user pick.
      try {
        const n = await offerPlacement(twin.property_id, room.room_id, item.item_id);
        setBrowsing(false);
        setOffered(n > 1 ? `${n} places for the ${item.label} are on the big screen. Pick one above.` : null);
      } catch (err) {
        setPlaceError(reason(err));
      }
      return;
    }
    const b = bounds(room.polygon);
    try {
      // `auto`: a rug goes under the seating, a lamp on a free table, a picture on the wall; anything else near the middle.
      await api().placePiece(twin.property_id, item.item_id, { x: (b.minX + b.maxX) / 2, z: (b.minZ + b.maxZ) / 2, room_id: room.room_id, floor_id: room.floor_id, snap: true, auto: true });
      backToHouse();
      furnitureChanged(twin.property_id);
      setBrowsing(false);
    } catch (err) {
      setPlaceError(reason(err));
    }
  };

  /** Turn the big screen to a piece: stand at a scan position a few steps away and face it. */
  const lookAt = (_itemId: string, at: { x: number; z: number }) => {
    if (!room) return;
    const away = (sw: { position: { x: number; z: number } }) => Math.hypot(sw.position.x - at.x, sw.position.z - at.z);
    const here = floorSweeps(twin, room.room_id);
    // Near, but not on top of it: a view from 1.5 to 3.5 m shows the whole piece.
    const sweep = [...here].sort((a, b) => Math.abs(away(a) - 2.5) - Math.abs(away(b) - 2.5))[0];
    if (!sweep) return;
    dispatch('viewer.goto', { room_id: room.room_id, at_sweep_id: sweep.sweep_id, look_at: { x: at.x, y: sweep.position.y - 1.0, z: at.z } });
  };

  return (
    <section aria-label="Arrange furniture" className={inline ? 'rounded-2xl border border-line bg-surface px-4 py-3' : 'absolute bottom-4 left-4 z-20 w-[min(26rem,calc(100%-2rem))] rounded-2xl border border-line bg-surface p-3 shadow-lg'}>
      <header className="mb-2 flex min-h-8 items-center gap-2">
        {/* On the phone the room's name is already in the bar above. */}
        <h2 className={inline ? 'text-[11px] font-semibold tracking-wider text-faint uppercase' : 'font-display text-base font-semibold'}>
          {browsing ? 'Add furniture' : inline ? 'Arrange this room' : 'Arrange'}
          {room && !inline ? `: ${roomName(twin, room.room_id)}` : ''}
        </h2>
        <button type="button" onClick={() => (onAddFurniture ? onAddFurniture() : setBrowsing(!browsing))} aria-pressed={onAddFurniture ? undefined : browsing} aria-haspopup={onAddFurniture ? 'dialog' : undefined}
          className={inline ? 'ml-auto h-8 rounded-full border border-line bg-raised px-3 text-xs font-semibold' : 'ml-auto rounded-full bg-ink px-2.5 py-1 text-sm font-semibold hover:bg-raised'}>
          {browsing ? 'Back to the plan' : 'Add furniture'}
        </button>
        {!inline && (
          <button type="button" onClick={onClose} className="rounded-full px-2.5 py-1 text-sm text-muted hover:bg-ink hover:text-text" aria-label="Close the arrange panel">
            Close
          </button>
        )}
      </header>
      {(trying || several) && !browsing && (
        <div role="status" className="mb-2 flex flex-wrap items-center gap-1.5 rounded-xl bg-ink px-2.5 py-1.5 text-sm" data-testid="keep-bar">
          {several ? (
            <>
              <span>Options for this room</span>
              <span role="group" aria-label="Options" className="mr-auto flex gap-1">
                {options.map((l, i) => (
                  <button key={l.layout_id} type="button" aria-pressed={l.layout_id === trying?.layout_id} title={l.rationale || undefined} onClick={() => dispatch('layout.select', { layout_id: l.layout_id })}
                    className={cn('h-7 w-7 rounded-full text-xs font-bold', l.layout_id === trying?.layout_id ? 'bg-main text-on-main' : 'bg-raised hover:bg-surface')}>
                    {LETTERS[i] ?? i + 1}
                  </button>
                ))}
              </span>
            </>
          ) : (
            <span className="mr-auto">You are trying a suggested layout here.</span>
          )}
          {trying && (
            <button type="button" onClick={() => keep(trying)} className="rounded-full bg-main px-2.5 py-0.5 font-semibold text-on-main">
              {several ? `Keep ${LETTERS[options.findIndex((l) => l.layout_id === trying.layout_id)] ?? 'it'}` : 'Keep it'}
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              backToHouse();
              if (several) dispatch('design.showVariants', { layout_ids: [] });
            }}
            className="rounded-full px-2.5 py-0.5 hover:bg-raised"
          >
            Back to the house
          </button>
        </div>
      )}
      {!onAddFurniture && (
        <AddInline
          twin={twin}
          room={room}
          open={browsing}
          place={place}
          onError={setPlaceError}
          onRoomDone={() => {
            backToHouse();
            furnitureChanged(twin.property_id);
            setBrowsing(false);
          }}
        />
      )}
      {browsing ? null : room && pieces.length ? (
        <PlanEditor
          propertyId={twin.property_id}
          room={room}
          openings={openings}
          pieces={pieces}
          items={items}
          onChanged={backToHouse}
          onLookAt={inline ? lookAt : undefined}
          onSelect={inline ? (id) => dispatch('furniture.select', { item_id: id }) : undefined}
        />
      ) : (
        <p className="text-sm text-muted">{!room ? 'Walk into a room to arrange it.' : house.data ? 'Nothing stands in this room yet. Add furniture, or ask the guide to set it up.' : 'Loading…'}</p>
      )}
      {!browsing && unplaced.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-line pt-2 text-sm">
          <span className="text-muted">In storage:</span>
          {unplaced.slice(0, 8).map((i) =>
            deleting === i.item_id ? (
              // Deleting can't be undone, so it takes a second, explicit click.
              <span key={i.item_id} className="inline-flex items-center gap-1 rounded-full bg-ink py-1 pl-2.5 pr-1">
                Delete {i.label} for good?
                <button type="button" onClick={() => void discard(i)} className="rounded-full bg-danger px-2 py-0.5 font-semibold text-surface">
                  Delete
                </button>
                <button type="button" onClick={() => setDeleting(null)} className="rounded-full px-2 py-0.5 hover:bg-raised">
                  Keep
                </button>
              </span>
            ) : (
              <span key={i.item_id} className="inline-flex items-center rounded-full bg-ink">
                <button type="button" onClick={() => void place(i)} className="rounded-l-full py-1 pl-2.5 pr-1.5 hover:bg-raised" title={`Place ${i.label} in this room`}>
                  + {i.label}
                  {i.owner_name ? <span className="text-muted"> · {i.owner_name}</span> : null}
                </button>
                <button type="button" onClick={() => setDeleting(i.item_id)} className="rounded-r-full py-1 pl-1 pr-2 text-muted hover:bg-raised hover:text-danger" aria-label={`Delete ${i.label} from the furniture`} title="Delete for good">
                  ×
                </button>
              </span>
            ),
          )}
          {/* The chips are a shortcut for a few pieces; the rest are cards in the furniture menu. */}
          {unplaced.length > 8 && onAddFurniture && (
            <button type="button" onClick={onAddFurniture} className="rounded-full px-2.5 py-1 text-muted hover:bg-ink hover:text-text">
              and {unplaced.length - 8} more
            </button>
          )}
        </div>
      )}
      {offered && !placeError && <p role="status" className="mt-1 text-sm text-leaf-deep">{offered}</p>}
      {placeError && <p role="alert" className="mt-1 text-sm text-danger">{placeError}</p>}
    </section>
  );
}

/**
 * Adding from inside the panel, for where there is no furniture menu (the phone's inline panel):
 * a photo of the person's own room, a photo of one piece, or the library. It stays mounted while
 * the panel is, so a photo's progress stays in view after "Back to the plan".
 */
function AddInline({ twin, room, open, place, onError, onRoomDone }: {
  twin: PropertyTwin;
  room: Room | undefined;
  /** The panel is in its "Add furniture" state. */
  open: boolean;
  place(item: InventoryItem): Promise<void>;
  onError(text: string): void;
  /** The room has been set up from a photo of the person's own. */
  onRoomDone(): void;
}) {
  // A photo: tap the piece, SAM cuts it out and builds it in 3D, and it is put in this room.
  // A photo of the person's own room: everything in it is built, and the server sets this room up with it.
  const { upload, uploadRoom, busy: uploading, error: uploadError, rows, dismiss } = useObjectUpload((itemId) => {
    void api().getInventoryItem(itemId).then(place, (err: unknown) => onError(err instanceof Error ? err.message : String(err)));
  }, onRoomDone);
  const [photo, setPhoto] = useState<File | null>(null);
  const sendPhoto = async (at: PhotoPoint | undefined) => {
    if (!photo) return;
    if (await upload(photo, at, at ? 'The piece you tapped' : 'Every piece in the photo')) setPhoto(null);
  };

  return (
    <>
      {open || rows.length > 0 ? (
        <div className="mb-2 space-y-2 border-b border-line pb-2" aria-label="Add from a photo">
          {open && !photo && room && (
            <OwnRoom roomName={roomName(twin, room.room_id)} busy={uploading} onSend={(file) => uploadRoom(file, twin.property_id, room.room_id)} />
          )}
          {open && !photo && (
            <>
              <p className="text-xs text-muted">Or one piece: take or import a photo of it, tap it, and it is cut out and built in 3D.</p>
              <PhotoButtons disabled={uploading} onPicked={(f) => void preparePhoto(f).then(setPhoto)} />
            </>
          )}
          {photo && <TapToPick file={photo} busy={uploading} onSubmit={(at) => void sendPhoto(at)} onCancel={() => setPhoto(null)} />}
          {uploadError && <p role="alert" className="text-sm text-danger">{uploadError}</p>}
          <JobRows rows={rows} dismiss={dismiss} />
        </div>
      ) : null}
      {open && !photo && <CatalogBrowser onAdded={place} />}
    </>
  );
}
