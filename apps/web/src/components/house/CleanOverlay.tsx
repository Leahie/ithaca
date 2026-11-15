'use client';

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { CatalogPiece, PropertyTwin } from '@ithaca/schema';
import { PlanPreview } from '@/components/design/PlanPreview';
import { GLASS, GLASS_PANEL } from '@/components/ui';
import { config } from '@/lib/config';
import { useInventory, useLayouts } from '@/lib/data/hooks';
import { useHands } from '@/lib/hands/store';
import { placeName, roomName } from '@/lib/rooms';
import { onSoundBlocked, pageHasBeenClicked, setVoiceKind, speechProvider, unlockSound } from '@/lib/speech/provider';
import { canSpeak } from '@/lib/speech/useSpeakGuide';
import { useGuideVoice } from '@/lib/store/guide';
import { onSessionEvent, useSession } from '@/lib/store/session';
import { formatClock, minutesOfDay } from '@/lib/time';

/** Two soft rising notes, made in the browser (no sound file). Silent until the page has been clicked. */
let chimeContext: AudioContext | null = null;
function chime(): void {
  try {
    chimeContext ??= new AudioContext();
    const ctx = chimeContext;
    [660, 880].forEach((hz, n) => {
      const at = ctx.currentTime + n * 0.11;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = hz;
      gain.gain.setValueAtTime(0, at);
      gain.gain.linearRampToValueAtTime(0.07, at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.22);
      osc.connect(gain).connect(ctx.destination);
      osc.start(at);
      osc.stop(at + 0.24);
    });
  } catch {
    /* no audio here: the dots still show */
  }
}

const PILL = `rounded-full ${GLASS}`;

/**
 * What the big screen shows over the house: where you are, the time, the guide's face (click it
 * for the code a phone scans) and the guide's latest words as a caption. Every control is on the
 * phone; "Controls" brings the usual top bar and room list back.
 */
export function CleanOverlay({ twin, pairing, onShowControls }: { twin: PropertyTwin; pairing: ReactNode; onShowControls(): void }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const sweepId = useSession((s) => s.viewer.sweep_id);
  const datetime = useSession((s) => s.viewer.datetime);
  const interim = useSession((s) => s.interim);
  const last = useSession((s) => {
    for (let i = s.chat.length - 1; i >= 0; i--) {
      const c = s.chat[i]!;
      if (c.kind === 'agent') return c;
      if (c.kind === 'user') return null; // the user spoke since: wait for the reply
    }
    return null;
  });
  const text = last?.text ?? '';
  const [visible, setVisible] = useState(false);
  // The guide talks; the caption is a small subtitle, not a chat. The voice itself is the
  // screen's (HouseExperience speaks it, on every view): this is its switch.
  const [voice, setVoice] = useGuideVoice();
  const hands = useHands((s) => s.status);
  const handsError = useHands((s) => s.error);
  const toggleHands = useHands((s) => s.toggle);
  const handsOn = hands === 'on' || hands === 'starting';
  // Which voice is chosen on the phone, in its settings; a new choice says hello in it.
  const kind = useSession((s) => s.viewer.playback.voice);
  const heard = useRef(kind);
  useEffect(() => {
    if (kind === heard.current) return;
    heard.current = kind;
    if (!voice) return;
    setVoiceKind(kind);
    speechProvider().cancelSpeech();
    speechProvider().speak("Hi, I'll be your guide.");
  }, [kind, voice]);

  // After a reload the browser plays no sound until the page is clicked: say so, and take the
  // first click anywhere as the switch.
  const [needsClick, setNeedsClick] = useState(false);
  useEffect(() => {
    setNeedsClick(!pageHasBeenClicked());
    const off = onSoundBlocked((b) => b && setNeedsClick(true));
    const unlock = () => {
      unlockSound();
      setNeedsClick(false);
    };
    window.addEventListener('pointerdown', unlock);
    window.addEventListener('keydown', unlock);
    return () => {
      off();
      window.removeEventListener('pointerdown', unlock);
      window.removeEventListener('keydown', unlock);
    };
  }, []);

  // Between the user's words and the guide's first: a soft two-note chime says "heard you", and
  // three dots show it is working, so the pause reads as thinking and not as nothing happening.
  const [thinking, setThinking] = useState(false);
  const voiceOn = useRef(voice);
  voiceOn.current = voice;
  useEffect(
    () =>
      onSessionEvent((event) => {
        const asked = event.type === 'chat.user' || (event.type === 'voice.transcript' && event.payload.final && event.payload.text.trim() !== '');
        if (asked) {
          setThinking(true);
          if (voiceOn.current) chime();
        } else if (event.type === 'agent.action') {
          setThinking(true); // a tool is running: still working, whatever was just said
        } else if (event.type === 'agent.token' || event.type === 'agent.message' || event.type === 'agent.error' || (event.type === 'agent.turn' && event.payload.status === 'ended')) {
          setThinking(false);
        }
      }),
    [],
  );

  // A caption stays while it is being written and for a few seconds after.
  useEffect(() => {
    if (!text) return setVisible(false);
    setVisible(true);
    const t = setTimeout(() => setVisible(false), 7000);
    return () => clearTimeout(t);
  }, [text]);

  // Pieces the guide suggests are shown here as pictures (the choice is made on the phone) until
  // one is picked, the user moves on, or a minute has passed.
  const [suggested, setSuggested] = useState<CatalogPiece[]>([]);
  useEffect(() => {
    let t = 0;
    const off = onSessionEvent((event) => {
      if (event.type === 'catalog.suggest') {
        setSuggested(event.payload.pieces.slice(0, 4));
        window.clearTimeout(t);
        t = window.setTimeout(() => setSuggested([]), 60000);
      } else if (event.type === 'catalog.picked' || event.type === 'chat.user' || (event.type === 'voice.transcript' && event.payload.final && event.payload.text.trim() !== '')) {
        setSuggested([]);
      }
    });
    return () => {
      off();
      window.clearTimeout(t);
    };
  }, []);

  // Placement options waiting for a choice (made on the phone) are drawn over the house.
  const shown = useSession((s) => s.viewer.shown_layout_ids);
  const selectedLayout = useSession((s) => s.viewer.selected_layout_id);
  const layouts = useLayouts(twin.property_id);
  const inventory = useInventory();
  const items = useMemo(() => new Map((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);
  const options = shown.includes(selectedLayout ?? '') ? [] : (layouts.data ?? []).filter((l) => shown.includes(l.layout_id)).slice(0, 3);

  const caption = interim ? `“${interim.text || 'Listening…'}”` : visible ? text : '';
  return (
    <div className="pointer-events-none absolute inset-0 z-20" data-testid="clean-overlay">
      {/* The guide's face: click it for the code a phone scans. */}
      <div className="pointer-events-auto absolute top-3 right-3 flex items-start gap-2">
        {canSpeak() && (
          <button type="button" aria-pressed={voice} onClick={() => setVoice(!voice)} className={`px-3.5 py-2 text-sm ${PILL}`}>
            {voice ? 'Voice on' : 'Voice off'}
          </button>
        )}
        {config.features.has('hands') && (
          <button
            type="button"
            aria-pressed={handsOn}
            onClick={toggleHands}
            title={hands === 'error' ? (handsError ?? undefined) : 'Arrange furniture with your hands. Pinch a piece to pick it up, move your hand to slide it, pinch with the other hand and turn to rotate it, let go to place it.'}
            className={`px-3.5 py-2 text-sm ${PILL}`}
          >
            {hands === 'starting' ? 'Hands…' : hands === 'error' ? 'Hands failed' : handsOn ? 'Hands on' : 'Hands off'}
          </button>
        )}
        <button type="button" onClick={onShowControls} className={`px-3.5 py-2 text-sm ${PILL}`}>
          Controls
        </button>
        {pairing}
      </div>
      <div className={`absolute bottom-3 left-3 px-3.5 py-1.5 text-sm tabular-nums ${PILL}`}><span className="font-display font-semibold">{placeName(twin, roomId, sweepId)}</span> · {formatClock(minutesOfDay(datetime, twin.timezone))}</div>
      {suggested.length > 0 && options.length <= 1 && (
        <div className="absolute inset-x-0 top-20 flex justify-center px-6" data-testid="clean-catalog">
          <div className="rounded-3xl border border-white/30 bg-black/45 p-4 text-white shadow-lg backdrop-blur-md">
            <div className="mb-3 text-center text-sm">The guide suggests · choose on your phone</div>
            <div className="flex gap-3">
              {suggested.map((p) => (
                <figure key={p.catalog_id} className="w-[min(170px,18vw)]">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={p.thumbnail_url} alt="" className="aspect-square w-full rounded-xl bg-white/90 object-contain" />
                  <figcaption className="mt-1.5 text-center text-xs text-white/90">
                    {p.label}
                    <span className="block text-white/60 tabular-nums">{p.dims_m.w.toFixed(1)} × {p.dims_m.d.toFixed(1)} m</span>
                  </figcaption>
                </figure>
              ))}
            </div>
          </div>
        </div>
      )}
      {options.length > 1 && (
        <div className="absolute inset-x-0 top-20 flex justify-center px-6" data-testid="clean-options">
          <div className={`rounded-3xl p-4 ${GLASS_PANEL}`}>
            <div className="mb-3 text-center text-sm">{options.length} places it fits in {roomName(twin, options[0]!.room_id)} · choose on your phone</div>
            <div className="flex gap-3">
              {options.map((l, i) => {
                const room = twin.rooms.find((r) => r.room_id === l.room_id);
                return (
                  <figure key={l.layout_id} className="w-[min(220px,24vw)]">
                    <div className="aspect-[4/3] overflow-hidden rounded-xl bg-white/90">
                      {room && <PlanPreview room={room} openings={twin.openings.filter((o) => o.room_id === room.room_id)} placements={l.placements} items={items} labels={false} className="h-full w-full p-2" />}
                    </div>
                    <figcaption className="mt-1.5 text-center text-xs text-white/85">{l.rationale || `Option ${'ABC'[i]}`}</figcaption>
                  </figure>
                );
              })}
            </div>
          </div>
        </div>
      )}
      {voice && needsClick && (
        <div className="absolute inset-x-0 top-16 flex justify-center" data-testid="sound-needs-click">
          <div className="rounded-full border border-white/40 bg-main px-5 py-2.5 text-sm font-semibold text-on-main shadow-lg">Click anywhere on this screen to hear the guide</div>
        </div>
      )}
      {thinking && !caption && (
        <div role="status" aria-label="The guide is thinking" className="absolute inset-x-0 bottom-3 flex justify-center" data-testid="guide-thinking">
          <div className="flex items-center gap-1.5 rounded-full border border-white/20 bg-black/40 px-4 py-2.5 backdrop-blur-md">
            {[0, 1, 2].map((i) => (
              <i key={i} className="thinking-dot h-2 w-2 rounded-full bg-white" style={{ animationDelay: `${i * 160}ms` }} />
            ))}
          </div>
        </div>
      )}
      {caption && (
        <div aria-live="polite" className="absolute inset-x-0 bottom-3 flex justify-center px-40">
          <p className="line-clamp-2 max-w-[560px] rounded-xl border border-white/20 bg-black/40 px-3.5 py-1.5 text-center text-[13px] leading-5 text-white/95 backdrop-blur-md">{caption}</p>
        </div>
      )}
    </div>
  );
}
