'use client';

import type { Amenity, PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useState } from 'react';
import { CHART, TableView, niceTicks } from '@/components/charts/kit';
import { Markdown } from '@/components/Markdown';
import { Badge, Notice, Spinner, ViewState, cn } from '@/components/ui';
import { api } from '@/lib/api';
import { useArea } from '@/lib/data/hooks';
import { listingFacts, money } from '@/lib/listing';

const METERS_PER_DEG = 111_320;

/** East / north offset of a place from the property, in meters (local tangent plane). */
function offset(twin: PropertyTwin, a: Amenity): [number, number] {
  return [(a.lon - twin.lon) * METERS_PER_DEG * Math.cos((twin.lat * Math.PI) / 180), (a.lat - twin.lat) * METERS_PER_DEG];
}

const distance = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`);
const placeKey = (a: Amenity) => `${a.name}|${a.lat}|${a.lon}`;

function AreaMap({ twin, amenities, active, onActive }: { twin: PropertyTwin; amenities: Amenity[]; active: string | null; onActive(key: string | null): void }) {
  const size = 440;
  const reach = Math.max(300, ...amenities.map((a) => Math.hypot(...offset(twin, a)))) * 1.12;
  const rings = niceTicks(0, reach, 4).filter((r) => r > 0);
  const scale = (size / 2 - 18) / reach;
  const px = (e: number) => size / 2 + e * scale;
  const py = (n: number) => size / 2 - n * scale; // north is up

  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="h-auto w-full" role="group" aria-label={`Map of ${amenities.length} places around the home. North is up.`}>
      {rings.map((r) => (
        <g key={r}>
          <circle cx={size / 2} cy={size / 2} r={r * scale} fill="none" stroke={CHART.grid} strokeWidth={1} />
          <text x={size / 2 + 4} y={size / 2 - r * scale + 12} fontSize={10} fill={CHART.muted}>
            {distance(r)}
          </text>
        </g>
      ))}
      <line x1={size / 2} x2={size / 2} y1={8} y2={size - 8} stroke={CHART.grid} strokeWidth={1} />
      <line x1={8} x2={size - 8} y1={size / 2} y2={size / 2} stroke={CHART.grid} strokeWidth={1} />
      <text x={size / 2} y={12} textAnchor="middle" fontSize={11} fill={CHART.ink2} fontWeight={600}>
        N
      </text>
      {/* The home */}
      <path d={`M${size / 2 - 7},${size / 2 + 6}V${size / 2 - 2}L${size / 2},${size / 2 - 8}L${size / 2 + 7},${size / 2 - 2}V${size / 2 + 6}Z`} fill="#f2b84b" stroke={CHART.surface} strokeWidth={2} />
      {amenities.map((a) => {
        const key = placeKey(a);
        const [e, n] = offset(twin, a);
        const on = key === active;
        return (
          <g key={key} onPointerEnter={() => onActive(key)} onPointerLeave={() => onActive(null)} onFocus={() => onActive(key)} onBlur={() => onActive(null)} tabIndex={0} role="img" aria-label={`${a.name}, ${a.category}, ${distance(a.distance_m)} away`} className="cursor-pointer outline-none">
            {/* Hit area larger than the dot */}
            <circle cx={px(e)} cy={py(n)} r={13} fill="transparent" />
            <circle cx={px(e)} cy={py(n)} r={on ? 7 : 5} fill={on ? CHART.ink : CHART.series1} stroke={CHART.surface} strokeWidth={2} />
            {on && (
              <text x={px(e)} y={py(n) - 12} textAnchor="middle" fontSize={11} fill={CHART.ink} fontWeight={600} paintOrder="stroke" stroke={CHART.surface} strokeWidth={4}>
                {a.name}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/**
 * Neighborhood: what is around the home, recent comparable sales, and aerial imagery.
 * All of it comes from the server's AreaProfile and the twin's assets.
 */
export function NeighborhoodTab({ twin }: { twin: PropertyTwin }) {
  const area = useArea(twin.property_id);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [image, setImage] = useState(0);

  // The profile lists the nearest few of each kind. A search also asks the server, which looks
  // further out and by name ("Kroger", "bus"); what it finds joins the list and the map.
  const [wider, setWider] = useState<Amenity[]>([]);
  const [searching, setSearching] = useState(false);
  const search = query.trim();
  useEffect(() => {
    setWider([]);
    if (search.length < 2 || api().mode !== 'http') return;
    let live = true;
    setSearching(true);
    const t = setTimeout(() => {
      api()
        .searchArea(twin.property_id, search)
        .then((found) => live && setWider(found))
        .catch(() => {})
        .finally(() => live && setSearching(false));
    }, 350);
    return () => {
      live = false;
      clearTimeout(t);
      setSearching(false);
    };
  }, [search, twin.property_id]);

  const categories = useMemo(() => [...new Set((area.data?.amenities ?? []).map((a) => a.category))].sort(), [area.data]);
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const local = (area.data?.amenities ?? []).filter((a) => (!category || a.category === category) && (!q || a.name.toLowerCase().includes(q) || a.category.toLowerCase().includes(q)));
    const seen = new Set(local.map(placeKey));
    const further = wider.filter((a) => (!category || a.category === category) && !seen.has(placeKey(a)));
    return [...local, ...further].sort((a, b) => a.distance_m - b.distance_m);
  }, [area.data, query, category, wider]);

  if (area.error) {
    return (
      <ViewState>
        <Notice tone="error" title="No area profile">
          {area.error.message}
        </Notice>
      </ViewState>
    );
  }
  if (!area.data) {
    return (
      <ViewState>
        <Spinner label="Loading the neighborhood…" />
      </ViewState>
    );
  }

  const images = [...(twin.assets.ortho ? [{ url: twin.assets.ortho, label: 'Top-down' }] : []), ...(twin.assets.obliques ?? []).map((url, i) => ({ url, label: `Oblique ${i + 1}` }))];
  const shown = images[Math.min(image, images.length - 1)];
  const listing = area.data.listing ?? null;
  const facts = listing ? listingFacts(listing) : [];
  const caveats = area.data.caveats ?? [];

  return (
    <div className="h-full overflow-y-auto p-5" data-testid="neighborhood" data-amenities={area.data.amenities.length} data-matches={matches.length}>
      <div className="mx-auto grid max-w-6xl gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        {listing && facts.length > 0 && (
          <section className="rounded-xl border border-line bg-surface p-5 lg:col-span-2" data-testid="listing">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <h2 className="font-display text-lg font-semibold">{twin.address}</h2>
              <span className="text-sm text-muted">
                {[listing.neighborhood, listing.subdivision].filter(Boolean).join(' · ')}
                {listing.status !== 'active' && <Badge className="ml-2">{listing.status.replace('_', ' ')}</Badge>}
              </span>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
              {facts.map((f) => (
                <div key={f.label}>
                  <dt className="text-xs text-muted">{f.label}</dt>
                  <dd className="font-medium tabular-nums">{f.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        <section className="rounded-xl border border-line bg-surface p-5">
          <div className="mb-3 flex items-baseline justify-between gap-3">
            <h2 className="text-sm font-medium">Around the home</h2>
            <span className="text-xs text-muted">
              {matches.length} of {area.data.amenities.length} places
            </span>
          </div>
          <AreaMap twin={twin} amenities={matches} active={active} onActive={setActive} />
          <TableView
            caption="Places around the home"
            columns={['Place', 'Category', 'Distance', 'Source']}
            rows={matches.map((a) => [a.name, a.category, distance(a.distance_m), a.source])}
          />
        </section>

        <section className="flex min-h-0 flex-col rounded-xl border border-line bg-surface p-5">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search nearby: grocery, park, school…"
            aria-label="Search nearby places"
            className="h-9 w-full rounded-md border border-line bg-ink px-3 text-sm placeholder:text-faint focus:border-line-strong focus:outline-none"
          />
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {categories.map((c) => (
              <button
                key={c}
                type="button"
                aria-pressed={c === category}
                onClick={() => setCategory(c === category ? null : c)}
                className={cn('rounded-full border px-2.5 py-0.5 text-xs', c === category ? 'border-sun/50 bg-sun/10 text-sun-deep' : 'border-line text-muted hover:text-text')}
              >
                {c}
              </button>
            ))}
          </div>
          <ul className="mt-3 max-h-[420px] flex-1 space-y-1 overflow-y-auto" data-testid="amenity-list">
            {matches.map((a) => {
              const key = placeKey(a);
              return (
                <li
                  key={key}
                  onPointerEnter={() => setActive(key)}
                  onPointerLeave={() => setActive(null)}
                  className={cn('flex items-center justify-between gap-3 rounded-md px-2.5 py-2 text-sm', key === active ? 'bg-raised' : '')}
                >
                  <span className="min-w-0">
                    <span className="block truncate">{a.name}</span>
                    <span className="text-xs text-muted">
                      {a.category} · {a.source}
                    </span>
                  </span>
                  <span className="shrink-0 font-medium tabular-nums">{distance(a.distance_m)}</span>
                </li>
              );
            })}
            {matches.length === 0 && (
              <li className="px-2.5 py-6 text-center text-sm text-muted">{searching ? 'Looking further out…' : search ? `Nothing nearby matches “${search}”.` : 'Nothing nearby matches.'}</li>
            )}
          </ul>
        </section>

        {shown && (
        <section className="rounded-xl border border-line bg-surface p-5">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-sm font-medium">From above</h2>
            <div className="flex flex-wrap gap-1.5">
              {images.map((img, i) => (
                <button key={img.url} type="button" aria-pressed={i === image} onClick={() => setImage(i)} className={cn('rounded-md border px-2 py-0.5 text-xs', i === image ? 'border-sun/50 bg-sun/10 text-sun-deep' : 'border-line text-muted hover:text-text')}>
                  {img.label}
                </button>
              ))}
            </div>
          </div>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={shown.url} alt={`${shown.label} aerial image of the property`} className="aspect-square w-full rounded-lg border border-line object-cover" data-testid="aerial" />
          {twin.assets.ortho && (twin.assets.obliques ?? []).length === 0 && <p className="mt-2 text-xs text-faint">No oblique views for this property yet.</p>}
        </section>
        )}

        {/* With no picture beside it, the summary takes the full width. */}
        <section className={cn('rounded-xl border border-line bg-surface p-5', !shown && 'lg:col-span-2')}>
          <Markdown text={area.data.markdown} />
          {caveats.length > 0 && (
            <ul className="mt-4 space-y-1 border-t border-line pt-3 text-xs leading-5 text-faint">
              {caveats.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-xl border border-line bg-surface p-5 lg:col-span-2">
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-sm font-medium">Comparable sales</h2>
            <Badge>{area.data.comps.length} nearby</Badge>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] border-collapse text-left text-sm" data-testid="comps">
              <thead className="text-xs text-muted">
                <tr>
                  {['Address', 'Price', 'Size', 'Beds', 'Sold', 'Distance'].map((h, i) => (
                    <th key={h} scope="col" className={cn('border-b border-line px-2 py-2 font-medium', i > 0 && 'text-right')}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {area.data.comps.map((c) => (
                  <tr key={`${c.address}|${c.price}`} className="border-b border-line/60">
                    <td className="px-2 py-2">{c.address}</td>
                    <td className="px-2 py-2 text-right font-medium">{money(c.price)}</td>
                    <td className="px-2 py-2 text-right text-muted">{c.sqft ? `${c.sqft.toLocaleString('en-US')} sq ft` : '–'}</td>
                    <td className="px-2 py-2 text-right text-muted">{c.beds ?? '–'}</td>
                    <td className="px-2 py-2 text-right text-muted">{c.sold_date ?? 'not sold'}</td>
                    <td className="px-2 py-2 text-right text-muted">{distance(c.distance_m)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}
