'use client';

import type { PropertyTwin, ViewerMode } from '@ithaca/schema';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { FurnitureMenu } from '@/components/design/FurnitureMenu';
import { HandsButton, HandsOverlay } from '@/components/hands/HandControl';
import { DesktopSession } from '@/components/session/DesktopSession';
import { useSessionInfo } from '@/components/session/SessionProvider';
import { Badge, Button, Notice, Spinner, ViewState, cn } from '@/components/ui';
import { IconDesign, IconMap, IconPeople, IconSofa, IconSound, IconTour, IconUser } from '@/components/ui/icons';
import { ApiError } from '@/lib/api';
import { config, type Feature } from '@/lib/config';
import { useLayouts, useTwin } from '@/lib/data/hooks';
import { useHands } from '@/lib/hands/store';
import { rememberHouse } from '@/lib/lastHouse';
import { sweepRooms } from '@/lib/rooms';
import { useSpeakGuide } from '@/lib/speech/useSpeakGuide';
import { useGuide, useGuideVoice } from '@/lib/store/guide';
import { onSessionEvent, useSession } from '@/lib/store/session';
import { useDesktopPlayback } from '@/lib/store/usePlayback';
import { sunPosition } from '@/lib/sun/position';
import { fromZonedParts, toZonedIso, zonedParts } from '@/lib/time';
import { AgentDock } from './AgentDock';
import { ArrangePanel } from './ArrangePanel';
import { HouseLoader } from './HouseLoader';
import { FamilyButton } from './FamilyButton';
import { PairingButton } from './PairingButton';
import { CleanOverlay } from './CleanOverlay';
import { RoomRail } from './RoomRail';

const loading = () => (
  <ViewState>
    <Spinner label="Loading view…" />
  </ViewState>
);
// The tour's code loads behind the same screen the tour then shows while it starts.
const TourTab = dynamic(() => import('@/components/tabs/TourTab').then((m) => m.TourTab), { ssr: false, loading: () => <HouseLoader label="Starting the tour…" /> });
const SoundTab = dynamic(() => import('@/components/tabs/SoundTab').then((m) => m.SoundTab), { ssr: false, loading });
const NeighborhoodTab = dynamic(() => import('@/components/tabs/NeighborhoodTab').then((m) => m.NeighborhoodTab), { ssr: false, loading });
const DesignTab = dynamic(() => import('@/components/tabs/DesignTab').then((m) => m.DesignTab), { ssr: false, loading });
const FamilyTab = dynamic(() => import('@/components/tabs/FamilyTab').then((m) => m.FamilyTab), { ssr: false, loading });

const TABS: { mode: ViewerMode; label: string; icon: typeof IconTour }[] = [
  { mode: 'tour', label: 'Tour', icon: IconTour },
  { mode: 'sound', label: 'Sound', icon: IconSound },
  { mode: 'neighborhood', label: 'Neighborhood', icon: IconMap },
  { mode: 'design', label: 'Design', icon: IconDesign },
  { mode: 'family', label: 'Family', icon: IconPeople },
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
        <Badge tone="sun" title="No windows or doors are marked for this home yet, so no sunlight comes into the rooms.">
          No openings
        </Badge>
      )}
      {/* The rebuilt house comes with its lot and the block around it, whatever the twin says. */}
      {q.context === 'none' && !twin.assets.house && <Badge title="No terrain or neighbor data: nothing outside the house casts shadows.">No surroundings</Badge>}
    </>
  );
}

const has = (f: Feature) => config.features.has(f);
/** Views that are switched on, in tab order. The page shows no tab bar when there is one. */
const VIEWS = TABS.filter((t) => has(t.mode));
/** The session (agent, phone, saved choices) is only needed when something uses it. */
const NEEDS_SESSION = has('agent') || has('phone') || has('design') || has('family');

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
  const [arranging, setArranging] = useState(false);
  // The furniture menu (the library, a whole room in a look, storage, photos) belongs to the views
  // that show furniture. It shuts when another view takes over, so it is not there on the way back.
  const [furnitureOpen, setFurnitureOpen] = useState(false);
  const canFurnish = (has('design') || has('agent')) && (mode === 'tour' || mode === 'design');
  useEffect(() => {
    if (!canFurnish) setFurnitureOpen(false);
  }, [canFurnish]);
  // The big screen is only the house: the top bar, the room list and the chat dock stay away until
  // "Controls" is pressed (lib/store/guide.ts). Opening the house again starts without them.
  const screen = useGuide((s) => s.screen);
  const setScreen = useGuide((s) => s.setScreen);
  useEffect(() => setScreen('clean'), [propertyId, setScreen]);
  const phoneHere = useSession((s) => s.peers.phone);
  const canClean = (has('family') || has('phone')) && mode === 'tour';
  const clean = canClean && (screen === 'clean' || (screen === 'auto' && phoneHere));
  // The guide's voice belongs to the screen, not to one view: it keeps talking (and telling the
  // phones when it has finished) whichever tab is open and whether or not the top bar shows.
  const [voice] = useGuideVoice();
  useSpeakGuide(has('agent') && voice);
  const initialised = useRef<string | null>(null);
  useDesktopPlayback(twin.data?.timezone);

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
      sweepRooms: sweepRooms(twin.data),
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

  useEffect(() => rememberHouse(propertyId), [propertyId]);

  // A phone that walks, picks a room or arranges furniture means the house, not a page about it:
  // if the guide (or someone at this screen) left another view open, go back to the tour, so a
  // family steering from their phones is never stuck looking at a chart. The desktop derives
  // the event (A2), so every screen and the guide see the same mode.
  useEffect(
    () =>
      onSessionEvent((event) => {
        if (event.source !== 'phone') return;
        // Picking up a piece or asking for the room from above counts; letting go (null) does not.
        const moves =
          event.type === 'viewer.step' ||
          event.type === 'viewer.walk' ||
          ((event.type === 'furniture.select' || event.type === 'furniture.drag') && !!event.payload.item_id) ||
          (event.type === 'furniture.overview' && !!event.payload.room_id);
        const picksRoom = event.type === 'viewer.goto';
        if (!moves && !picksRoom) return;
        if (!has('tour')) return;
        const now = useSession.getState().viewer.mode;
        // Walking and arranging happen in the tour. A room picked on the phone also moves the
        // Sound view's ear, so that view stays. The family board is a page, like the other two.
        if (moves ? now !== 'tour' : now === 'neighborhood' || now === 'design' || now === 'family') useSession.getState().dispatch('viewer.setMode', { mode: 'tour' });
      }),
    [],
  );

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
      <header className={cn('flex h-16 shrink-0 items-center gap-4 border-b border-line bg-surface px-4', clean && 'hidden')}>
        <Link href="/" className="flex items-center gap-2 font-display text-lg font-semibold">
          <span className="h-3 w-3 rounded-full bg-sun shadow-[0_0_0_1.5px_var(--color-main)]" />
          Ithaca
        </Link>
        <div className="hidden min-w-0 items-center gap-2 lg:flex">
          <span className="truncate text-sm text-muted">{t.address}</span>
          {(has('tour') || has('sound')) && <QualityBadges twin={t} />}
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
                aria-label={label}
                title={label}
                data-tab={m}
                onClick={() => dispatch('viewer.setMode', { mode: m })}
                className={cn(
                  'flex h-10 items-center gap-2 rounded-full px-4 font-display text-base font-semibold transition-colors',
                  m === mode ? 'bg-raised text-text shadow-[0_2px_0_var(--color-line)]' : 'text-muted hover:text-text',
                )}
              >
                <Icon />
                <span className={m === mode ? 'hidden sm:inline' : 'hidden 2xl:inline'}>{label}</span>
              </button>
            ))}
          </nav>
        ) : (
          <div className="mx-auto" />
        )}
        <div className="flex items-center gap-1">
          {/* The one solid button of the bar, its word always showing: nobody should have to find the furniture. */}
          {canFurnish && (
            <Button variant="primary" onClick={() => setFurnitureOpen(true)} aria-haspopup="dialog" data-testid="furniture-open" title="Browse the furniture library, furnish this room in a look, or add your own" className="mr-1">
              <IconSofa width={18} height={18} />
              Furniture
            </Button>
          )}
          {(has('design') || has('agent')) && mode === 'tour' && (
            <button type="button" onClick={() => setArranging(!arranging)} aria-pressed={arranging} className="inline-flex h-10 items-center rounded-full px-3.5 font-display font-semibold text-muted hover:bg-ink hover:text-text">
              {arranging ? 'Done arranging' : 'Arrange'}
            </button>
          )}
          {canClean && (
            <button type="button" onClick={() => setScreen('clean')} title="Show only the house. The phone is the remote." className="hidden h-10 items-center rounded-full px-3.5 font-display font-semibold text-muted hover:bg-ink hover:text-text lg:inline-flex">
              Hide controls
            </button>
          )}
          {has('hands') && <HandsButton />}
          {/* The family code covers one phone too, so it replaces single-phone pairing. */}
          {has('family') ? <FamilyButton propertyId={propertyId} /> : has('phone') && <PairingButton propertyId={propertyId} />}
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
        {mode !== 'neighborhood' && mode !== 'family' && !clean && <RoomRail twin={t} tour={mode === 'tour'} />}
        <main className="relative min-w-0 flex-1">
          {/* The tour stays mounted once opened: reloading it is slow. While another view is open it
              is faded out, not display:none, so its canvas keeps its size. */}
          {has('tour') && (visited.has('tour') || mode === 'tour') && (
            <div className={cn('absolute inset-0', mode === 'tour' ? 'z-10' : 'pointer-events-none z-0 opacity-0')} aria-hidden={mode !== 'tour'} inert={mode !== 'tour'}>
              <TourTab twin={t} active={mode === 'tour'} soundBar={!clean && !arranging} />
            </div>
          )}
          {(mode === 'sound' || mode === 'neighborhood' || mode === 'design' || mode === 'family') && (
            <div className="absolute inset-0 z-10 bg-surface">
              {mode === 'sound' && <SoundTab twin={t} />}
              {mode === 'neighborhood' && <NeighborhoodTab twin={t} />}
              {mode === 'design' && <DesignTab twin={t} />}
              {mode === 'family' && <FamilyTab twin={t} />}
            </div>
          )}
          {arranging && mode === 'tour' && <ArrangePanel twin={t} onClose={() => setArranging(false)} onAddFurniture={() => setFurnitureOpen(true)} />}
          {(has('design') || has('agent')) && (
            <FurnitureMenu
              twin={t}
              open={furnitureOpen && canFurnish}
              overHouse={mode === 'tour'}
              onOpen={() => setFurnitureOpen(true)}
              onClose={() => setFurnitureOpen(false)}
              onFurnished={() => {
                // The options to keep are in the room now, and on this screen the Arrange panel is what keeps one.
                setFurnitureOpen(false);
                if (mode !== 'tour' && has('tour')) dispatch('viewer.setMode', { mode: 'tour' });
                setArranging(true);
              }}
            />
          )}
          {has('hands') && <HandsOverlay />}
          {clean && (
            <CleanOverlay twin={t} onShowControls={() => setScreen('controls')} onFurniture={canFurnish ? () => setFurnitureOpen(true) : undefined}
              pairing={has('family') ? <FamilyButton propertyId={propertyId} avatar /> : <PairingButton propertyId={propertyId} avatar />} />
          )}
        </main>
        {has('agent') && dockOpen && !clean && <AgentDock propertyId={propertyId} />}
      </div>
    </div>
  );
}

export function HouseExperience({ propertyId }: { propertyId: string }) {
  if (!NEEDS_SESSION) return <House propertyId={propertyId} />;
  return (
    <DesktopSession propertyId={propertyId} fallback={<HouseLoader label="Loading the home…" className="h-dvh" />}>
      <House propertyId={propertyId} />
    </DesktopSession>
  );
}
