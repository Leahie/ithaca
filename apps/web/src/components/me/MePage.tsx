'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { DesktopSession } from '@/components/session/DesktopSession';
import { Badge, Segmented } from '@/components/ui';
import { config } from '@/lib/config';
import { houseHref, lastHouse } from '@/lib/lastHouse';
import { HistorySection } from './HistorySection';
import { InventorySection } from './InventorySection';
import { MemorySection } from './MemorySection';
import { MyRoomScan } from './MyRoomScan';
import { ResetEverything } from './ResetEverything';

type Section = 'inventory' | 'memory' | 'history';

/** /me: what the agent knows about you, and everything you can change or delete. */
export function MePage() {
  const [section, setSection] = useState<Section>('inventory');
  // The house this tab came from (read after mount: the server has no sessionStorage).
  const [houseId, setHouseId] = useState(config.defaultPropertyId);
  useEffect(() => setHouseId(lastHouse()), []);
  return (
    // The house's own session is joined (not a new one, which would leave the family's phones
    // behind), so that object-pipeline progress (job.progress) reaches this page.
    <DesktopSession propertyId={houseId}>
      <div className="min-h-dvh" data-testid="me">
        <header className="sticky top-0 z-20 flex h-13 items-center gap-4 border-b border-line bg-surface/95 px-5 backdrop-blur">
          <Link href="/" className="flex items-center gap-2 text-sm font-semibold tracking-wide">
            <span className="h-2.5 w-2.5 rounded-full bg-sun" />
            Ithaca
          </Link>
          <span className="text-sm text-muted">Me</span>
          {config.useFixtures && <Badge tone="sky">Fixture mode</Badge>}
          <Segmented<Section>
            className="ml-auto"
            label="Section"
            value={section}
            onChange={setSection}
            options={[
              { value: 'inventory', label: 'Furniture' },
              { value: 'memory', label: 'Memory' },
              { value: 'history', label: 'History' },
            ]}
          />
          <Link href={houseHref(houseId)} className="text-sm text-muted hover:text-text">
            Back to the home
          </Link>
          <ResetEverything />
        </header>
        <main className="mx-auto max-w-6xl p-5">
          <div className="mb-5">
            <h1 className="text-xl font-semibold">{section === 'inventory' ? 'Your furniture' : section === 'memory' ? 'What the agent remembers' : 'Homes and decisions'}</h1>
            <p className="mt-1 text-sm text-muted">
              {section === 'inventory'
                ? 'Pieces you own or are considering. The agent places these in every home you view.'
                : section === 'memory'
                  ? 'Plain files, kept per user. Read them, correct them, or delete them.'
                  : 'Every home you viewed, with the notes kept on it and the layouts you chose or rejected.'}
            </p>
          </div>
          {section === 'inventory' && (
            <div className="space-y-5">
              <MyRoomScan houseId={houseId} />
              <InventorySection />
            </div>
          )}
          {section === 'memory' && <MemorySection />}
          {section === 'history' && <HistorySection />}
        </main>
      </div>
    </DesktopSession>
  );
}
