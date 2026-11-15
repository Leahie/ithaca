'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import type { ReactNode } from 'react';
import { OpenPropertyForm } from '@/app/OpenPropertyForm';
import { PlanSvg } from '@/components/house/HousePlan';
import { Notice } from '@/components/ui';
import { IconDesign, IconMap, IconPeople, IconSound, IconSun, IconTour, IconUser } from '@/components/ui/icons';
import { config } from '@/lib/config';
import { useArea, useTwin } from '@/lib/data/hooks';
import { houseHref } from '@/lib/lastHouse';
import { listingFacts } from '@/lib/listing';
import { roomArea } from '@/lib/rooms';

const has = (f: Parameters<typeof config.features.has>[0]) => config.features.has(f);

/** What a visit offers. Each entry is shown only when that part of the app is switched on. */
const OFFERS: { on: boolean; icon: typeof IconTour; title: string; text: string }[] = [
  {
    on: has('tour'),
    icon: IconTour,
    title: 'Walk through it',
    text: 'Every room at eye height, rebuilt from the scan and the floor plan. Walk with the keyboard, click where you want to stand, or step outside and look at the house from the street.',
  },
  {
    on: has('tour'),
    icon: IconSun,
    title: 'Watch the sun move',
    text: 'Pick any day of the year and any hour. See where the light falls in each room, with the neighbours’ houses and trees casting their real shadows.',
  },
  {
    on: has('sound'),
    icon: IconSound,
    title: 'Hear what it sounds like',
    text: 'The highway, the street, birds and frogs, hour by hour. Open a window, add carpet, or put a TV in the living room and listen from the bedroom.',
  },
  {
    on: has('neighborhood'),
    icon: IconMap,
    title: 'Know the neighbourhood',
    text: 'The asking price next to comparable homes nearby, and how far it is to the grocery store, the school, the park and the transit stop.',
  },
  {
    on: has('design') || has('agent'),
    icon: IconDesign,
    title: 'Move your furniture in',
    text: 'Photograph a piece you own and it is rebuilt in 3D at its real size. Arrange a room by hand, or ask the guide for layouts and keep the one you like.',
  },
  {
    on: has('family'),
    icon: IconPeople,
    title: 'Bring the family',
    text: 'One code on the big screen. Each person joins from their own phone and gets their own guide, which remembers what matters to them and compares notes with the others.',
  },
];

const STEPS = [
  { title: 'Open the home', text: 'On a laptop or a TV. The whole house loads in the browser: nothing to install.' },
  { title: 'Connect your phones', text: 'Press Family, scan the code, type your name. Your phone becomes the remote, and the big screen shows only the house.' },
  { title: 'Ask, walk, arrange', text: 'Talk to your guide or use the phone’s controls. What each of you likes and dislikes is remembered for the next home.' },
];

function Brand({ className }: { className?: string }) {
  return (
    <span className={`flex items-center gap-2 font-display font-semibold ${className ?? ''}`}>
      <span className="h-3 w-3 rounded-full bg-sun shadow-[0_0_0_1.5px_var(--color-main)]" />
      Ithaca
    </span>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-white/60">{label}</dt>
      <dd className="font-medium text-white tabular-nums">{children}</dd>
    </div>
  );
}

/**
 * The front page: what Ithaca is, the home that is open for a visit, and the way in. The home's
 * card is drawn from the same twin and area profile the house page uses, so it shows a home that
 * is really there (or says why it is not).
 */
export function HomePage({ propertyId }: { propertyId: string }) {
  const router = useRouter();
  const twin = useTwin(propertyId || null);
  const area = useArea(propertyId || null);
  const href = houseHref(propertyId);
  const t = twin.data;
  const listing = area.data?.listing ?? null;
  // The price and the size lead; the rest of the listing is on the Neighborhood view.
  const facts = listing ? listingFacts(listing).filter((f) => ['Asking price', 'Last asking price', 'Sold for', 'Bedrooms', 'Bathrooms', 'Size'].includes(f.label)) : [];
  const ground = t ? [...t.floors].sort((a, b) => a.index - b.index)[0] : undefined;
  const floorArea = t ? t.rooms.reduce((sum, r) => sum + roomArea(r), 0) : 0;

  return (
    <div className="min-h-dvh" data-testid="home">
      <header className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5">
        <Brand className="text-lg" />
        <nav className="flex items-center gap-1" aria-label="Site">
          {has('me') && (
            <Link href="/me" className="inline-flex h-10 items-center gap-1.5 rounded-full px-3.5 font-display font-semibold text-muted hover:bg-ink hover:text-text">
              <IconUser />
              Your furniture
            </Link>
          )}
          {propertyId && (
            <Link href={href} className="inline-flex h-10 items-center rounded-full bg-main px-4 font-display font-semibold text-on-main hover:bg-main/90">
              Open the home
            </Link>
          )}
        </nav>
      </header>

      <main className="mx-auto max-w-6xl px-5 pb-20">
        <section className="grid items-center gap-10 py-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:py-16">
          <div>
            <h1 className="text-4xl leading-[1.08] sm:text-5xl">Live in a home before you buy it.</h1>
            <p className="mt-5 max-w-xl text-lg leading-8 text-muted">
              Walk through the house with your own furniture in it. See where the sun falls in December, hear the street at rush hour, and ask a guide who remembers what your family needs.
            </p>
            {propertyId ? (
              <div className="mt-8 flex flex-wrap items-center gap-3">
                <Link href={href} data-testid="enter-home" className="inline-flex h-12 items-center rounded-full bg-main px-6 font-display text-base font-semibold text-on-main shadow-ledge hover:bg-main/90">
                  Step inside
                </Link>
                <a href="#how" className="inline-flex h-12 items-center rounded-full px-4 font-display text-base font-semibold text-muted hover:bg-ink hover:text-text">
                  How a visit works
                </a>
              </div>
            ) : (
              <div className="mt-8 max-w-md space-y-2">
                <p className="text-sm text-muted">Open a home by its id.</p>
                <OpenPropertyForm />
              </div>
            )}
          </div>

          {/* The home that is open for a visit: its ground floor, as the house page draws it. */}
          {propertyId && (
            <div className="overflow-hidden rounded-3xl bg-main text-white shadow-float" data-testid="home-card">
              <div className="aspect-[16/10] p-5">
                {t && ground ? (
                  <PlanSvg twin={t} floorId={ground.floor_id} roomId={null} onPick={() => router.push(href)} className="h-full w-full" />
                ) : (
                  <div className="flex h-full items-center justify-center text-sm text-white/60">{twin.error ? 'The floor plan could not be loaded.' : 'Drawing the floor plan…'}</div>
                )}
              </div>
              <div className="border-t border-white/15 p-5">
                {twin.error ? (
                  <p className="text-sm leading-6 text-coral">
                    This home could not be loaded: {twin.error.message}
                    {!config.useFixtures && ' Is the API running?'}
                  </p>
                ) : (
                  <>
                    <div className="font-display text-lg font-semibold">{t?.address ?? 'Loading the home…'}</div>
                    {listing?.neighborhood && <div className="text-sm text-white/60">{listing.neighborhood}</div>}
                    <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
                      {facts.map((f) => (
                        <Fact key={f.label} label={f.label}>
                          {f.value}
                        </Fact>
                      ))}
                      {t && (
                        <>
                          <Fact label="Rooms">{t.rooms.length}</Fact>
                          <Fact label="Floors">{t.floors.length}</Fact>
                          {!facts.some((f) => f.label === 'Size') && <Fact label="Floor area">{floorArea.toFixed(0)} m²</Fact>}
                        </>
                      )}
                    </dl>
                    <Link href={href} className="mt-5 inline-flex h-10 items-center rounded-full bg-leaf/25 px-4 text-sm font-semibold text-white shadow-[inset_0_0_0_1.5px_rgb(43_220_134/0.6)] hover:bg-leaf/35">
                      Step inside
                    </Link>
                  </>
                )}
              </div>
            </div>
          )}
        </section>

        <section aria-labelledby="offers">
          <h2 id="offers" className="font-display text-2xl font-semibold">
            What you can find out
          </h2>
          <ul className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {OFFERS.filter((o) => o.on).map(({ icon: Icon, title, text }) => (
              <li key={title} className="rounded-[var(--radius-card)] border border-line bg-raised p-5">
                <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-leaf/15 text-leaf-deep">
                  <Icon width={20} height={20} />
                </span>
                <h3 className="mt-4 font-display text-base font-semibold">{title}</h3>
                <p className="mt-1.5 text-[15px] leading-6 text-muted">{text}</p>
              </li>
            ))}
          </ul>
        </section>

        <section id="how" aria-labelledby="how-title" className="mt-16 scroll-mt-6">
          <h2 id="how-title" className="font-display text-2xl font-semibold">
            How a visit works
          </h2>
          <ol className="mt-6 grid gap-4 md:grid-cols-3">
            {STEPS.filter((_, i) => i !== 1 || has('family') || has('phone')).map((s, i) => (
              <li key={s.title} className="rounded-[var(--radius-card)] border border-line bg-surface p-5">
                <span className="font-mono text-sm text-muted">{i + 1}</span>
                <h3 className="mt-2 font-display text-base font-semibold">{s.title}</h3>
                <p className="mt-1.5 text-[15px] leading-6 text-muted">{s.text}</p>
              </li>
            ))}
          </ol>
          {config.useFixtures && (
            <div className="mt-6">
              <Notice title="Fixture mode">This copy runs from recorded data with a scripted guide. Start the API for the real guide, family phones and photo-to-furniture.</Notice>
            </div>
          )}
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-6 text-sm text-muted">
          <Brand />
          <span>Sun, sound and layout figures are simulations from the home’s scan and public data. Treat them as estimates.</span>
        </div>
      </footer>
    </div>
  );
}
