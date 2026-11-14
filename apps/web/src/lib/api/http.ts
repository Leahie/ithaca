import type { NoiseScenario } from '@ithaca/schema';
import { config } from '@/lib/config';
import { ApiError, type ApiClient } from './types';
import { apiUserId } from './user';

async function request<T>(path: string, init: RequestInit = {}, as: 'json' | 'text' | 'none' = 'json'): Promise<T> {
  let res: Response;
  try {
    res = await fetch(config.apiBase + path, {
      ...init,
      headers: { 'X-User-Id': apiUserId(), ...(init.body && !(init.body instanceof FormData) ? { 'content-type': 'application/json' } : {}), ...init.headers },
    });
  } catch (err) {
    throw new ApiError(`Cannot reach the API at ${config.apiBase} (${(err as Error).message})`, 0);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown; message?: unknown };
      const d = body.detail ?? body.message;
      if (d) detail = typeof d === 'string' ? d : JSON.stringify(d);
    } catch {
      /* not JSON */
    }
    throw new ApiError(detail, res.status, res.status === 501 || res.status === 503 || /not configured/i.test(detail));
  }
  if (as === 'none' || res.status === 204) return undefined as T;
  return (as === 'text' ? await res.text() : await res.json()) as T;
}

const q = (params: Record<string, string | number | boolean | null | undefined>) => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined) sp.set(k, String(v));
  const s = sp.toString();
  return s ? `?${s}` : '';
};
const enc = encodeURIComponent;
const memoryPath = (p: string) => p.split('/').filter(Boolean).map(enc).join('/');

export function createHttpApi(): ApiClient {
  return {
    mode: 'http',
    getTwin: (pid) => request(`/properties/${enc(pid)}/twin`),
    putOpenings: (pid, openings) => request(`/properties/${enc(pid)}/openings`, { method: 'PUT', body: JSON.stringify(openings) }),
    getSun: (pid, roomId, start, end) => request(`/properties/${enc(pid)}/sun${q({ room_id: roomId, start, end })}`),
    getNoise: (pid, roomId, s: NoiseScenario) =>
      request(`/properties/${enc(pid)}/noise${q({ room_id: roomId, glazing: s.glazing, add_carpet: s.add_carpet, include_furniture: s.include_furniture })}`),
    getArea: (pid) => request(`/properties/${enc(pid)}/area`),
    ingestProperty: (pid) => request(`/properties/${enc(pid)}/ingest`, { method: 'POST' }),

    createSession: (opts) =>
      request('/sessions', {
        method: 'POST',
        ...(opts?.propertyId ? { body: JSON.stringify({ property_id: opts.propertyId, household_id: opts.householdId ?? null }) } : {}),
      }),
    createHousehold: (name) => request('/households', { method: 'POST', body: JSON.stringify({ name }) }),
    getHousehold: (hid) => request(`/households/${enc(hid)}`),
    rotateInvite: (hid) => request(`/households/${enc(hid)}/invite`, { method: 'POST' }),
    joinHousehold: (invite_token, name, member_id) =>
      request('/households/join', { method: 'POST', body: JSON.stringify({ invite_token, name, member_id: member_id ?? null }) }),
    joinSession: (sid, pairToken) => request(`/sessions/${enc(sid)}/join`, { method: 'POST', body: JSON.stringify({ pair_token: pairToken }) }),
    upload: (file, filename) => {
      const form = new FormData();
      form.append('file', file, filename);
      return request('/uploads', { method: 'POST', body: form });
    },
    ingestObject: (uploadId) => request('/objects/ingest', { method: 'POST', body: JSON.stringify({ upload_id: uploadId }) }),
    listInventory: () => request('/inventory'),
    getInventoryItem: (id) => request(`/inventory/${enc(id)}`),
    deleteInventoryItem: (id) => request(`/inventory/${enc(id)}`, { method: 'DELETE' }, 'none'),
    listLayouts: (pid, roomId) => request(`/layouts${q({ property_id: pid, room_id: roomId })}`),
    getLayout: (id) => request(`/layouts/${enc(id)}`),
    selectLayout: (id) => request(`/layouts/${enc(id)}/select`, { method: 'POST' }),
    layoutFeedback: (id, feedback) => request(`/layouts/${enc(id)}/feedback`, { method: 'POST', body: JSON.stringify({ feedback }) }),
    movePlacement: (id, itemId, pose) => request(`/layouts/${enc(id)}/placements/${enc(itemId)}`, { method: 'PUT', body: JSON.stringify(pose) }),
    removePlacement: (id, itemId) => request(`/layouts/${enc(id)}/placements/${enc(itemId)}`, { method: 'DELETE' }),
    dropItem: (drop) => request('/layouts/drop', { method: 'POST', body: JSON.stringify(drop) }),
    placementOptions: (ask) => request('/layouts/options', { method: 'POST', body: JSON.stringify(ask) }),
    listCatalog: () => request('/catalog'),
    addFromCatalog: (catalogId) => request(`/catalog/${enc(catalogId)}/add`, { method: 'POST' }),
    listMemory: () => request('/memory'),
    getMemory: (p) => request(`/memory/${memoryPath(p)}`, {}, 'text'),
    putMemory: (p, text) => request(`/memory/${memoryPath(p)}`, { method: 'PUT', body: text, headers: { 'content-type': 'text/plain; charset=utf-8' } }, 'none'),
    deleteMemory: (p) => request(`/memory/${memoryPath(p)}`, { method: 'DELETE' }, 'none'),

    assetUrl: (url) => (typeof window === 'undefined' ? url : new URL(url, window.location.href).toString()),
  };
}
