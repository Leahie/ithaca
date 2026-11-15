'use client';

import type { NoiseScenario, SoundPlaceSourcePayload } from '@ithaca/schema';
import { api } from '@/lib/api';
import { noiseKey } from '@/lib/api/fixture';
import { useResource } from './resource';

export const useTwin = (propertyId: string | null) => useResource(propertyId ? `twin:${propertyId}` : null, () => api().getTwin(propertyId!));

export const useLayouts = (propertyId: string | null, roomId?: string | null) =>
  useResource(propertyId ? `layouts:${propertyId}:${roomId ?? '*'}` : null, () => api().listLayouts(propertyId!, roomId ?? undefined));

export const useInventory = () => useResource('inventory', () => api().listInventory());

/** What stands in the house now (docs/specs/furniture.md). */
export const useHouseFurniture = (propertyId: string | null) =>
  useResource(propertyId ? `furniture:${propertyId}` : null, () => api().listHouseFurniture(propertyId!));

export const useSunReport = (propertyId: string, roomId: string | null, start: string, end: string) =>
  useResource(roomId ? `sun:${propertyId}:${roomId}:${start}:${end}` : null, () => api().getSun(propertyId, roomId!, start, end));

export const useNoiseReport = (propertyId: string, roomId: string | null, scenario: NoiseScenario) =>
  useResource(roomId ? `noise:${propertyId}:${roomId}:${noiseKey(scenario)}` : null, () => api().getNoise(propertyId, roomId!, scenario));

export const useArea = (propertyId: string | null) => useResource(propertyId ? `area:${propertyId}` : null, () => api().getArea(propertyId!));

export const useSoundPresets = (propertyId: string) => useResource(`sound-presets:${propertyId}`, () => api().getSoundPresets(propertyId));

/** How a placed sound carries through the house. */
export const useSoundField = (propertyId: string, source: SoundPlaceSourcePayload | null, doors: 'closed' | 'open') =>
  useResource(source ? `sound-field:${propertyId}:${source.room_id}:${source.x}:${source.z}:${source.preset_id}:${source.level_dba ?? ''}:${doors}` : null, () =>
    api().getSoundField(propertyId, source!, doors),
  );

export const useMemoryList = () => useResource('memory:list', () => api().listMemory());
export const useMemoryFile = (path: string | null) => useResource(path ? `memory:file:${path}` : null, () => api().getMemory(path!));
