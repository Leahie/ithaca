'use client';

import type { ResetCounts, ResetFailure } from '@ithaca/schema';
import { useEffect, useId, useState } from 'react';
import { createPortal } from 'react-dom';
import { Button, MODAL_BACKDROP, MODAL_SCROLL, Spinner, cn, useScrollLock } from '@/components/ui';
import { api } from '@/lib/api';
import { resetEverything, startOver } from '@/lib/reset';

const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** What a reset deletes, a line each: with how many there are once they are counted, in general words until then. */
function whatGoes(c: ResetCounts | null): string[] {
  if (!c) {
    return [
      'All furniture, and where it stands in the house',
      'All saved layouts',
      'Everything the guides remember, and every conversation with them',
      'Every family and its members',
      'Your photos and videos, and the 3D models made from them',
    ];
  }
  return [
    `${count(c.furniture, 'piece of furniture', 'pieces of furniture')} (${c.in_houses} placed in a house)`,
    count(c.layouts, 'saved layout', 'saved layouts'),
    `${count(c.memory_files, 'memory file', 'memory files')}: what the guides remember`,
    count(c.conversations, 'conversation with a guide', 'conversations with the guides'),
    `${count(c.families, 'family', 'families')} with ${count(c.members, 'member', 'members')}`,
    `${count(c.files, 'file', 'files')}: your photos and videos, and the 3D models made from them`,
  ];
}

/** The one click that deletes: a filled danger pill, so it cannot be taken for the way out. */
const DELETE = 'inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-full bg-danger px-5 font-display text-base font-semibold text-white transition-colors hover:bg-danger/90 disabled:cursor-not-allowed disabled:opacity-60';

function ResetModal({ onClose }: { onClose: () => void }) {
  const title = useId();
  const [counts, setCounts] = useState<ResetCounts | null>(null);
  const [counting, setCounting] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** The parts the server could not delete. The rest is gone and every screen is signed out. */
  const [failed, setFailed] = useState<ResetFailure[] | null>(null);

  useEffect(() => {
    let live = true;
    void api()
      .resetPreview()
      .then(
        (c) => live && setCounts(c),
        () => {}, // not counted: the list says what goes in general words
      )
      .finally(() => live && setCounting(false));
    return () => {
      live = false;
    };
  }, []);

  // Once part of it is deleted there is no going back to the page as it was: closing starts over.
  const close = failed ? startOver : onClose;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) close();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, close]);
  useScrollLock();

  const run = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await resetEverything();
      if (result.ok) return startOver(); // the button keeps saying "Deleting…" until the home page loads
      setFailed(result.failed ?? []);
      setBusy(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return createPortal(
    <div className={MODAL_BACKDROP} onClick={(e) => e.target === e.currentTarget && !busy && close()}>
      <section role="dialog" aria-modal aria-labelledby={title} data-testid="reset-modal"
        className="flex w-full max-w-md flex-col overflow-hidden rounded-3xl border border-line bg-surface shadow-float">
        <div className={cn(MODAL_SCROLL, 'max-h-[calc(100dvh-9rem)] space-y-3 px-5 pt-5 pb-1')}>
          <h2 id={title} className="font-display text-xl font-semibold">Reset everything?</h2>
          {failed ? (
            <div role="alert" className="space-y-2 text-sm leading-6">
              <p className="font-semibold text-danger">Not everything was deleted.</p>
              <ul className="list-disc space-y-1 pl-5 text-muted">
                {failed.map((f, i) => (
                  <li key={i}>
                    <span className="font-semibold text-text">{f.part}</span>: {f.error}
                  </li>
                ))}
              </ul>
              <p className="text-muted">The rest is gone, and every phone and screen was signed out. Try again to delete what is left.</p>
            </div>
          ) : (
            <>
              <p className="text-sm leading-6 text-muted">This deletes all data, for everyone who uses this screen or joined from a phone:</p>
              <ul className="list-disc space-y-1 pl-5 text-sm leading-6" aria-busy={counting}>
                {/* Fixture mode has no backend to count: its zeros would read as "nothing to lose". */}
                {whatGoes(api().mode === 'fixture' ? null : counts).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              {counting && <Spinner label="Counting…" />}
              <p className="text-sm leading-6 text-muted">Every phone is signed out. To join again, they scan the family code.</p>
              <p className="text-sm leading-6 text-muted">
                <span className="font-semibold text-text">Kept:</span> the house itself and the furniture library.
              </p>
              <p className="text-sm leading-6 font-semibold">This cannot be undone.</p>
            </>
          )}
          {error && (
            <p role="alert" className="text-sm leading-6 text-danger">
              Nothing was deleted. {error}
            </p>
          )}
        </div>
        <footer className="flex shrink-0 flex-wrap justify-end gap-2 px-5 pt-3 pb-5">
          <Button autoFocus disabled={busy} onClick={close}>
            {failed ? 'Close' : 'Keep my data'}
          </Button>
          <button type="button" className={DELETE} disabled={busy} aria-busy={busy} onClick={() => void run()} data-testid="reset-confirm">
            {busy ? 'Deleting…' : failed ? 'Try again' : 'Delete everything'}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}

/**
 * "Reset everything" on the Me page: a danger button in the header and the modal it opens. The
 * family popover on the house page links here as /me#reset, which opens the modal on arrival.
 */
export function ResetEverything() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (window.location.hash !== '#reset') return;
    setOpen(true);
    // The mark has done its work: a reload of this page must not open the modal again.
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }, []);
  return (
    <>
      <Button id="reset" variant="danger" size="sm" onClick={() => setOpen(true)} data-testid="reset-open">
        Reset everything
      </Button>
      {open && <ResetModal onClose={() => setOpen(false)} />}
    </>
  );
}
