'use client';

import { CatalogSuggestion } from './CatalogSuggestion';
import { LayoutSuggestion } from './LayoutSuggestion';
import { useEffect, useRef, useState } from 'react';
import { Badge, Button, Notice, cn } from '@/components/ui';
import { IconCheck, IconMic, IconSend } from '@/components/ui/icons';
import { speechProvider } from '@/lib/speech/provider';
import { usePushToTalk } from '@/lib/speech/usePushToTalk';
import type { ChatItem } from '@/lib/store/reducer';
import { useMemberName } from '@/lib/store/family';
import { onSessionEvent, useSession } from '@/lib/store/session';

const SUGGESTIONS = ['Take me to the kitchen', 'Show me December morning', 'Show layouts', 'How loud is the bedroom?'];

/** A member's initials in an ink disc: who said it, without spending an accent colour. */
function Who({ name, agent = false }: { name: string; agent?: boolean }) {
  const initials = name
    .split(/\s+/)
    .map((w) => w[0] ?? '')
    .join('')
    .slice(0, 2)
    .toUpperCase();
  return (
    <span className="flex items-center gap-1.5 text-[12px] font-semibold text-muted">
      <span aria-hidden className="grid h-5 w-5 place-items-center rounded-full bg-main font-display text-[9px] text-on-main">
        {initials}
      </span>
      {agent ? `${name}'s agent` : name}
    </span>
  );
}

function Item({ item, nameOf }: { item: ChatItem; nameOf: (id: string | null | undefined) => string | null }) {
  // In a family session every line says whose it is: the person, or their agent.
  const who = 'member_id' in item ? nameOf(item.member_id) : null;
  switch (item.kind) {
    case 'user':
      return (
        <div className="flex flex-col items-end gap-1">
          {who && <Who name={who} />}
          <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-raised px-3 py-2 text-sm leading-5">
            {item.text}
            {(item.via === 'voice' || item.source === 'phone') && (
              <span className="mt-0.5 block text-[11px] text-faint">
                {item.via === 'voice' ? 'spoken' : 'typed'}
                {item.source === 'phone' ? ' on phone' : ''}
              </span>
            )}
          </div>
        </div>
      );
    case 'agent':
      return (
        <div className="space-y-1">
          {who && <Who name={who} agent />}
          <div data-testid="agent-message" data-streaming={item.streaming} className={cn('max-w-[92%] text-sm leading-[1.45] whitespace-pre-wrap', item.streaming && 'streaming-caret')}>
            {item.text}
          </div>
        </div>
      );
    case 'action':
      return (
        <div data-testid="agent-action" data-tool={item.tool}>
          <Badge tone="sun" title={item.tool}>
            <IconCheck width={11} height={11} />
            {who ? `${who}'s agent: ${item.summary}` : item.summary}
          </Badge>
        </div>
      );
    case 'error':
      return (
        <div data-testid="agent-error">
          <Notice tone="error" title={who ? `${who}'s agent hit a problem` : 'The agent hit a problem'}>
            {item.message}
          </Notice>
        </div>
      );
    case 'layouts':
      return <LayoutSuggestion layoutIds={item.layout_ids} />;
    case 'catalog':
      return <CatalogSuggestion propertyId={item.property_id} roomId={item.room_id} nearItemId={item.near_item_id} pieces={item.pieces} />;
    case 'note':
      return (
        <div data-testid="family-note" className="rounded-[var(--radius-frame)] border border-line bg-raised px-3 py-2.5">
          <div className="text-[11px] font-semibold tracking-[0.08em] text-muted uppercase">Family board</div>
          <p className="mt-1 text-sm leading-5">{item.text}</p>
          <div className="mt-1.5">{<Who name={nameOf(item.member_id) ?? 'The guide'} agent={!!nameOf(item.member_id)} />}</div>
        </div>
      );
    case 'ask': {
      const from = nameOf(item.from_member_id);
      const to = nameOf(item.to_member_id) ?? 'Another member';
      return (
        <div data-testid="family-ask" className="space-y-2 rounded-[var(--radius-frame)] border-2 border-text bg-raised px-3 py-2.5">
          <div className="text-[11px] font-semibold tracking-[0.08em] text-muted uppercase">
            {from ? `${from}'s agent` : 'The guide'} asked {to}'s agent
          </div>
          {item.question && <p className="text-sm leading-5 text-muted">“{item.question}”</p>}
          <div className="space-y-1">
            <Who name={to} agent />
            <p className={cn('text-sm leading-5', !item.answer && 'streaming-caret text-muted')}>{item.answer ?? 'Thinking'}</p>
          </div>
        </div>
      );
    }
  }
}

/** Streaming chat with the agent (A8): tokens as they arrive, a chip per action, errors shown. */
export function AgentDock() {
  const chat = useSession((s) => s.chat);
  const interim = useSession((s) => s.interim);
  const status = useSession((s) => s.status);
  const nameOf = useMemberName();
  const dispatch = useSession((s) => s.dispatch);
  const [draft, setDraft] = useState('');
  const [speak, setSpeak] = useState(false);
  const ptt = usePushToTalk();
  const end = useRef<HTMLDivElement>(null);
  const spokeByVoice = useRef(false);

  useEffect(() => {
    // Braces matter: scrollIntoView returns a Promise in current Chromium, and an effect must not.
    void end.current?.scrollIntoView({ block: 'end' });
  }, [chat, interim]);

  // Read the reply aloud when the question was spoken, or when the user turned it on.
  useEffect(
    () =>
      onSessionEvent((event) => {
        if (event.type === 'voice.transcript' && event.payload.final) spokeByVoice.current = true;
        if (event.type === 'chat.user') spokeByVoice.current = false;
        // Read aloud only the big screen's own agent, not every family member's.
        if (event.type === 'agent.message' && !event.member_id && (speak || spokeByVoice.current)) speechProvider().speak(event.payload.text);
      }),
    [speak],
  );

  const send = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    dispatch('chat.user', { text: trimmed });
    setDraft('');
  };

  return (
    <aside aria-label="Agent" className="flex h-full w-[340px] shrink-0 flex-col border-l border-line bg-surface">
      <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <div className="text-sm font-medium">Agent</div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              if (speak) speechProvider().cancelSpeech();
              setSpeak(!speak);
            }}
            aria-pressed={speak}
            className={cn('rounded px-1.5 py-0.5 text-[11px]', speak ? 'bg-sun/15 text-sun-deep' : 'text-faint hover:text-muted')}
          >
            {speak ? 'Voice replies on' : 'Voice replies off'}
          </button>
          <Badge tone={status === 'open' ? 'leaf' : status === 'connecting' ? 'sun' : 'danger'}>{status === 'open' ? 'live' : status}</Badge>
        </div>
      </header>

      <div className="flex-1 space-y-2.5 overflow-y-auto px-4 py-3" aria-live="polite">
        {chat.length === 0 && (
          <div className="space-y-3 pt-2">
            <p className="text-sm leading-5 text-muted">Ask about this home, or tell the agent where to take you. It can move the tour, set the sun, play street noise and lay out your furniture.</p>
            <div className="flex flex-wrap gap-1.5">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" onClick={() => send(s)} className="rounded-full border border-line px-2.5 py-1 text-xs text-muted hover:border-line-strong hover:text-text">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {chat.map((item) => (
          <Item key={`${item.kind}:${item.id}`} item={item} nameOf={nameOf} />
        ))}
        {interim && (
          <div className="flex justify-end">
            <div className="max-w-[85%] rounded-2xl rounded-br-sm border border-dashed border-line-strong px-3 py-2 text-sm text-muted italic">{interim.text || 'Listening…'}</div>
          </div>
        )}
        <div ref={end} />
      </div>

      {ptt.error && <div className="px-4 pb-2 text-xs text-danger">{ptt.error}</div>}
      <form
        className="flex items-end gap-2 border-t border-line p-3"
        onSubmit={(e) => {
          e.preventDefault();
          send(draft);
        }}
      >
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send(draft);
            }
          }}
          rows={Math.min(4, Math.max(1, draft.split('\n').length))}
          placeholder="Ask the agent…"
          aria-label="Message the agent"
          className="min-h-9 flex-1 resize-none rounded-md border border-line bg-ink px-3 py-2 text-sm placeholder:text-faint focus:border-line-strong focus:outline-none"
        />
        <Button
          variant={ptt.listening ? 'primary' : 'subtle'}
          aria-label="Hold to talk"
          title={ptt.supported ? 'Hold to talk' : 'Speech recognition is not available in this browser'}
          disabled={!ptt.supported}
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            ptt.start();
          }}
          onPointerUp={ptt.stop}
          onPointerCancel={ptt.stop}
          square
        >
          <IconMic />
        </Button>
        <Button type="submit" variant="primary" aria-label="Send" disabled={!draft.trim()} square>
          <IconSend />
        </Button>
      </form>
    </aside>
  );
}
