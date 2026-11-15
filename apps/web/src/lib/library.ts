'use client';

import type { JobProgressPayload, LibraryFacet, LibraryPiece } from '@ithaca/schema';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { useSession } from '@/lib/store/session';

/** Pieces asked for at a time: a few screens of cards. */
export const LIBRARY_PAGE = 60;

/** What the furniture menu is narrowed to. */
export interface LibraryFilter {
  q?: string;
  category?: string | null;
  source?: string | null;
  /** One design's key: its colours and sizes, and nothing else. */
  family?: string | null;
}

export interface Library {
  /** The pieces loaded so far. While a new search is on its way these are still the old ones. */
  items: LibraryPiece[];
  /** How many pieces match, loaded or not. */
  total: number;
  /** The size of the whole library, whatever is searched for. Null until the first answer. */
  totalAll: number | null;
  /** Every section and collection in menu order, counted for the search; empty until the first answer. */
  categories: LibraryFacet[];
  sources: LibraryFacet[];
  /** The first page for this filter is on its way. */
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  /** The first page failed. */
  error: string | null;
  /** A later page failed: no more is asked for until `loadMore` is called by hand. */
  moreError: string | null;
  loadMore(): void;
  retry(): void;
}

interface Loaded {
  /** The filter `items` answer. */
  key: string;
  items: LibraryPiece[];
  total: number;
  status: 'loading' | 'ready' | 'more';
  error: string | null;
  moreError: string | null;
}

interface Facets {
  categories: LibraryFacet[];
  sources: LibraryFacet[];
  totalAll: number;
}

const words = (err: unknown) => (err instanceof Error ? err.message : String(err));

/**
 * The furniture library, a page at a time (`GET /library`). A new filter starts again from the
 * first page; `loadMore` adds the next one. The old pieces and counts stay until the new ones
 * arrive, so the menu never blinks empty while someone types. `enabled: false` asks for nothing.
 */
export function useLibrary(filter: LibraryFilter, enabled = true): Library {
  const q = filter.q?.trim() ?? '';
  const category = filter.category ?? null;
  const source = filter.source ?? null;
  const family = filter.family ?? null;
  const key = [q, category ?? '', source ?? '', family ?? ''].join('\n');

  const [state, setState] = useState<Loaded>({ key: '', items: [], total: 0, status: 'loading', error: null, moreError: null });
  const [facets, setFacets] = useState<Facets | null>(null);
  const [attempt, setAttempt] = useState(0);
  // What loadMore reads: two calls in one frame must not both ask for the same page.
  const now = useRef(state);
  now.current = state;

  useEffect(() => {
    if (!enabled) return;
    let live = true;
    setState((s) => ({ ...s, status: 'loading', error: null, moreError: null }));
    api()
      .listLibrary({ q, category, source, family, offset: 0, limit: LIBRARY_PAGE })
      .then(
        (page) => {
          if (!live) return;
          setState({ key, items: page.items, total: page.total, status: 'ready', error: null, moreError: null });
          setFacets({ categories: page.categories, sources: page.sources, totalAll: page.total_all });
        },
        (err: unknown) => {
          if (live) setState({ key, items: [], total: 0, status: 'ready', error: words(err), moreError: null });
        },
      );
    return () => {
      live = false;
    };
    // `key` stands for q, category, source and family.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled, attempt]);

  const loadMore = useCallback(() => {
    const s = now.current;
    if (s.key !== key || s.status !== 'ready' || s.error || s.items.length >= s.total) return;
    const next: Loaded = { ...s, status: 'more', moreError: null };
    now.current = next;
    setState(next);
    api()
      .listLibrary({ q, category, source, family, offset: s.items.length, limit: LIBRARY_PAGE })
      .then(
        (page) =>
          setState((cur) => {
            if (cur.key !== key || cur.status !== 'more') return cur; // the filter changed meanwhile
            const have = new Set(cur.items.map((p) => p.library_id));
            const fresh = page.items.filter((p) => !have.has(p.library_id));
            // A page with nothing new is the end, whatever the total says: never ask in a loop.
            return { ...cur, items: [...cur.items, ...fresh], total: fresh.length ? page.total : cur.items.length, status: 'ready' };
          }),
        (err: unknown) => setState((cur) => (cur.key !== key || cur.status !== 'more' ? cur : { ...cur, status: 'ready', moreError: words(err) })),
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const current = state.key === key;
  const loading = enabled && (!current || state.status === 'loading');
  // Another design's colours are not worth showing while this design's load: only a search keeps its old pieces.
  const blank = !enabled || (!current && !!family);
  return {
    items: blank ? [] : state.items,
    total: blank ? 0 : state.total,
    totalAll: facets?.totalAll ?? null,
    categories: facets?.categories ?? [],
    sources: facets?.sources ?? [],
    loading,
    loadingMore: current && state.status === 'more',
    hasMore: current && !loading && state.items.length < state.total,
    error: current && state.status === 'ready' ? state.error : null,
    moreError: current ? state.moreError : null,
    loadMore,
    retry,
  };
}

/** A value that follows `value` once it has stopped changing for `ms`: one search per pause in typing. */
export function useDebounced<T>(value: T, ms = 250): T {
  const [late, setLate] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setLate(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return late;
}

const FINISHED = new Set(['done', 'failed']);

/**
 * One background job's progress. It arrives as `job.progress` events, but a session that dropped
 * for a moment hears nothing, so an open job is also asked for; whichever answer is further along
 * counts (finished beats running, then the higher percent).
 */
export function useJobProgress(jobId: string | null): JobProgressPayload | undefined {
  const heard = useSession((s) => (jobId ? (s.jobs[jobId] as JobProgressPayload | undefined) : undefined));
  const [asked, setAsked] = useState<JobProgressPayload | undefined>();
  const polled = asked?.job_id === jobId ? asked : undefined;
  const best =
    !heard || !polled ? (heard ?? polled) : FINISHED.has(heard.status) !== FINISHED.has(polled.status) ? (FINISHED.has(heard.status) ? heard : polled) : polled.pct > heard.pct ? polled : heard;
  const open = !!jobId && !FINISHED.has(best?.status ?? 'queued');
  useEffect(() => {
    if (!jobId || !open) return;
    const t = setInterval(() => {
      api().getJob(jobId).then(setAsked, () => {});
    }, 2500);
    return () => clearInterval(t);
  }, [jobId, open]);
  return best;
}
