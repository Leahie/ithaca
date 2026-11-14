'use client';

import { useEffect, useState } from 'react';
import type { PropertyTwin } from '@ithaca/schema';
import { roomName } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';
import { formatClock, minutesOfDay } from '@/lib/time';

const GLASS = 'rounded-full border border-white/30 bg-black/35 text-white shadow-lg backdrop-blur-md';

/**
 * What the big screen shows over the house while a phone is the remote: where you are, the time,
 * and the guide's latest words as a caption. Every control is on the phone; "Controls" brings the
 * usual top bar and room list back.
 */
export function CleanOverlay({ twin, onShowControls }: { twin: PropertyTwin; onShowControls(): void }) {
  const roomId = useSession((s) => s.viewer.room_id);
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

  // A caption stays while it is being written and for a few seconds after.
  useEffect(() => {
    if (!text) return setVisible(false);
    setVisible(true);
    const t = setTimeout(() => setVisible(false), 9000);
    return () => clearTimeout(t);
  }, [text]);

  const caption = interim ? `“${interim.text || 'Listening…'}”` : visible ? (text.length > 260 ? `…${text.slice(-260)}` : text) : '';
  return (
    <div className="pointer-events-none absolute inset-0 z-20" data-testid="clean-overlay">
      <div className={`absolute top-3 left-3 px-3.5 py-1.5 font-display text-sm font-semibold ${GLASS}`}>{roomName(twin, roomId)}</div>
      <button type="button" onClick={onShowControls} className={`pointer-events-auto absolute top-3 right-3 flex items-center gap-2 py-1 pr-3.5 pl-1 text-sm ${GLASS}`}>
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-sun font-display text-sm font-semibold text-main">G</span>
        Phone connected · Controls
      </button>
      <div className={`absolute bottom-3 left-3 px-3.5 py-1.5 font-mono text-sm tabular-nums ${GLASS}`}>{formatClock(minutesOfDay(datetime, twin.timezone))}</div>
      {caption && (
        <div aria-live="polite" className="absolute inset-x-0 bottom-14 flex justify-center px-6">
          <p className="max-w-[640px] rounded-2xl border border-white/30 bg-black/45 px-4 py-2.5 text-center text-base leading-6 text-white shadow-lg backdrop-blur-md">{caption}</p>
        </div>
      )}
    </div>
  );
}
