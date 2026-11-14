'use client';

import { useCallback, useEffect, useState } from 'react';

/**
 * A small keyed async cache. `useResource(key, loader)` loads once per key and shares the
 * result; `invalidate(prefix)` drops matching keys and makes mounted users reload.
 */
interface Entry {
  promise: Promise<unknown>;
  data?: unknown;
  error?: Error;
  done: boolean;
}

const cache = new Map<string, Entry>();
const listeners = new Set<() => void>();

export function invalidate(prefix: string): void {
  for (const key of [...cache.keys()]) if (key.startsWith(prefix)) cache.delete(key);
  listeners.forEach((l) => l());
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

  return { data: entry?.data as T | undefined, error: entry?.error, loading: !!key && !entry?.done, reload };
}
