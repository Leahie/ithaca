'use client';

import { useCallback, useEffect, useState } from 'react';

/**
 * A small keyed async cache. `useResource(key, loader)` loads once per key and shares the
 * result; `invalidate(prefix)` drops matching keys and makes mounted users reload. While a
 * reload is on its way the previous value is still returned.
 */
interface Entry {
  promise: Promise<unknown>;
  data?: unknown;
  error?: Error;
  done: boolean;
}

const cache = new Map<string, Entry>();
const listeners = new Set<() => void>();

/** The last loaded value per key, shown while a reload is on its way, so nothing blinks empty. */
const stale = new Map<string, unknown>();

export function invalidate(prefix: string): void {
  for (const [key, entry] of [...cache.entries()]) {
    if (!key.startsWith(prefix)) continue;
    if (entry.done && !entry.error) stale.set(key, entry.data);
    cache.delete(key);
  }
  listeners.forEach((l) => l());
}

/**
 * Drop everything, the kept copies too: after "Reset everything" none of it is true any more.
 * Mounted users are not woken: the page is about to be loaded again, and a reload now would only
 * ask a server that has just been emptied.
 */
export function clearResources(): void {
  cache.clear();
  stale.clear();
}

export interface Resource<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  reload: () => void;
}

export function useResource<T>(key: string | null, loader: () => Promise<T>): Resource<T> {
  const [, bump] = useState(0);
  const rerender = useCallback(() => bump((n) => n + 1), []);

  useEffect(() => {
    listeners.add(rerender);
    return () => {
      listeners.delete(rerender);
    };
  }, [rerender]);

  let entry = key ? cache.get(key) : undefined;
  if (key && !entry && typeof window !== 'undefined') {
    const created: Entry = { promise: Promise.resolve(), done: false };
    created.promise = loader().then(
      (data) => {
        created.data = data;
        created.done = true;
      },
      (error: unknown) => {
        created.error = error instanceof Error ? error : new Error(String(error));
        created.done = true;
      },
    );
    cache.set(key, created);
    entry = created;
  }

  useEffect(() => {
    if (!entry) return;
    // Also when the entry is already done: it may have settled between render and commit,
    // in which case this component rendered "loading" and nothing else would wake it.
    let live = true;
    void entry.promise.then(() => live && rerender());
    return () => {
      live = false;
    };
  }, [entry, rerender]);

  const reload = useCallback(() => {
    if (key) invalidate(key);
  }, [key]);

  const data = entry?.done ? entry.data : key ? stale.get(key) : undefined;
  return { data: data as T | undefined, error: entry?.error, loading: !!key && !entry?.done, reload };
}
