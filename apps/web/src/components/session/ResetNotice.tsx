'use client';

import type { ClientKind } from '@ithaca/schema';
import { Button } from '@/components/ui';
import { startOver } from '@/lib/reset';

/**
 * The whole screen, once everything was reset from another one (`app.reset`). The page that was
 * here is gone with its data, and this screen's session is over: it says so and what to do next,
 * and does not try to connect again.
 */
export function ResetNotice({ client }: { client: ClientKind }) {
  return (
    <main role="alert" data-testid="reset-notice" className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-5 px-5 py-8">
      <div className="flex items-center gap-2 font-display text-2xl font-semibold">
        <span className="h-4 w-4 rounded-full bg-sun shadow-[0_0_0_1.5px_var(--color-main)]" />
        Ithaca
      </div>
      <div className="space-y-2">
        <h1 className="font-display text-2xl font-semibold">Everything was reset</h1>
        <p className="text-[15px] leading-6 text-muted">
          All data was deleted from the big screen: the family, the furniture and what the guides remembered.{' '}
          {client === 'phone' ? 'To join again, scan the family code on the big screen.' : 'Reload this page to start again.'}
        </p>
      </div>
      {client === 'desktop' && (
        <Button variant="primary" size="lg" className="self-start" onClick={startOver}>
          Start again
        </Button>
      )}
    </main>
  );
}
