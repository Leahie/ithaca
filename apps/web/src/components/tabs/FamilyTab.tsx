'use client';

import type { BoardEntry, BoardKind, BoardPerson, PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Button, Notice, Spinner, ViewState, cn } from '@/components/ui';
import { IconPeople, IconPlus, IconTrash } from '@/components/ui/icons';
import { api } from '@/lib/api';
import { apiUserId } from '@/lib/api/user';
import { useBoard } from '@/lib/data/hooks';
import { roomName, roomNames } from '@/lib/rooms';
import { useFamily } from '@/lib/store/family';
import { useSession } from '@/lib/store/session';

/** The sections of a card, in the order they are read: what cannot be given up comes first. */
const SECTIONS: { kind: Exclude<BoardKind, 'room'>; title: string; add: string }[] = [
  { kind: 'must', title: 'Must-haves', add: 'Add a must-have' },
  { kind: 'like', title: 'Style and likes', add: 'Add a style or a like' },
  { kind: 'avoid', title: 'Avoid', add: 'Add something to avoid' },
  { kind: 'about', title: 'About', add: 'Add something about how they live' },
];

const SECTION_TITLE = 'text-[11px] font-semibold tracking-[0.08em] text-muted uppercase';
const FIELD = 'h-8 min-w-0 rounded-md border border-line bg-ink px-2.5 text-[13px] placeholder:text-faint focus:border-line-strong focus:outline-none';

const initials = (name: string) =>
  name
    .split(/\s+/)
    .map((w) => w[0] ?? '')
    .join('')
    .slice(0, 2)
    .toUpperCase();

/** How long ago, in words short enough for a line under an entry. */
function when(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (!Number.isFinite(minutes)) return '';
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return days === 1 ? 'yesterday' : `${days} days ago`;
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** One line of a card. Its trash can turns the line into a question, answered in place. */
function Entry({ entry, editable, onChanged }: { entry: BoardEntry; editable: boolean; onChanged(): void }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await api().deleteBoardEntry(entry.entry_id);
      onChanged();
    } catch (err) {
      setError(message(err));
      setBusy(false);
    }
  };

  if (confirm) {
    return (
      <li className="rounded-lg border border-coral px-3 py-2" data-testid="board-entry" data-entry-id={entry.entry_id}>
        <p className="text-sm">Delete “{entry.text}”?</p>
        <div className="mt-2 flex items-center gap-2">
          <Button size="sm" variant="danger" disabled={busy} onClick={() => void remove()}>
            {busy ? 'Deleting…' : 'Delete'}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirm(false)}>
            Keep
          </Button>
        </div>
        {error && <p className="mt-1.5 text-xs text-danger">{error}</p>}
      </li>
    );
  }
  return (
    <li className="flex items-start gap-2" data-testid="board-entry" data-entry-id={entry.entry_id} data-by={entry.by}>
      <div className="min-w-0 flex-1">
        <p className="text-[15px] leading-snug [overflow-wrap:anywhere]">{entry.text}</p>
        <p className="mt-0.5 text-xs text-faint" title={new Date(entry.updated_at).toLocaleString()}>
          {entry.by === 'guide' ? 'guide' : 'by hand'} · {when(entry.updated_at)}
        </p>
      </div>
      {editable && (
        <button type="button" aria-label={`Delete: ${entry.text}`} title="Delete" onClick={() => setConfirm(true)}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-faint hover:bg-ink hover:text-danger">
          <IconTrash width={14} height={14} />
        </button>
      )}
    </li>
  );
}

/** A one-line box that adds an entry. `children`: what stands before the box (the room picker). */
function AddLine({ label, placeholder, onAdd, children }: { label: string; placeholder: string; onAdd(text: string): Promise<void>; children?: ReactNode }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const clean = text.trim();
    if (!clean || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onAdd(clean);
      setText('');
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <form
        className="flex gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        {children}
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} aria-label={label} maxLength={200} className={cn(FIELD, 'flex-1')} />
        <Button size="sm" variant="ghost" square type="submit" aria-label="Add" title="Add" disabled={busy || !text.trim()}>
          <IconPlus width={14} height={14} />
        </Button>
      </form>
      {error && <p className="text-xs text-danger">{error}</p>}
    </>
  );
}

interface CardProps {
  person: BoardPerson;
  twin: PropertyTwin;
  /** In a family: the cards are people. Alone, the one card is "You". */
  family: boolean;
  /** A member: whether their phone is in the session now. Not set for the big screen's card. */
  here?: boolean;
  editable: boolean;
  onAdd(kind: BoardKind, text: string, roomId?: string): Promise<void>;
  onChanged(): void;
}

function Card({ person, twin, family, here, editable, onAdd, onChanged }: CardProps) {
  const entries = person.entries ?? [];
  const shared = !!person.shared;
  const currentRoom = useSession((s) => s.viewer.room_id);
  const rooms = useMemo(() => [...roomNames(twin)].sort((a, b) => a[1].localeCompare(b[1], 'en', { numeric: true })), [twin]);
  const [picked, setPicked] = useState<string | null>(null);
  // The wish is for the room picked here, else the room on screen: that is the one being talked about.
  const roomId = picked ?? (rooms.some(([id]) => id === currentRoom) ? currentRoom : null) ?? rooms[0]?.[0] ?? null;

  // Room wishes under each room's name, rooms in the order they were first wished for.
  const wishes = new Map<string, BoardEntry[]>();
  for (const e of entries) if (e.kind === 'room' && e.room_id) wishes.set(e.room_id, [...(wishes.get(e.room_id) ?? []), e]);

  const whose = shared ? 'you tell it' : `${person.name} tells it`;
  return (
    <article className="rounded-[var(--radius-card)] border border-line bg-raised" data-testid="board-card" data-person-id={person.person_id} data-entries={entries.length}>
      <header className="flex items-center gap-3 border-b border-line px-4 py-3">
        <span aria-hidden className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-ink font-display text-sm font-semibold">
          {shared && family ? <IconPeople width={18} height={18} /> : initials(person.name)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate font-display text-base font-semibold">{person.name}</div>
          {shared && <div className="truncate text-xs text-muted">{family ? 'Everyone, on the big screen' : 'What you want from a home'}</div>}
        </div>
        {here !== undefined && (
          <span className={cn('flex shrink-0 items-center gap-1.5 text-[13px]', here ? 'text-leaf-deep' : 'text-faint')} title={here ? 'Their phone is connected' : 'Their phone is not connected'}>
            <i aria-hidden className={cn('h-2 w-2 rounded-full', here ? 'bg-leaf' : 'bg-line-strong')} />
            {here ? 'here' : 'away'}
          </span>
        )}
      </header>
      <div className="space-y-4 p-4">
        {entries.length === 0 && (
          <p className="text-sm leading-5 text-muted">
            Empty so far. {shared ? 'The guide' : `${person.name}'s guide`} writes down what {whose} about rooms and taste{editable ? ', or add a line yourself.' : '.'}
          </p>
        )}
        {SECTIONS.map(({ kind, title, add }) => {
          const found = entries.filter((e) => e.kind === kind);
          if (!found.length && !editable) return null;
          return (
            <section key={kind} className="space-y-2" data-kind={kind}>
              <h3 className={SECTION_TITLE}>{title}</h3>
              {found.length > 0 && (
                <ul className="space-y-2">
                  {found.map((e) => (
                    <Entry key={e.entry_id} entry={e} editable={editable} onChanged={onChanged} />
                  ))}
                </ul>
              )}
              {editable && <AddLine label={`${add} for ${person.name}`} placeholder={add} onAdd={(text) => onAdd(kind, text)} />}
            </section>
          );
        })}
        {(wishes.size > 0 || (editable && rooms.length > 0)) && (
          <section className="space-y-2" data-kind="room">
            <h3 className={SECTION_TITLE}>Rooms</h3>
            {[...wishes].map(([id, found]) => (
              <div key={id} className="space-y-1.5" data-room-id={id}>
                <div className="text-[13px] font-semibold">{roomName(twin, id)}</div>
                <ul className="space-y-2 border-l-2 border-line pl-3">
                  {found.map((e) => (
                    <Entry key={e.entry_id} entry={e} editable={editable} onChanged={onChanged} />
                  ))}
                </ul>
              </div>
            ))}
            {editable && roomId && (
              <AddLine label={`Add a wish for a room, for ${person.name}`} placeholder="Add a wish for this room" onAdd={(text) => onAdd('room', text, roomId)}>
                <select value={roomId} onChange={(e) => setPicked(e.target.value)} aria-label="Room" className={cn(FIELD, 'max-w-[42%] shrink-0 pr-1')}>
                  {rooms.map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
                </select>
              </AddLine>
            )}
          </section>
        )}
      </div>
    </article>
  );
}

/**
 * Family: a board of what each person wants from a home. One card per person, the big screen's
 * own card first. Each person's guide writes on their card as it learns; anyone at this screen
 * can add or delete a line by hand. A paper page: it is not over the house (docs/style.md).
 */
export function FamilyTab({ twin }: { twin: PropertyTwin }) {
  const household = useFamily((s) => s.household);
  const householdId = household?.household_id ?? null;
  const board = useBoard(twin.property_id, householdId);
  const connected = useSession((s) => s.members);
  const dispatch = useSession((s) => s.dispatch);
  const people = board.data?.people ?? [];

  // Someone joined since the board was loaded: load it again, so they have their card.
  const known = new Set(people.map((p) => p.person_id));
  const unknown = board.data ? Object.keys(connected).filter((id) => !known.has(id)).join() : '';
  const reload = board.reload;
  useEffect(() => {
    if (unknown) reload();
  }, [unknown, reload]);

  if (board.error && !board.data) {
    return (
      <ViewState>
        <Notice tone="error" title="Could not load the family board">
          {board.error.message}
        </Notice>
      </ViewState>
    );
  }
  if (!board.data) {
    return (
      <ViewState>
        <Spinner label="Loading the family board…" />
      </ViewState>
    );
  }

  const family = !!board.data.household_id;
  const notes = board.data.notes ?? [];
  // A member may change only their own card; the big screen any card.
  const me = apiUserId();
  // Every screen in the session loads the board again, this one included (SessionProvider).
  const changed = (personId: string) => dispatch('board.changed', { person_id: personId });
  const add = async (personId: string, kind: BoardKind, text: string, roomId?: string) => {
    await api().addBoardEntry({ person_id: personId, kind, text, room_id: roomId ?? null, property_id: roomId ? twin.property_id : null, household_id: householdId });
    changed(personId);
  };

  return (
    <div className="h-full overflow-y-auto p-5" data-testid="family-board" data-people={people.length}>
      <div className="mx-auto max-w-6xl">
        <div className="mb-4">
          <h1 className="text-lg font-semibold">Family board</h1>
          <p className="text-sm text-muted">What each person wants from a home, kept up to date by their own guide.</p>
        </div>

        {people.length <= 1 && (
          <div className="mb-4">
            <Notice title={family ? 'Nobody has joined yet' : 'No family yet'}>
              Each person who scans the family code gets a card here. The phone button at the top right shows the code.
            </Notice>
          </div>
        )}

        <div className="grid items-start gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {people.map((p) => (
            <Card
              key={p.person_id}
              person={p}
              twin={twin}
              family={family}
              here={p.shared ? undefined : (connected[p.person_id]?.connected ?? false)}
              editable={!me.startsWith('m-') || p.person_id === me}
              onAdd={(kind, text, roomId) => add(p.person_id, kind, text, roomId)}
              onChanged={() => changed(p.person_id)}
            />
          ))}
        </div>

        {notes.length > 0 && (
          <section className="mt-6" data-testid="board-notes">
            <h2 className={SECTION_TITLE}>Notes about this house</h2>
            <ul className="mt-2 space-y-1.5">
              {notes.map((n) => (
                <li key={n.note_id} className="rounded-lg border border-line bg-raised px-3 py-2 text-sm leading-5">
                  <span className="font-semibold">{n.name ? `${n.name}'s guide` : 'The guide'}</span>
                  {n.room_id && <span className="text-muted"> · {roomName(twin, n.room_id)}</span>}: {n.text} <span className="text-xs whitespace-nowrap text-faint">{when(n.created_at)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}
