'use client';

import type { PropertyTwin, RoomScan, ScanThing } from '@ithaca/schema';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { Badge, Button, Notice, Spinner, cn } from '@/components/ui';
import { IconBed, IconCheck, IconClose } from '@/components/ui/icons';
import { api } from '@/lib/api';
import { useTwin } from '@/lib/data/hooks';
import { invalidate, useResource } from '@/lib/data/resource';
import { houseHref } from '@/lib/lastHouse';
import { useJobProgress } from '@/lib/library';
import { roomNames } from '@/lib/rooms';
import { ItemPreview } from './ItemPreview';

const GROUPS: [string, string][] = [
  ['furniture', 'Furniture'],
  ['floor', 'On the floor'],
  ['bed', 'On the bed'],
  ['desk', 'On the desk'],
  ['shelf', 'On the shelf'],
  ['wall', 'On the walls'],
  ['window', 'At the windows'],
];

const words = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** The house's bedrooms, by name; every room when none is called a bedroom. */
function bedrooms(twin: PropertyTwin): { id: string; name: string }[] {
  const names = roomNames(twin);
  const beds = twin.rooms.filter((r) => /bed/i.test(r.label));
  return (beds.length ? beds : twin.rooms)
    .map((r) => ({ id: r.room_id, name: names.get(r.room_id) ?? r.label }))
    .sort((a, b) => a.name.localeCompare(b.name, 'en', { numeric: true }));
}

/**
 * The person's own room, scanned from one photo: every piece of furniture, the bedding, what is on
 * the desk and the shelf, what hangs on the walls and at the windows, each modelled in 3D. Click
 * the photo to open it; then put all of it in a bedroom of the house, or pick things on the photo.
 */
export function MyRoomScan({ houseId }: { houseId: string }) {
  const scans = useResource('scans', () => api().listScans());
  const [open, setOpen] = useState(false);
  const scan = scans.data?.[0];
  if (scans.error || !scan) return null; // fixture mode, or no API: nothing to show
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-testid="my-room-card"
        className="group flex w-full items-center gap-4 rounded-xl border border-line bg-surface p-3 text-left transition-colors hover:border-sun"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={api().assetUrl(scan.photo_url)} alt={`Photo of ${scan.name.toLowerCase()}`} className="h-24 w-32 shrink-0 rounded-lg object-cover transition-transform group-hover:scale-[1.02]" />
        <span className="min-w-0">
          <span className="block font-medium">{scan.name}</span>
          <span className="mt-0.5 block text-sm text-muted">
            {scan.things.length} things scanned from your photo, each in 3D: the furniture, the bedding, what is on the desk and the shelf, the map, the poster and the curtains.
          </span>
          <span className="mt-1.5 inline-flex items-center gap-1.5 text-sm font-semibold text-text">
            <IconBed width={16} height={16} /> Click to put it all in a bedroom
          </span>
        </span>
      </button>
    );
  }
  return <ScanView scan={scan} houseId={houseId} onClose={() => setOpen(false)} />;
}

function ScanView({ scan, houseId, onClose }: { scan: RoomScan; houseId: string; onClose(): void }) {
  const twin = useTwin(houseId || null);
  const rooms = useMemo(() => (twin.data ? bedrooms(twin.data) : []), [twin.data]);
  const [roomId, setRoomId] = useState<string | null>(null);
  const room = rooms.find((r) => r.id === roomId) ?? rooms[0] ?? null;
  const byKey = useMemo(() => new Map(scan.things.map((t) => [t.key, t])), [scan]);

  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const focused = focus ? (byKey.get(focus) ?? null) : null;

  const [job, setJob] = useState<{ id: string; room: string; count: number } | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const progress = useJobProgress(job?.id ?? null);
  const status = progress?.status ?? (job ? 'queued' : null);

  useEffect(() => {
    if (status !== 'done' && status !== 'failed') return;
    invalidate('inventory');
    invalidate(`furniture:${houseId}`);
  }, [status, houseId]);

  /** Picking a thing picks what it stands on; dropping a piece drops what stands on it. */
  const toggle = (key: string) => {
    setFocus(key);
    setPicked((now) => {
      const next = new Set(now);
      if (next.has(key)) {
        next.delete(key);
        for (const t of scan.things) if (t.rests_on === key) next.delete(t.key);
      } else {
        next.add(key);
        for (let up = byKey.get(key)?.rests_on; up; up = byKey.get(up)?.rests_on) next.add(up);
      }
      return next;
    });
  };

  const furnish = async () => {
    if (!room) return;
    setError(null);
    setSaid(null);
    setSending(true);
    try {
      const keys = picked.size ? [...picked] : undefined;
      const ref = await api().furnishFromScan(houseId, scan.scan_id, room.id, keys);
      setJob({ id: ref.job_id, room: room.name, count: keys?.length ?? scan.things.length });
    } catch (err) {
      setError(words(err));
    } finally {
      setSending(false);
    }
  };

  const one = async (thing: ScanThing, where: 'room' | 'storage') => {
    setError(null);
    setSaid(null);
    setSending(true);
    try {
      if (where === 'room' && room) {
        await api().putScanThing(houseId, scan.scan_id, thing.key, room.id);
        invalidate(`furniture:${houseId}`);
        setSaid(`${thing.label} is in ${room.name} now, with what was already there.`);
      } else {
        await api().addScanThing(scan.scan_id, thing.key);
        setSaid(`${thing.label} is in your furniture.`);
      }
      invalidate('inventory');
    } catch (err) {
      setError(words(err));
    } finally {
      setSending(false);
    }
  };

  // Small things last, so they sit over the big ones and can be clicked.
  const spots = useMemo(() => [...scan.things].sort((a, b) => (b.box[2] - b.box[0]) * (b.box[3] - b.box[1]) - (a.box[2] - a.box[0]) * (a.box[3] - a.box[1])), [scan]);
  const running = status === 'queued' || status === 'running';
  const pct = status === 'done' ? 100 : (progress?.pct ?? 0);

  return (
    <section className="rounded-xl border border-line bg-surface p-4" data-testid="my-room-scan" aria-label={scan.name}>
      <header className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">{scan.name}</h2>
          <p className="mt-0.5 text-sm text-muted">
            {scan.blurb} Click things on the photo to pick some of them, or put all {scan.things.length} in a bedroom.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close your room">
          <IconClose /> Close
        </Button>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-4">
          <div className="relative overflow-hidden rounded-lg border border-line bg-ink" style={{ aspectRatio: `${scan.width} / ${scan.height}` }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={api().assetUrl(scan.photo_url)} alt={scan.name} className="absolute inset-0 h-full w-full" draggable={false} />
            {spots.map((t) => {
              const on = picked.has(t.key);
              const lit = on || hover === t.key || focus === t.key;
              return (
                <button
                  key={t.key}
                  type="button"
                  title={t.label}
                  aria-label={t.label}
                  aria-pressed={on}
                  data-testid="scan-spot"
                  onClick={() => toggle(t.key)}
                  onMouseEnter={() => setHover(t.key)}
                  onMouseLeave={() => setHover((h) => (h === t.key ? null : h))}
                  className={cn(
                    'absolute rounded-md border-2 transition-colors',
                    on ? 'border-sun bg-sun/20' : lit ? 'border-white bg-white/10' : 'border-white/0 hover:border-white/70',
                  )}
                  style={{ left: `${t.box[0] * 100}%`, top: `${t.box[1] * 100}%`, width: `${(t.box[2] - t.box[0]) * 100}%`, height: `${(t.box[3] - t.box[1]) * 100}%` }}
                >
                  {lit && (
                    <span className="pointer-events-none absolute top-0 left-0 z-10 -translate-y-full rounded bg-black/75 px-1.5 py-0.5 text-[11px] font-semibold whitespace-nowrap text-white">
                      {t.label}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="space-y-3">
            {GROUPS.map(([group, title]) => {
              const things = scan.things.filter((t) => t.group === group);
              if (!things.length) return null;
              return (
                <div key={group}>
                  <div className="mb-1.5 text-[11px] font-semibold tracking-[0.08em] text-muted uppercase">{title}</div>
                  <div className="flex flex-wrap gap-1.5">
                    {things.map((t) => (
                      <button
                        key={t.key}
                        type="button"
                        aria-pressed={picked.has(t.key)}
                        onClick={() => toggle(t.key)}
                        onMouseEnter={() => setHover(t.key)}
                        onMouseLeave={() => setHover((h) => (h === t.key ? null : h))}
                        className={cn(
                          'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors',
                          picked.has(t.key) ? 'border-sun bg-sun text-main' : focus === t.key ? 'border-line-strong bg-raised' : 'border-line bg-raised hover:border-line-strong',
                        )}
                      >
                        {picked.has(t.key) && <IconCheck width={12} height={12} />}
                        {t.label}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <aside className="space-y-4">
          <div className="space-y-3 rounded-xl border border-line bg-raised p-3">
            {twin.error ? (
              <Notice tone="error" title="Could not load the house">
                {twin.error.message}
              </Notice>
            ) : !twin.data ? (
              <Spinner label="Loading the house…" />
            ) : !room ? (
              <Notice title="No rooms">This house has no rooms to put your things in.</Notice>
            ) : (
              <>
                <label className="block text-sm">
                  <span className="text-muted">Put it in</span>
                  <select
                    value={room.id}
                    onChange={(e) => setRoomId(e.target.value)}
                    className="mt-1 block h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm font-semibold"
                    data-testid="scan-room"
                  >
                    {rooms.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name}
                      </option>
                    ))}
                  </select>
                </label>
                <Button variant="primary" className="w-full" disabled={sending || running} onClick={() => void furnish()} data-testid="scan-furnish">
                  {sending || running ? <Spinner /> : null}
                  {picked.size ? `Put the ${picked.size} picked in ${room.name}` : `Put everything in ${room.name}`}
                </Button>
                <p className="text-xs leading-snug text-muted">What stands in {room.name} now goes back to your storage. In a small room the least important pieces wait in storage too.</p>
                {picked.size > 0 && (
                  <div className="flex gap-2">
                    <Button variant="ghost" size="sm" onClick={() => setPicked(new Set(scan.things.map((t) => t.key)))}>
                      Pick all
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setPicked(new Set())}>
                      Clear
                    </Button>
                  </div>
                )}
              </>
            )}

            {job && (
              <div data-status={status}>
                <div className="flex items-center justify-between text-xs">
                  <span className="truncate text-muted">{status === 'done' ? `Done: ${job.room}` : status === 'failed' ? 'It did not work' : (progress?.note ?? `Setting up ${job.room}`)}</span>
                  <span className="font-semibold">{Math.round(pct)}%</span>
                </div>
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-line">
                  <div className={cn('h-full rounded-full transition-[width] duration-300', status === 'failed' ? 'bg-danger' : 'bg-sun')} style={{ width: `${pct}%` }} />
                </div>
                {status === 'done' && (
                  <p className="mt-2 text-sm">
                    Your room is set up in {job.room}.{' '}
                    <Link href={houseHref(houseId)} className="font-semibold text-sky-deep hover:underline">
                      See it in the house
                    </Link>
                  </p>
                )}
                {status === 'failed' && <p className="mt-1.5 text-xs text-danger">{progress?.result_ref ?? 'The room could not be set up.'}</p>}
              </div>
            )}
            {said && <p className="text-sm text-text">{said}</p>}
            {error && <p className="text-xs text-danger">{error}</p>}
          </div>

          {focused ? (
            <div className="space-y-3 rounded-xl border border-line bg-raised p-3" data-testid="scan-thing">
              <div>
                <h3 className="font-semibold">{focused.label}</h3>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  <Badge tone="sky">{GROUPS.find(([g]) => g === focused.group)?.[1] ?? focused.group}</Badge>
                  {focused.rests_on && <Badge>on the {byKey.get(focused.rests_on)?.label.toLowerCase()}</Badge>}
                </div>
              </div>
              <ItemPreview item={{ item_id: `${scan.scan_id}/${focused.key}`, dims_m: focused.dims_m, glb_url: api().assetUrl(focused.model_url), scale: 1 }} />
              <p className="text-xs text-muted">
                {focused.dims_m.w.toFixed(2)} × {focused.dims_m.d.toFixed(2)} × {focused.dims_m.h.toFixed(2)} m{focused.note ? `. ${focused.note}` : ''}
              </p>
              <div className="flex gap-2">
                <Button variant="subtle" size="sm" className="flex-1" disabled={sending || !room} onClick={() => void one(focused, 'room')}>
                  Add to {room?.name ?? 'the room'}
                </Button>
                <Button variant="ghost" size="sm" disabled={sending} onClick={() => void one(focused, 'storage')}>
                  Add to storage
                </Button>
              </div>
            </div>
          ) : (
            <Notice title="Click anything in the photo">See its 3D model, then put just that in a room, or pick several and set the room up with them.</Notice>
          )}

          {!!scan.finishes?.length && (
            <div className="text-xs text-muted">
              <div className="mb-1 font-semibold tracking-[0.08em] uppercase">The room itself</div>
              <div className="flex flex-wrap gap-1.5">
                {(scan.finishes ?? []).map((f) => (
                  <Badge key={f}>{f}</Badge>
                ))}
              </div>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
