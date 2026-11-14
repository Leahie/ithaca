'use client';

import type { PropertyTwin, ViewerMode } from '@ithaca/schema';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { HandsButton, HandsOverlay } from '@/components/hands/HandControl';
import { SessionProvider, useSessionInfo } from '@/components/session/SessionProvider';
import { Badge, Notice, Spinner, ViewState, cn } from '@/components/ui';
import { IconDesign, IconMap, IconSound, IconSun, IconTour, IconUser } from '@/components/ui/icons';
import { ApiError } from '@/lib/api';
import { config, type Feature } from '@/lib/config';
import { useLayouts, useTwin } from '@/lib/data/hooks';
import { useHands } from '@/lib/hands/store';
import { useSession } from '@/lib/store/session';
import { sunPosition } from '@/lib/sun/position';
import { fromZonedParts, toZonedIso, zonedParts } from '@/lib/time';
import { AgentDock } from './AgentDock';
import { HouseLoader } from './HouseLoader';
import { PairingButton } from './PairingButton';
import { RoomRail } from './RoomRail';

const loading = () => (
  <ViewState>
    <Spinner label="Loading view…" />
  </ViewState>
);
// The tour's code loads behind the same screen the tour then shows while it starts.
const TourTab = dynamic(() => import('@/components/tabs/TourTab').then((m) => m.TourTab), { ssr: false, loading: () => <HouseLoader label="Starting the tour…" /> });
const SunLabTab = dynamic(() => import('@/components/tabs/SunLabTab').then((m) => m.SunLabTab), { ssr: false, loading });
const SoundTab = dynamic(() => import('@/components/tabs/SoundTab').then((m) => m.SoundTab), { ssr: false, loading });
const NeighborhoodTab = dynamic(() => import('@/components/tabs/NeighborhoodTab').then((m) => m.NeighborhoodTab), { ssr: false, loading });
const DesignTab = dynamic(() => import('@/components/tabs/DesignTab').then((m) => m.DesignTab), { ssr: false, loading });

const TABS: { mode: ViewerMode; label: string; icon: typeof IconTour }[] = [
  { mode: 'tour', label: 'Tour', icon: IconTour },
  { mode: 'sun', label: 'Sun Lab', icon: IconSun },
  { mode: 'sound', label: 'Sound', icon: IconSound },
  { mode: 'neighborhood', label: 'Neighborhood', icon: IconMap },
  { mode: 'design', label: 'Design', icon: IconDesign },
];

/** Caveats the twin carries when a data source was missing (C3). */
function QualityBadges({ twin }: { twin: PropertyTwin }) {
  const q = twin.quality;
  return (
    <>
      {q.alignment === 'gps_only' && (
        <Badge tone="sun" title="The twin is placed by GPS only; its compass heading is not yet refined against the building footprint. Sun directions may be rotated.">
          Compass: GPS only
        </Badge>
      )}
      {q.openings === 'none' && (
        <Badge tone="sun" title="No windows or doors are marked yet. Annotate them in the Sun Lab.">
          No openings
        </Badge>
      )}
      {q.context === 'none' && <Badge title="No terrain or neighbor data: nothing outside the house casts shadows.">No surroundings</Badge>}
    </>
  );
}

const has = (f: Feature) => config.features.has(f);
/** Views that are switched on, in tab order. The page shows no tab bar when there is one. */
const VIEWS = TABS.filter((t) => has(t.mode));
/** The session (agent, phone, saved choices) is only needed when something uses it. */
const NEEDS_SESSION = has('agent') || has('phone') || has('design');

function House({ propertyId }: { propertyId: string }) {
  const twin = useTwin(propertyId);
  const layouts = useLayouts(has('design') ? propertyId : null);
  const stateMode = useSession((s) => s.viewer.mode);
  // A view that is switched off cannot be shown, whatever the session says.
  const mode: ViewerMode = has(stateMode) ? stateMode : (VIEWS[0]?.mode ?? 'tour');
  const dispatch = useSession((s) => s.dispatch);
  const { error: sessionError } = useSessionInfo();
  const [visited, setVisited] = useState<Set<ViewerMode>>(() => new Set());
  const [dockOpen, setDockOpen] = useState(has('agent'));
  const initialised = useRef<string | null>(null);

  // Values that come from the twin, not from a user action.
  useEffect(() => {
    if (!twin.data || initialised.current === twin.data.property_id) return;
    initialised.current = twin.data.property_id;
    let now = new Date();
    now.setSeconds(0, 0);
    // Opening the app at night would show a dark house: start at 10 am of the same day instead.
    if (sunPosition(now, twin.data.lat, twin.data.lon).elevation_deg < 5) {
      now = fromZonedParts({ ...zonedParts(now, twin.data.timezone), hour: 10, minute: 0, second: 0 }, twin.data.timezone);
    }
    useSession.getState().init({
      viewer: { datetime: toZonedIso(now, twin.data.timezone) },
      sweepRooms: Object.fromEntries(twin.data.sweeps.map((s) => [s.sweep_id, s.room_id ?? null])),
    });
  }, [twin.data]);

  // A layout the user chose earlier is the selected one until they pick another.
  useEffect(() => {
    const chosen = layouts.data?.find((l) => l.status === 'chosen');
    if (chosen && !useSession.getState().viewer.selected_layout_id) useSession.getState().init({ viewer: { selected_layout_id: chosen.layout_id } });
  }, [layouts.data]);

  useEffect(() => {
    setVisited((v) => (v.has(mode) ? v : new Set(v).add(mode)));
  }, [mode]);

  // Leaving the page turns the camera off.
  useEffect(() => () => useHands.getState().stop(), []);

  if (twin.error) {
    const notFound = twin.error instanceof ApiError && twin.error.status === 404;
    return (
      <ViewState>
        <Notice tone="error" title={notFound ? `No twin for property "${propertyId}"` : 'Could not load this home'}>
          {twin.error.message}
          {!config.useFixtures && ' Start the API, or set NEXT_PUBLIC_USE_FIXTURES=1 to work offline.'}
        </Notice>
      </ViewState>
    );
  }
  if (!twin.data) return <HouseLoader label="Loading the home…" className="h-dvh" />;
  const t = twin.data;

  return (
    <div className="flex h-dvh flex-col" data-testid="house" data-mode={mode} data-features={[...config.features].join(' ')}>
      <header className="flex h-16 shrink-0 items-center gap-4 border-b border-line bg-surface px-4">
        <Link href="/" className="flex items-center gap-2 font-display text-lg font-semibold">
          <span className="h-3 w-3 rounded-full bg-sun shadow-[0_0_0_1.5px_var(--color-main)]" />
          Ithaca
        </Link>
        <div className="hidden min-w-0 items-center gap-2 lg:flex">
          <span className="truncate text-sm text-muted">{t.address}</span>
          {(has('sun') || has('sound')) && <QualityBadges twin={t} />}
          {config.useFixtures && <Badge tone="sky" title="NEXT_PUBLIC_USE_FIXTURES=1: data comes from the fixtures folder and the agent is scripted.">Fixture mode</Badge>}
        </div>
        {VIEWS.length > 1 ? (
          <nav aria-label="Views" className="mx-auto flex gap-1 rounded-full bg-ink p-1" role="tablist">
            {VIEWS.map(({ mode: m, label, icon: Icon }) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={m === mode}
                data-tab={m}
                onClick={() => dispatch('viewer.setMode', { mode: m })}
                className={cn(
                  'flex h-10 items-center gap-2 rounded-full px-4 font-display text-base font-semibold transition-colors',
                  m === mode ? 'bg-raised text-text shadow-[0_2px_0_var(--color-line)]' : 'text-muted hover:text-text',
                )}
              >
                <Icon />
                <span className="hidden md:inline">{label}</span>
              </button>
            ))}
          </nav>
        ) : (
          <div className="mx-auto" />
        )}
        <div className="flex items-center gap-1">
          {has('hands') && <HandsButton />}
          {has('phone') && <PairingButton propertyId={propertyId} />}
          {has('me') && (
            <Link href="/me" className="inline-flex h-10 items-center gap-1.5 rounded-full px-3.5 font-display font-semibold text-muted hover:bg-ink hover:text-text">
              <IconUser />
              <span className="hidden xl:inline">Me</span>
            </Link>
          )}
          {has('agent') && (
            <button type="button" onClick={() => setDockOpen(!dockOpen)} aria-pressed={dockOpen} className="inline-flex h-10 items-center rounded-full px-3.5 font-display font-semibold text-muted hover:bg-ink hover:text-text">
              {dockOpen ? 'Hide agent' : 'Agent'}
            </button>
          )}
        </div>
      </header>

      {sessionError && (
        <div className="border-b border-line p-2">
          <Notice tone="warn" title="No live session">
            {sessionError}. The viewer works, but the agent and the phone are not connected.
          </Notice>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {mode !== 'neighborhood' && <RoomRail twin={t} tour={mode === 'tour'} />}
        <main className="relative min-w-0 flex-1">
          {/* The two 3D views stay mounted once opened: reloading them is slow. An inactive one is
              faded out, not display:none, so its canvas keeps its size. */}
          {has('tour') && (visited.has('tour') || mode === 'tour') && (
            <div className={cn('absolute inset-0', mode === 'tour' ? 'z-10' : 'pointer-events-none z-0 opacity-0')} aria-hidden={mode !== 'tour'} inert={mode !== 'tour'}>
              <TourTab twin={t} active={mode === 'tour'} />
            </div>
          )}
          {has('sun') && (visited.has('sun') || mode === 'sun') && (
            <div className={cn('absolute inset-0', mode === 'sun' ? 'z-10' : 'pointer-events-none z-0 opacity-0')} aria-hidden={mode !== 'sun'} inert={mode !== 'sun'}>
              <SunLabTab twin={t} active={mode === 'sun'} />
            </div>
          )}
          {(mode === 'sound' || mode === 'neighborhood' || mode === 'design') && (
            <div className="absolute inset-0 z-10 bg-surface">
              {mode === 'sound' && <SoundTab twin={t} />}
              {mode === 'neighborhood' && <NeighborhoodTab twin={t} />}
              {mode === 'design' && <DesignTab twin={t} />}
            </div>
          )}
          {has('hands') && <HandsOverlay />}
        </main>
        {has('agent') && dockOpen && <AgentDock />}
      </div>
    </div>
  );
}

export function HouseExperience({ propertyId }: { propertyId: string }) {
  if (!NEEDS_SESSION) return <House propertyId={propertyId} />;
  return (
    <SessionProvider client="desktop" propertyId={propertyId}>
      <House propertyId={propertyId} />
    </SessionProvider>
  );
}
