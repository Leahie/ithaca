import type { HousePiece, Household, InventoryItem, JobProgressPayload, LayoutVariant, NoiseReport, NoiseScenario, Opening, PropertyTwin, SunReport } from '@ithaca/schema';
import { config } from '@/lib/config';
import { pointInPolygon } from '@/lib/geometry/polygon';
import { ApiError, type ApiClient } from './types';
import type { ResetCounts } from '@ithaca/schema';
import type { BoardEntry } from '@ithaca/schema';
import { apiUserId } from './user';

/**
 * Fixture-mode API (A6). Reads come from the repo's `fixtures/` tree, served by the
 * `/fixtures/[...path]` route. Writes go to an overlay in localStorage so that every tab of
 * the same browser (desktop, phone remote, /me) sees them. No backend is involved.
 */

const OVERLAY_KEY = `ithaca.fixture.${config.userId}`;

interface Overlay {
  openings: Record<string, Opening[]>;
  layouts: Record<string, Partial<Pick<LayoutVariant, 'status' | 'feedback'>>>;
  /** The in_house table, per property. Absent until this browser first changes the house. */
  house: Record<string, HousePiece[]>;
  addedItems: InventoryItem[];
  deletedItems: string[];
  memory: Record<string, string | null>; // null = deleted
  uploads: Record<string, string>; // upload_id -> preview URL
  scales: Record<string, number>; // item_id -> scale set by hand
}

const empty = (): Overlay => ({ openings: {}, layouts: {}, house: {}, addedItems: [], deletedItems: [], memory: {}, uploads: {}, scales: {} });

/** A piece at the scale set by hand (its dims follow). */
const resized = (item: InventoryItem, scale: number | undefined): InventoryItem => {
  if (scale === undefined) return item;
  const k = scale / (item.scale ?? 1);
  return { ...item, scale, dims_m: { w: item.dims_m.w * k, d: item.dims_m.d * k, h: item.dims_m.h * k } };
};

/** Yaw in [0, 360), to 0.1° (F5). */
const normYaw = (deg: number): number => {
  const r = Math.round((((deg % 360) + 360) % 360) * 10) / 10;
  return r >= 360 ? 0 : r;
};

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
const fixtureJobs = new Map<string, JobProgressPayload>();
const emitJob = (payload: JobProgressPayload) => {
  fixtureJobs.set(payload.job_id, payload);
  jobListeners.forEach((cb) => cb(payload));
};

export function noiseKey(s: NoiseScenario): string {
  return `${s.glazing ?? 'asbuilt'}|${s.add_carpet ? 1 : 0}|${s.include_furniture === false ? 0 : 1}${s.windows_open ? '|open' : ''}`;
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

/**
 * The family board in fixture mode: kept in memory, per board, with the API's rules. It lasts
 * until the page is reloaded, and each tab has its own.
 */
const boards = new Map<string, BoardEntry[]>();
const oneLine = (text: string) => text.split(/\s+/).join(' ').trim();
/** The family whose board the caller sees: a member's own, else the one named, else none. */
function boardFamily(householdId?: string | null): Household | undefined {
  const all = loadHouseholds();
  const me = apiUserId();
  if (me.startsWith('m-')) return Object.values(all).find((h) => (h.members ?? []).some((m) => m.member_id === me));
  return householdId ? all[householdId] : undefined;
}
const boardKey = (householdId?: string | null) => boardFamily(householdId)?.household_id ?? `solo:${config.userId}`;

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
      house: fixtureAssetUrl(a.house),
    },
  };
}

const NOTHING_TO_DELETE: ResetCounts = { furniture: 0, in_houses: 0, layouts: 0, memory_files: 0, conversations: 0, families: 0, members: 0, files: 0 };

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
    return [...base, ...o.addedItems].filter((i) => !o.deletedItems.includes(i.item_id)).map((i) => resized(i, o.scales[i.item_id]));
  };

  /**
   * What stands in the house. Until this browser first changes it, that is the fixture's chosen
   * layouts (one per room, later ones winning a piece), less pieces no longer in storage.
   */
  const house = async (pid: string): Promise<HousePiece[]> => {
    const o = readOverlay();
    const owned = new Set((await inventory()).map((i) => i.item_id));
    const stored = o.house[pid];
    if (stored) return stored.filter((p) => owned.has(p.item_id));
    const twin = await baseTwin(pid);
    const pieces = new Map<string, HousePiece>();
    for (const l of (await baseLayouts(pid)).map((x) => withOverlay(x, o)).filter((x) => x.status === 'chosen')) {
      const floorY = twin.rooms.find((r) => r.room_id === l.room_id)?.floor_y ?? 0;
      for (const p of l.placements) {
        if (!owned.has(p.item_id)) continue;
        pieces.set(p.item_id, { item_id: p.item_id, property_id: pid, room_id: l.room_id, position: { ...p.position, y: floorY }, yaw_deg: normYaw(p.yaw_deg), placed_by: l.user_id, updated_at: new Date(0).toISOString(), warnings: [] });
      }
    }
    return [...pieces.values()];
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
      // The fixtures have no open-window reports: the windows-closed one stands in.
      const hit = all[noiseKey(scenario)] ?? all[noiseKey({ ...scenario, windows_open: false })];
      if (!hit) throw new ApiError(`No noise fixture for scenario ${noiseKey(scenario)}`, 404);
      return hit;
    },
    getArea: (pid) => fixture(`reports/${encodeURIComponent(pid)}/area.json`),
    // The sound simulation runs on the server (sim/noise.py); nothing stands in for it offline.
    async searchArea() {
      throw new ApiError('Searching the wider area needs the API (fixture mode is offline)', 501, true);
    },
    async getSoundPresets() {
      throw new ApiError('Placing a sound needs the API (fixture mode is offline)', 501, true);
    },
    async getSoundField() {
      throw new ApiError('Placing a sound needs the API (fixture mode is offline)', 501, true);
    },
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
    async leaveHousehold() {},
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
    async getJob(jobId) {
      const job = fixtureJobs.get(jobId);
      if (!job) throw new ApiError('no such job', 404);
      return job;
    },
    async furnishFromPhoto() {
      // Cutting things out of a photo and rebuilding them in 3D needs the API and its model services.
      throw new ApiError('Setting a room up from a photo needs the API (fixture mode has none).', 503, true);
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
    async resizePiece(_pid, id, scale) {
      if (!(await inventory()).some((i) => i.item_id === id)) throw new ApiError(`No inventory item ${id}`, 404);
      patchOverlay((o) => {
        o.scales[id] = Math.min(5, Math.max(0.2, scale));
      });
      return (await inventory()).find((i) => i.item_id === id)!;
    },
    async deleteInventoryItem(id) {
      // A deleted piece leaves every house too (F3).
      const pids = new Set([...twins.keys(), ...Object.keys(readOverlay().house), config.defaultPropertyId].filter(Boolean));
      const houses = await Promise.all([...pids].map(async (pid) => [pid, await house(pid)] as const));
      patchOverlay((o) => {
        o.addedItems = o.addedItems.filter((i) => i.item_id !== id);
        if (!o.deletedItems.includes(id)) o.deletedItems.push(id);
        for (const [pid, pieces] of houses) o.house[pid] = pieces.filter((p) => p.item_id !== id);
      });
    },
    listHouseFurniture: house,
    async placePiece(pid, itemId, place) {
      // Fixture mode has no solver: the spot is kept as given (snap included), with no warnings.
      if (!(await inventory()).some((i) => i.item_id === itemId)) throw new ApiError(`No piece ${itemId} in storage`, 404);
      const twin = await baseTwin(pid);
      const room = twin.rooms.find((r) => (!place.floor_id || r.floor_id === place.floor_id) && pointInPolygon([place.x, place.z], r.polygon));
      if (!room) throw new ApiError('That spot is not inside any room.', 409);
      if (place.room_id && place.room_id !== room.room_id) throw new ApiError(`That spot is in ${room.label}, not ${place.room_id}.`, 409);
      const pieces = await house(pid);
      const prev = pieces.find((p) => p.item_id === itemId);
      const piece: HousePiece = {
        item_id: itemId,
        property_id: pid,
        room_id: room.room_id,
        position: { x: place.x, y: room.floor_y, z: place.z },
        yaw_deg: normYaw(place.yaw_deg ?? prev?.yaw_deg ?? 0),
        placed_by: config.userId,
        updated_at: new Date().toISOString(),
        warnings: [],
      };
      patchOverlay((o) => (o.house[pid] = [...pieces.filter((p) => p.item_id !== itemId), piece]));
      return piece;
    },
    async removePiece(pid, itemId) {
      const pieces = await house(pid);
      patchOverlay((o) => (o.house[pid] = pieces.filter((p) => p.item_id !== itemId)));
    },
    async listLayouts(pid, roomId) {
      const o = readOverlay();
      return (await baseLayouts(pid)).filter((l) => !roomId || l.room_id === roomId).map((l) => withOverlay(l, o));
    },
    getLayout: findLayout,
    async selectLayout(id) {
      const chosen = await findLayout(id);
      const pid = chosen.property_id;
      const siblings = (await baseLayouts(pid)).filter((l) => l.room_id === chosen.room_id);
      const owned = new Set((await inventory()).map((i) => i.item_id));
      const twin = await baseTwin(pid);
      const floorY = twin.rooms.find((r) => r.room_id === chosen.room_id)?.floor_y ?? 0;
      const now = new Date().toISOString();
      const kept: HousePiece[] = chosen.placements
        .filter((p) => owned.has(p.item_id))
        .map((p) => ({ item_id: p.item_id, property_id: pid, room_id: chosen.room_id, position: { ...p.position, y: floorY }, yaw_deg: normYaw(p.yaw_deg), placed_by: config.userId, updated_at: now, warnings: [] }));
      const keptIds = new Set(kept.map((p) => p.item_id));
      // The room becomes exactly the proposal; a piece it takes from another room leaves that room.
      const rest = (await house(pid)).filter((p) => p.room_id !== chosen.room_id && !keptIds.has(p.item_id));
      patchOverlay((o) => {
        for (const s of siblings) if (withOverlay(s, o).status === 'chosen') o.layouts[s.layout_id] = { ...o.layouts[s.layout_id], status: 'proposed' };
        o.layouts[id] = { ...o.layouts[id], status: 'chosen' };
        o.house[pid] = [...rest, ...kept];
      });
      return { ...chosen, status: 'chosen' };
    },
    // The catalog and the placement solver live on the API: fixture mode has neither.
    async placementOptions() {
      throw new ApiError('Placing a piece needs the API (fixture mode is on).', 501, true);
    },
    async listCatalog() {
      return [];
    },
    async addFromCatalog() {
      throw new ApiError('The furniture catalog needs the API (fixture mode is on).', 501, true);
    },
    // The library and the designer live on the API too: an empty library, no looks.
    async listLibrary() {
      return { total: 0, total_all: 0, items: [], categories: [], sources: [] };
    },
    async addFromLibrary() {
      throw new ApiError('The furniture library needs the API (fixture mode is on).', 501, true);
    },
    async listLooks() {
      return [];
    },
    async furnishRoom() {
      throw new ApiError('Furnishing a room needs the API (fixture mode is on).', 501, true);
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
    // Fixture mode has no backend, so there is nothing to count. A reset forgets what this browser
    // changed on top of the fixtures (the overlay, the families) and what this tab holds in memory.
    async resetPreview() {
      return { ...NOTHING_TO_DELETE };
    },
    async resetEverything() {
      resetFixtureOverlay();
      try {
        window.localStorage.removeItem(HOUSEHOLDS_KEY);
      } catch {
        /* nothing was stored */
      }
      fixtureJobs.clear();
      boards.clear();
      return { ok: true, deleted: { ...NOTHING_TO_DELETE }, failed: [] };
    },
    async getBoard({ propertyId, householdId }) {
      const family = boardFamily(householdId);
      const key = boardKey(householdId);
      const entries = (boards.get(key) ?? []).filter((e) => !propertyId || !e.property_id || e.property_id === propertyId);
      const cards = family
        ? [{ person_id: 'shared', name: family.name, shared: true }, ...(family.members ?? []).map((m) => ({ person_id: m.member_id, name: m.name, shared: false }))]
        : [{ person_id: 'shared', name: 'You', shared: true }];
      return { board_id: key, household_id: family?.household_id ?? null, name: family?.name ?? null, people: cards.map((c) => ({ ...c, entries: entries.filter((e) => e.person_id === c.person_id) })), notes: [] };
    },
    async addBoardEntry(entry) {
      const text = oneLine(entry.text).slice(0, 200);
      const room = entry.kind === 'room';
      if (!text) throw new ApiError('an entry needs some text', 422);
      if (room && !(entry.room_id && entry.property_id)) throw new ApiError('a room wish needs room_id and property_id', 422);
      const key = boardKey(entry.household_id);
      const list = boards.get(key) ?? [];
      const room_id = room ? (entry.room_id ?? null) : null;
      const now = new Date().toISOString();
      // Saying what the card already says changes that line: no second copy, as on the server.
      const same = list.find((e) => e.person_id === entry.person_id && e.kind === entry.kind && (e.room_id ?? null) === room_id && oneLine(e.text).toLowerCase() === text.toLowerCase());
      const saved: BoardEntry = same
        ? { ...same, text, updated_at: same.text === text ? same.updated_at : now }
        : { entry_id: uuid().replace(/-/g, ''), person_id: entry.person_id, kind: entry.kind, text, room_id, property_id: room ? (entry.property_id ?? null) : null, by: 'hand', created_at: now, updated_at: now };
      boards.set(key, same ? list.map((e) => (e === same ? saved : e)) : [...list, saved]);
      return saved;
    },
    async updateBoardEntry(id, text) {
      for (const [key, list] of boards) {
        const hit = list.find((e) => e.entry_id === id);
        if (!hit) continue;
        const saved: BoardEntry = { ...hit, text: oneLine(text).slice(0, 200) || hit.text, by: 'hand', updated_at: new Date().toISOString() };
        boards.set(key, list.map((e) => (e === hit ? saved : e)));
        return saved;
      }
      throw new ApiError('no such entry', 404);
    },
    async deleteBoardEntry(id) {
      for (const [key, list] of boards) boards.set(key, list.filter((e) => e.entry_id !== id));
    },

    assetUrl: (url) => (typeof window === 'undefined' ? url : new URL(url, window.location.href).toString()),
  };
}
