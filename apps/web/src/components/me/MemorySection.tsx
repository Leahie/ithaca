'use client';

import { useEffect, useState } from 'react';
import { Markdown } from '@/components/Markdown';
import { Button, Notice, Segmented, Spinner, cn } from '@/components/ui';
import { IconTrash } from '@/components/ui/icons';
import { api } from '@/lib/api';
import { useMemoryFile, useMemoryList } from '@/lib/data/hooks';
import { invalidate } from '@/lib/data/resource';

const DESCRIPTIONS: [RegExp, string][] = [
  [/^profile\.md$/, 'Who lives with you, your commute, what matters most.'],
  [/^preferences\.md$/, 'Style and layout likes and dislikes, with reasons.'],
  [/^inventory\.md$/, 'An index of your furniture. Rebuilt from the inventory.'],
  [/^properties\//, 'Notes and verdict for one home you viewed.'],
  [/^house\//, 'What stands in each room of one home right now, piece by piece. Rewritten whenever furniture moves.'],
];
const describe = (path: string) => DESCRIPTIONS.find(([re]) => re.test(path))?.[1] ?? 'A note the agent keeps.';
/** B3: inventory.md and the house registers are derived views; the database is canonical. */
const isDerived = (path: string) => path === 'inventory.md' || path.startsWith('house/');

/** What the agent remembers, as files you can read, edit and delete. */
export function MemorySection() {
  const list = useMemoryList();
  const [path, setPath] = useState<string | null>(null);
  const file = useMemoryFile(path);
  const [draft, setDraft] = useState<string | null>(null);
  const [view, setView] = useState<'read' | 'edit'>('read');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [newPath, setNewPath] = useState('');

  const files = list.data ?? [];
  useEffect(() => {
    if (!path && files.length) setPath(files[0]!);
  }, [files, path]);
  useEffect(() => {
    setDraft(null);
    setConfirm(false);
    setError(null);
    setView('read');
  }, [path]);

  const text = draft ?? file.data ?? '';
  const dirty = draft !== null && draft !== (file.data ?? '');

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run(async () => {
      if (!path) return;
      await api().putMemory(path, text);
      invalidate('memory:');
      setDraft(null);
    });

  const remove = () =>
    run(async () => {
      if (!path) return;
      await api().deleteMemory(path);
      invalidate('memory:');
      setPath(null);
    });

  const create = () =>
    run(async () => {
      const clean = newPath.trim().replace(/^\/+/, '');
      if (!clean) return;
      const name = clean.endsWith('.md') ? clean : `${clean}.md`;
      await api().putMemory(name, `# ${name.replace(/\.md$/, '').split('/').pop()}\n\n`);
      invalidate('memory:');
      setNewPath('');
      setPath(name);
    });

  if (list.error) {
    return (
      <Notice tone="error" title="Could not load memory">
        {list.error.message}
      </Notice>
    );
  }
  if (!list.data) return <Spinner label="Loading memory…" />;

  return (
    <div className="grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)]" data-testid="memory">
      <div className="space-y-3">
        <ul className="space-y-1">
          {files.map((f) => (
            <li key={f}>
              <button
                type="button"
                data-testid="memory-file"
                data-path={f}
                aria-current={f === path}
                onClick={() => setPath(f)}
                className={cn('block w-full rounded-lg border px-3 py-2 text-left', f === path ? 'border-sun/50 bg-sun/10' : 'border-line bg-surface hover:border-line-strong')}
              >
                <span className="block truncate font-mono text-xs">{f}</span>
                <span className="mt-0.5 block text-xs leading-4 text-muted">{describe(f)}</span>
              </button>
            </li>
          ))}
          {files.length === 0 && <li className="text-sm text-muted">The agent has not written anything down yet.</li>}
        </ul>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <input
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            placeholder="new-note.md"
            aria-label="New memory file name"
            className="h-8 min-w-0 flex-1 rounded-md border border-line bg-ink px-2.5 font-mono text-xs placeholder:text-faint focus:border-line-strong focus:outline-none"
          />
          <Button size="sm" type="submit" className="h-8" disabled={busy || !newPath.trim()}>
            Add
          </Button>
        </form>
        <p className="text-xs leading-5 text-faint">The agent reads these files at the start of every conversation and updates them when you give feedback. Deleting a file makes it forget.</p>
      </div>

      <div className="min-w-0 rounded-xl border border-line bg-surface">
        {!path ? (
          <div className="p-6 text-sm text-muted">Select a file.</div>
        ) : (
          <>
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-2.5">
              <span className="truncate font-mono text-xs text-muted">/memories/{path}</span>
              <Segmented<'read' | 'edit'>
                label="View"
                size="sm"
                value={view}
                onChange={setView}
                options={[
                  { value: 'read', label: 'Read' },
                  { value: 'edit', label: 'Edit' },
                ]}
              />
            </header>
            <div className="p-4">
              {file.error ? (
                <Notice tone="error" title="Could not open this file">
                  {file.error.message}
                </Notice>
              ) : file.data === undefined ? (
                <Spinner label="Opening…" />
              ) : view === 'read' ? (
                <Markdown text={text} />
              ) : (
                <textarea
                  value={text}
                  onChange={(e) => setDraft(e.target.value)}
                  spellCheck={false}
                  aria-label={`Contents of ${path}`}
                  data-testid="memory-editor"
                  className="h-[420px] w-full resize-y rounded-md border border-line bg-ink p-3 font-mono text-[13px] leading-5 focus:border-line-strong focus:outline-none"
                />
              )}
              {isDerived(path) && (
                <p className="mt-3 text-xs text-sun-deep">
                  {path.startsWith('house/')
                    ? 'The app writes this file each time furniture in the house changes. It cannot be edited or deleted here: move the furniture instead.'
                    : 'This file is rebuilt from your inventory. Edits here are overwritten: change the inventory instead.'}
                </p>
              )}
              {error && <p className="mt-3 text-xs text-danger">{error}</p>}
              <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                <div className="flex gap-2">
                  <Button variant="primary" disabled={!dirty || busy} onClick={() => void save()}>
                    {busy ? 'Saving…' : 'Save'}
                  </Button>
                  {dirty && (
                    <Button variant="ghost" disabled={busy} onClick={() => setDraft(null)}>
                      Discard changes
                    </Button>
                  )}
                </div>
                {confirm ? (
                  <div className="flex items-center gap-2">
                    <Button variant="danger" disabled={busy} onClick={() => void remove()}>
                      Delete this memory
                    </Button>
                    <Button variant="ghost" onClick={() => setConfirm(false)}>
                      Keep
                    </Button>
                  </div>
                ) : (
                  <Button variant="ghost" className="text-danger hover:text-danger" onClick={() => setConfirm(true)}>
                    <IconTrash /> Delete
                  </Button>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
