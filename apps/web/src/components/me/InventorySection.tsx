'use client';

import type { InventoryItem, JobProgressPayload, PhotoPoint } from '@ithaca/schema';
import { useEffect, useRef, useState } from 'react';
import { Badge, Button, Notice, Spinner, Toggle, cn } from '@/components/ui';
import { IconTrash, IconUpload } from '@/components/ui/icons';
import { ApiError, api } from '@/lib/api';
import { useInventory } from '@/lib/data/hooks';
import { invalidate } from '@/lib/data/resource';
import { useSession } from '@/lib/store/session';
import { ItemPreview } from './ItemPreview';

const FINISHED = new Set(['done', 'failed']);

/** Of two reports on one job, the one further along: finished beats running, then the higher percent. */
function furthest(a: JobProgressPayload | undefined, b: JobProgressPayload | undefined): JobProgressPayload | undefined {
  if (!a || !b) return a ?? b;
  if (FINISHED.has(a.status) !== FINISHED.has(b.status)) return FINISHED.has(a.status) ? a : b;
  return b.pct > a.pct ? b : a;
}

interface PendingJob {
  job_id: string;
  filename: string;
  started: number;
}

/**
 * Upload an image -> object pipeline job -> a new inventory item. Shared by /me, the phone and the
 * Arrange panel. `at`: the spot tapped on the photo, so only that thing is built. `onItem` gets the
 * first new item of each job that finishes (to put it in the room).
 */
export function useObjectUpload(onItem?: (itemId: string) => void) {
  const jobs = useSession((s) => s.jobs);
  const dispatch = useSession((s) => s.dispatch);
  const [pending, setPending] = useState<PendingJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const handled = useRef(new Set<string>());
  const latest = useRef(onItem);
  latest.current = onItem;

  const upload = async (file: File, at?: PhotoPoint, label?: string) => {
    setError(null);
    setBusy(true);
    try {
      const up = await api().upload(file, file.name || 'capture.jpg');
      const job = await api().ingestObject(up.upload_id, at);
      dispatch('capture.upload', { upload_id: up.upload_id });
      setPending((p) => [...p, { job_id: job.job_id, filename: label ?? (file.name || 'photo'), started: Date.now() }]);
      return true;
    } catch (err) {
      setError(err instanceof ApiError && err.notConfigured ? `The object pipeline is not configured: ${err.message}` : err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setBusy(false);
    }
  };

  // Progress arrives as job.progress events, but only to sessions of the user who started the job:
  // a family member's phone, or a page whose session dropped, hears nothing. So open jobs are also
  // asked for, and whichever answer is further along counts.
  const [polled, setPolled] = useState<Record<string, JobProgressPayload>>({});
  const progressOf = (jobId: string): JobProgressPayload | undefined => furthest(jobs[jobId] as JobProgressPayload | undefined, polled[jobId]);
  const open = pending.filter((p) => !FINISHED.has(progressOf(p.job_id)?.status ?? 'queued')).map((p) => p.job_id);
  const openKey = open.join(',');
  useEffect(() => {
    if (!openKey) return;
    const ids = openKey.split(',');
    const t = setInterval(() => {
      for (const id of ids) {
        api().getJob(id).then((job) => setPolled((s) => ({ ...s, [id]: job })), () => {});
      }
    }, 2500);
    return () => clearInterval(t);
  }, [openKey]);

  useEffect(() => {
    for (const p of pending) {
      const progress = progressOf(p.job_id);
      const itemId = progress?.status === 'done' ? progress.result_ref?.split(',')[0] : undefined;
      if (!itemId || handled.current.has(p.job_id)) continue;
      handled.current.add(p.job_id);
      invalidate('inventory');
      latest.current?.(itemId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs, polled, pending]);

  const rows = pending.map((p) => ({ ...p, progress: progressOf(p.job_id) }));
  const dismiss = (jobId: string) => setPending((p) => p.filter((j) => j.job_id !== jobId));
  return { upload, busy, error, rows, dismiss };
}

export function JobRows({ rows, dismiss }: Pick<ReturnType<typeof useObjectUpload>, 'rows' | 'dismiss'>) {
  if (!rows.length) return null;
  return (
    <ul className="space-y-2" data-testid="jobs">
      {rows.map((r) => {
        const status = r.progress?.status ?? 'queued';
        const pct = status === 'done' ? 100 : (r.progress?.pct ?? 0);
        return (
          <li key={r.job_id} data-status={status} className="rounded-lg border border-line bg-surface px-3 py-2.5 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="min-w-0 truncate">{r.filename}</span>
              <span className="flex shrink-0 items-center gap-2">
                <Badge tone={status === 'done' ? 'leaf' : status === 'failed' ? 'danger' : 'sun'}>{status === 'done' ? 'added to inventory' : status === 'failed' ? 'failed' : `${status} ${Math.round(pct)}%`}</Badge>
                {(status === 'done' || status === 'failed') && (
                  <button type="button" onClick={() => dismiss(r.job_id)} className="text-xs text-faint hover:text-text">
                    dismiss
                  </button>
                )}
              </span>
            </div>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-line">
              <div className={cn('h-full rounded-full transition-[width] duration-300', status === 'failed' ? 'bg-danger' : 'bg-sun')} style={{ width: `${pct}%` }} />
            </div>
            {status === 'failed' && (
              <p className="mt-1.5 text-xs text-danger">
                {r.progress?.result_ref ? `It could not be made: ${r.progress.result_ref}.` : 'The pipeline could not turn this image into a correctly scaled 3D model.'}
                {!r.progress?.result_ref?.includes('not configured') && ' Try a clearer photo, or a product page that lists dimensions.'}
              </p>
            )}
            {status === 'running' && <p className="mt-1.5 text-xs text-muted">Cutting it out with SAM and building the 3D model. This takes about half a minute.</p>}
          </li>
        );
      })}
    </ul>
  );
}

function SourcePhoto({ item }: { item: InventoryItem }) {
  const [mask, setMask] = useState(true);
  return (
    <div>
      <div className="relative overflow-hidden rounded-lg border border-line bg-ink">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={item.source_image_url} alt={`Source photo of ${item.label}`} className="block w-full" />
        {mask && item.mask_url && (
          // The segmentation mask, tinted and laid over the photo. Luminance masking accepts
          // both white-on-black and white-on-transparent masks.
          <div
            data-testid="mask-overlay"
            className="pointer-events-none absolute inset-0 bg-sun/55 mix-blend-screen"
            style={{ maskImage: `url("${item.mask_url}")`, WebkitMaskImage: `url("${item.mask_url}")`, maskSize: '100% 100%', WebkitMaskSize: '100% 100%', maskMode: 'luminance' }}
          />
        )}
      </div>
      <div className="mt-2">
        <Toggle label="Show what was segmented" checked={mask && !!item.mask_url} disabled={!item.mask_url} onChange={setMask} />
        {!item.mask_url && <p className="mt-1 text-xs text-faint">This item has no segmentation mask.</p>}
      </div>
    </div>
  );
}

export function InventorySection() {
  const inventory = useInventory();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const { upload, busy, error, rows, dismiss } = useObjectUpload();

  const items = inventory.data ?? [];
  const selected = items.find((i) => i.item_id === selectedId) ?? null;
  useEffect(() => setConfirm(false), [selectedId]);

  const remove = async (item: InventoryItem) => {
    setDeleteError(null);
    try {
      await api().deleteInventoryItem(item.item_id);
      setSelectedId(null);
      invalidate('inventory');
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]" data-testid="inventory" data-count={items.length}>
      <div className="space-y-4">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            for (const f of e.dataTransfer.files) if (f.type.startsWith('image/')) void upload(f);
          }}
          className={cn('flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed p-4', dragging ? 'border-sun bg-sun/10' : 'border-line-strong bg-surface')}
        >
          <div className="text-sm">
            <div className="font-medium">Add furniture</div>
            <div className="text-muted">Drop a product screenshot, a product photo, or a photo of your own room.</div>
          </div>
          <input
            ref={input}
            type="file"
            accept="image/*"
            multiple
            className="sr-only"
            data-testid="upload-input"
            onChange={(e) => {
              for (const f of e.target.files ?? []) void upload(f);
              e.target.value = '';
            }}
          />
          <Button variant="primary" disabled={busy} onClick={() => input.current?.click()}>
            <IconUpload /> {busy ? 'Uploading…' : 'Choose images'}
          </Button>
        </div>
        {error && (
          <Notice tone="error" title="Upload failed">
            {error}
          </Notice>
        )}
        <JobRows rows={rows} dismiss={dismiss} />

        {inventory.error ? (
          <Notice tone="error" title="Could not load the inventory">
            {inventory.error.message}
          </Notice>
        ) : !inventory.data ? (
          <Spinner label="Loading your furniture…" />
        ) : items.length === 0 ? (
          <Notice title="No furniture yet">Add a piece above. The agent uses your inventory when it lays out a room, in this home and in the next one.</Notice>
        ) : (
          <ul className="grid grid-cols-2 gap-3 md:grid-cols-3">
            {items.map((item) => (
              <li key={item.item_id}>
                <button
                  type="button"
                  data-testid="inventory-item"
                  data-item-id={item.item_id}
                  aria-pressed={item.item_id === selectedId}
                  onClick={() => setSelectedId(item.item_id)}
                  className={cn('block w-full overflow-hidden rounded-xl border bg-surface text-left transition-colors', item.item_id === selectedId ? 'border-sun' : 'border-line hover:border-line-strong')}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={item.source_image_url} alt="" className="aspect-[4/3] w-full bg-ink object-cover" />
                  <div className="p-3">
                    <div className="truncate text-sm font-medium">{item.label}</div>
                    <div className="mt-0.5 truncate text-xs text-muted">
                      {item.item_class.replace(/_/g, ' ')} · {item.dims_m.w.toFixed(2)} × {item.dims_m.d.toFixed(2)} × {item.dims_m.h.toFixed(2)} m
                    </div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <aside className="space-y-4">
        {!selected ? (
          <Notice title="Pick an item">Select a piece to see its 3D model, what was segmented from the photo, and what the agent knows about it.</Notice>
        ) : (
          <div className="space-y-4 rounded-xl border border-line bg-surface p-4" data-testid="item-detail">
            <div>
              <h2 className="text-base font-semibold">{selected.label}</h2>
              <div className="mt-1 flex flex-wrap gap-1.5">
                <Badge tone="sky">{selected.item_class.replace(/_/g, ' ')}</Badge>
                {[selected.attributes.style, selected.attributes.color, selected.attributes.material].filter(Boolean).map((a) => (
                  <Badge key={a}>{a}</Badge>
                ))}
              </div>
            </div>
            <ItemPreview item={selected} />
            <dl className="grid grid-cols-3 gap-2 text-center text-xs">
              {(
                [
                  ['Width', selected.dims_m.w],
                  ['Depth', selected.dims_m.d],
                  ['Height', selected.dims_m.h],
                ] as const
              ).map(([k, v]) => (
                <div key={k} className="rounded-md border border-line py-2">
                  <dt className="text-muted">{k}</dt>
                  <dd className="mt-0.5 text-sm font-semibold">{v.toFixed(2)} m</dd>
                </div>
              ))}
            </dl>
            <SourcePhoto item={selected} />
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted">{selected.price != null ? `$${selected.price.toLocaleString('en-US')}` : 'No price recorded'}</span>
              {selected.product_url && (
                <a href={selected.product_url} target="_blank" rel="noreferrer" className="text-sky-deep hover:underline">
                  Product page
                </a>
              )}
            </div>
            {deleteError && <p className="text-xs text-danger">{deleteError}</p>}
            {confirm ? (
              <div className="flex items-center gap-2">
                <Button variant="danger" className="flex-1" onClick={() => void remove(selected)}>
                  Delete for good
                </Button>
                <Button variant="ghost" onClick={() => setConfirm(false)}>
                  Keep
                </Button>
              </div>
            ) : (
              <Button variant="ghost" className="w-full text-danger hover:text-danger" onClick={() => setConfirm(true)}>
                <IconTrash /> Remove from inventory
              </Button>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
