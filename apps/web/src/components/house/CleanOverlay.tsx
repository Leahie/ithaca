'use client';

import { useEffect, useState, type ReactNode } from 'react';
import type { PropertyTwin } from '@ithaca/schema';
import { roomName } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';
import { formatClock, minutesOfDay } from '@/lib/time';

const GLASS = 'rounded-full border border-white/30 bg-black/35 text-white shadow-lg backdrop-blur-md';

/**
 * What the big screen shows over the house: where you are, the time, the guide's face (click it
 * for the code a phone scans) and the guide's latest words as a caption. Every control is on the
 * phone; "Controls" brings the usual top bar and room list back.
 */
export function CleanOverlay({ twin, pairing, onShowControls }: { twin: PropertyTwin; pairing: ReactNode; onShowControls(): void }) {
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
      {/* The guide's face: click it for the code a phone scans. */}
      <div className="pointer-events-auto absolute top-3 right-3 flex items-start gap-2">
        <button type="button" onClick={onShowControls} className={`px-3.5 py-2 text-sm ${GLASS}`}>
          Controls
        </button>
        {pairing}
      </div>
      <div className={`absolute bottom-3 left-3 px-3.5 py-1.5 text-sm tabular-nums ${GLASS}`}><span className="font-display font-semibold">{roomName(twin, roomId)}</span> · {formatClock(minutesOfDay(datetime, twin.timezone))}</div>
      {caption && (
        <div aria-live="polite" className="absolute inset-x-0 bottom-14 flex justify-center px-6">
          <p className="max-w-[640px] rounded-2xl border border-white/30 bg-black/45 px-4 py-2.5 text-center text-base leading-6 text-white shadow-lg backdrop-blur-md">{caption}</p>
        </div>
      )}
    </div>
  );
}
