import type { Household, InventoryItem, JobProgressPayload, LayoutVariant, NoiseReport, NoiseScenario, Opening, PropertyTwin, SunReport } from '@ithaca/schema';
import { config } from '@/lib/config';
import { ApiError, type ApiClient } from './types';

/**
 * Fixture-mode API (A6). Reads come from the repo's `fixtures/` tree, served by the
 * `/fixtures/[...path]` route. Writes go to an overlay in localStorage so that every tab of
 * the same browser (desktop, phone remote, /me) sees them. No backend is involved.
 */

const OVERLAY_KEY = `ithaca.fixture.${config.userId}`;

interface Overlay {
  openings: Record<string, Opening[]>;
  layouts: Record<string, Partial<Pick<LayoutVariant, 'status' | 'feedback' | 'placements'>>>;
  addedItems: InventoryItem[];
  deletedItems: string[];
  memory: Record<string, string | null>; // null = deleted
  uploads: Record<string, string>; // upload_id -> preview URL
}

const empty = (): Overlay => ({ openings: {}, layouts: {}, addedItems: [], deletedItems: [], memory: {}, uploads: {} });

function readOverlay(): Overlay {
  try {
    const raw = window.localStorage.getItem(OVERLAY_KEY);
    return raw ? { ...empty(), ...(JSON.parse(raw) as Partial<Overlay>) } : empty();
  } catch {
    return empty();
  }
}

function writeOverlay(next: Overlay): void {
  try {
    window.localStorage.setItem(OVERLAY_KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable: the change lives for this call only */
  }
}

function patchOverlay(fn: (o: Overlay) => void): void {
  const o = readOverlay();
  fn(o);
  writeOverlay(o);
}

export function resetFixtureOverlay(): void {
  try {
    window.localStorage.removeItem(OVERLAY_KEY);
  } catch {
    /* nothing to reset */
  }
}

async function fixture<T>(path: string, as: 'json' | 'text' = 'json'): Promise<T> {
  const res = await fetch(`/fixtures/${path}`);
  if (!res.ok) throw new ApiError(res.status === 404 ? `No fixture at fixtures/${path}` : res.statusText, res.status);
  return (as === 'json' ? await res.json() : await res.text()) as T;
}

/** Job progress produced by fixture-mode jobs. The local transport relays these as `job.progress`. */
type JobListener = (payload: JobProgressPayload) => void;
const jobListeners = new Set<JobListener>();
export function onFixtureJob(cb: JobListener): () => void {
  jobListeners.add(cb);
  return () => jobListeners.delete(cb);
}
const emitJob = (payload: JobProgressPayload) => jobListeners.forEach((cb) => cb(payload));

export function noiseKey(s: NoiseScenario): string {
  return `${s.glazing ?? 'asbuilt'}|${s.add_carpet ? 1 : 0}|${s.include_furniture === false ? 0 : 1}`;
}

const uuid = () => crypto.randomUUID();

const HOUSEHOLDS_KEY = 'ithaca.fixture.households';
function loadHouseholds(): Record<string, Household> {
  try {
    return JSON.parse(window.localStorage.getItem(HOUSEHOLDS_KEY) ?? '{}') as Record<string, Household>;
  } catch {
    return {};
  }
}
function saveHouseholds(all: Record<string, Household>): void {
  try {
    window.localStorage.setItem(HOUSEHOLDS_KEY, JSON.stringify(all));
  } catch {
    /* fixture households last for this page load only */
  }
}

async function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

/**
 * In a twin fixture, asset paths are keys relative to `fixtures/` (e.g. "blobs/<pid>/interior.glb").
 * The API will return full URLs; here the key becomes a URL on the fixtures route.
 */
export const fixtureAssetUrl = (key: string | null | undefined): string | null => (!key ? null : /^(https?:)?\/\//.test(key) || key.startsWith('/') ? key : `/fixtures/${key}`);

function resolveAssets(twin: PropertyTwin): PropertyTwin {
  const a = twin.assets;
  return {
    ...twin,
    assets: {
      interior_glb: fixtureAssetUrl(a.interior_glb),
      context_glb: fixtureAssetUrl(a.context_glb),
      ortho: fixtureAssetUrl(a.ortho),
      obliques: (a.obliques ?? []).flatMap((o) => fixtureAssetUrl(o) ?? []),
      splat: fixtureAssetUrl(a.splat),
      splat_to_interior: a.splat_to_interior ?? null,
    },
  };
}

export function createFixtureApi(): ApiClient {
  const twins = new Map<string, Promise<PropertyTwin>>();
  const baseTwin = (pid: string) => {
    if (!twins.has(pid)) twins.set(pid, fixture<PropertyTwin>(`twin/${encodeURIComponent(pid)}.json`).then(resolveAssets));
    return twins.get(pid)!;
  };
  const baseLayouts = async (pid: string): Promise<LayoutVariant[]> => {
    try {
      return await fixture<LayoutVariant[]>(`layouts/${encodeURIComponent(pid)}.json`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return [];
      throw err;
    }
  };
  const withOverlay = (l: LayoutVariant, o: Overlay): LayoutVariant => ({ ...l, ...o.layouts[l.layout_id] });

  const inventory = async (): Promise<InventoryItem[]> => {
    const o = readOverlay();
    const base = await fixture<InventoryItem[]>(`inventory/${encodeURIComponent(config.userId)}.json`).catch(() => [] as InventoryItem[]);
    return [...base, ...o.addedItems].filter((i) => !o.deletedItems.includes(i.item_id));
  };

  const findLayout = async (id: string): Promise<LayoutVariant> => {
    // Layout fixtures are per property; search the ones this session has loaded, then the default.
    const pids = new Set([...twins.keys(), config.defaultPropertyId].filter(Boolean));
    for (const pid of pids) {
      const hit = (await baseLayouts(pid)).find((l) => l.layout_id === id);
      if (hit) return withOverlay(hit, readOverlay());
    }
    throw new ApiError(`No layout ${id} in fixtures`, 404);
  };

  const memoryIndex = async (): Promise<string[]> => {
    const base = await fixture<string[]>(`memory/${encodeURIComponent(config.userId)}/_index`).catch(() => [] as string[]);
    const o = readOverlay();
    const all = new Set(base);
    for (const [p, text] of Object.entries(o.memory)) {
      if (text === null) all.delete(p);
      else all.add(p);
    }
    return [...all].sort();
  };

  return {
    mode: 'fixture',

    async getTwin(pid) {
      const twin = await baseTwin(pid);
      const openings = readOverlay().openings[pid];
      return openings ? { ...twin, openings, quality: { ...twin.quality, openings: 'annotated' } } : twin;
    },
    async putOpenings(pid, openings) {
      patchOverlay((o) => (o.openings[pid] = openings));
      return openings;
    },
    async getSun(pid, roomId, start, end) {
      const year = await fixture<SunReport>(`reports/${encodeURIComponent(pid)}/sun/${encodeURIComponent(roomId)}.json`);
      // Fixture reports cover one year. Match by month and day so any year works.
      const md = (d: string) => d.slice(5);
      const wraps = md(start) > md(end);
      const daily = year.daily.filter((d) => (wraps ? md(d.date) >= md(start) || md(d.date) <= md(end) : md(d.date) >= md(start) && md(d.date) <= md(end)));
      return { ...year, start, end, daily };
    },
    async getNoise(pid, roomId, scenario) {
      const all = await fixture<Record<string, NoiseReport>>(`reports/${encodeURIComponent(pid)}/noise/${encodeURIComponent(roomId)}.json`);
      const hit = all[noiseKey(scenario)];
      if (!hit) throw new ApiError(`No noise fixture for scenario ${noiseKey(scenario)}`, 404);
      return hit;
    },
    getArea: (pid) => fixture(`reports/${encodeURIComponent(pid)}/area.json`),
    async ingestProperty() {
      throw new ApiError('Ingestion is not available in fixture mode', 501, true);
    },

    async createSession(opts) {
      const session = { session_id: `fx-${uuid().slice(0, 8)}`, pair_token: uuid().slice(0, 8), expires_at: new Date(Date.now() + 10 * 60_000).toISOString() };
      if (opts?.householdId) saveHouseholds({ ...loadHouseholds(), [opts.householdId]: { ...loadHouseholds()[opts.householdId]!, active_session_id: session.session_id } });
      return session;
    },
    // Households in fixture mode live in this browser's localStorage: tabs of one browser can
    // try the family flow, real phones need the API.
    async createHousehold(name) {
      const h: Household = { household_id: `fx-h-${uuid().slice(0, 8)}`, name, invite_token: uuid().slice(0, 12), invite_expires_at: new Date(Date.now() + 86_400_000).toISOString(), members: [], active_session_id: null };
      saveHouseholds({ ...loadHouseholds(), [h.household_id]: h });
      return h;
    },
    async getHousehold(hid) {
      const h = loadHouseholds()[hid];
      if (!h) throw new ApiError('unknown household', 404);
      return h;
    },
    async rotateInvite(hid) {
      const h = loadHouseholds()[hid];
      if (!h) throw new ApiError('unknown household', 404);
      const next = { ...h, invite_token: uuid().slice(0, 12), invite_expires_at: new Date(Date.now() + 86_400_000).toISOString() };
      saveHouseholds({ ...loadHouseholds(), [hid]: next });
      return next;
    },
    async joinHousehold(invite, name, memberId) {
      const all = loadHouseholds();
      const h = Object.values(all).find((x) => x.invite_token === invite);
      if (!h) throw new ApiError('This family code is unknown or has expired. Ask for a new one on the big screen.', 403);
      const member = (h.members ?? []).find((m) => m.member_id === memberId || m.name.toLowerCase() === name.toLowerCase()) ?? { member_id: `m-${uuid().slice(0, 12)}`, name };
      const next = { ...h, members: [...(h.members ?? []).filter((m) => m.member_id !== member.member_id), { ...member, name }] };
      saveHouseholds({ ...all, [h.household_id]: next });
      return { household: next, member: { ...member, name }, session_id: h.active_session_id ?? null, client_token: h.active_session_id ? 'fx' : null };
    },
    async joinSession() {
      return { ok: true };
    },
    async upload(file) {
      const upload_id = `fx-up-${uuid().slice(0, 8)}`;
      // Keep small images so the new item can show its own photo; larger ones fall back to a fixture image.
      const url = file.size < 400_000 ? await blobToDataUrl(file) : '';
      patchOverlay((o) => (o.uploads[upload_id] = url));
      return { upload_id, url: url || `/fixtures/blobs/inventory/armchair.svg` };
    },
    async ingestObject(uploadId) {
      const job_id = `fx-job-${uuid().slice(0, 8)}`;
      const base = { job_id, kind: 'object.ingest' };
      const steps: [number, number][] = [
        [150, 5],
        [600, 30],
        [1100, 65],
        [1600, 90],
      ];
      emitJob({ ...base, pct: 0, status: 'queued' });
      for (const [delay, pct] of steps) setTimeout(() => emitJob({ ...base, pct, status: 'running' }), delay);
      setTimeout(() => {
        void (async () => {
          const template = (await fixture<InventoryItem[]>(`inventory/${encodeURIComponent(config.userId)}.json`)).at(-1);
          if (!template) return emitJob({ ...base, pct: 100, status: 'failed' });
          const item: InventoryItem = {
            ...template,
            item_id: uuid(),
            label: 'Scanned item (fixture)',
            source_image_url: readOverlay().uploads[uploadId] || template.source_image_url,
            mask_url: null,
            price: null,
          };
          patchOverlay((o) => o.addedItems.push(item));
          emitJob({ ...base, pct: 100, status: 'done', result_ref: item.item_id });
        })();
      }, 2100);
      return { job_id };
    },
    listInventory: inventory,
    async getInventoryItem(id) {
      const hit = (await inventory()).find((i) => i.item_id === id);
      if (!hit) throw new ApiError(`No inventory item ${id}`, 404);
      return hit;
    },
    async deleteInventoryItem(id) {
      patchOverlay((o) => {
        o.addedItems = o.addedItems.filter((i) => i.item_id !== id);
        if (!o.deletedItems.includes(id)) o.deletedItems.push(id);
      });
    },
    async listLayouts(pid, roomId) {
      const o = readOverlay();
      return (await baseLayouts(pid)).filter((l) => !roomId || l.room_id === roomId).map((l) => withOverlay(l, o));
    },
    getLayout: findLayout,
    async selectLayout(id) {
      const chosen = await findLayout(id);
      const siblings = (await baseLayouts(chosen.property_id)).filter((l) => l.room_id === chosen.room_id);
      patchOverlay((o) => {
        // One chosen layout per room: the previous choice goes back to "proposed".
        for (const s of siblings) if (o.layouts[s.layout_id]?.status === 'chosen') o.layouts[s.layout_id] = { ...o.layouts[s.layout_id], status: 'proposed' };
        o.layouts[id] = { ...o.layouts[id], status: 'chosen' };
      });
      return { ...chosen, status: 'chosen' };
    },
    async movePlacement(id, itemId, pose) {
      // Fixture mode has no solver: the move is kept as given, with no warnings.
      const layout = withOverlay(await findLayout(id), readOverlay());
      const placements = layout.placements.map((p) => (p.item_id === itemId ? { ...p, position: { ...p.position, x: pose.x, z: pose.z }, yaw_deg: ((pose.yaw_deg % 360) + 360) % 360 } : p));
      patchOverlay((o) => (o.layouts[id] = { ...o.layouts[id], placements }));
      return { ...layout, placements };
    },
    async removePlacement(id, itemId) {
      const layout = withOverlay(await findLayout(id), readOverlay());
      const placements = layout.placements.filter((p) => p.item_id !== itemId);
      patchOverlay((o) => (o.layouts[id] = { ...o.layouts[id], placements }));
      return { ...layout, placements };
    },
    // The catalog and the drop solver live on the API: fixture mode has neither.
    async dropItem() {
      throw new ApiError('Placing a piece needs the API (fixture mode is on).', 501, true);
    },
    async placementOptions() {
      throw new ApiError('Placing a piece needs the API (fixture mode is on).', 501, true);
    },
    async listCatalog() {
      return [];
    },
    async addFromCatalog() {
      throw new ApiError('The furniture catalog needs the API (fixture mode is on).', 501, true);
    },
    async layoutFeedback(id, feedback) {
      const layout = await findLayout(id);
      patchOverlay((o) => (o.layouts[id] = { ...o.layouts[id], feedback }));
      return { ...layout, feedback };
    },
    listMemory: memoryIndex,
    async getMemory(p) {
      const o = readOverlay();
      if (p in o.memory) {
        const text = o.memory[p];
        if (text === null || text === undefined) throw new ApiError(`No memory file ${p}`, 404);
        return text;
      }
      return fixture(`memory/${encodeURIComponent(config.userId)}/${p.split('/').map(encodeURIComponent).join('/')}`, 'text');
    },
    async putMemory(p, text) {
      patchOverlay((o) => (o.memory[p] = text));
    },
    async deleteMemory(p) {
      patchOverlay((o) => (o.memory[p] = null));
    },

    assetUrl: (url) => (typeof window === 'undefined' ? url : new URL(url, window.location.href).toString()),
  };
}
