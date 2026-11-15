'use client';

import { useEffect, type ReactNode } from 'react';
import { config } from '@/lib/config';
import { useFamily } from '@/lib/store/family';
import { SessionProvider } from './SessionProvider';

/**
 * The big screen's session, the same one on every desktop page (the house and /me). With families
 * on it waits for the saved household first, so the session is the family's: its phones find the
 * house through it, and going to /me and back does not leave them behind in an old session.
 */
export function DesktopSession({ propertyId, fallback = null, children }: { propertyId: string; fallback?: ReactNode; children: ReactNode }) {
  const family = config.features.has('family');
  const restored = useFamily((s) => s.restored);
  const householdId = useFamily((s) => s.household?.household_id ?? null);
  useEffect(() => {
    if (family) void useFamily.getState().restore();
  }, [family]);
  if (family && !restored) return <>{fallback}</>;
  return (
    <SessionProvider client="desktop" propertyId={propertyId} householdId={family ? householdId : null}>
      {children}
    </SessionProvider>
  );
}
