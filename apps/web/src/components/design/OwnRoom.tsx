'use client';

import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { Spinner, cn } from '@/components/ui';
import { IconCamera, IconUpload } from '@/components/ui/icons';
import { preparePhoto } from './PhotoPiece';

/**
 * "My room from a photo": one picture of a room the person lives in now. Everything of theirs in
 * it (furniture, lamps, plants, rugs, pictures, the things standing on the furniture) is cut out
 * and rebuilt in 3D, and the room of the house they are in is set up with it: on the floor where it
 * fits that room, on the piece it stood on, on the wall it hung on.
 *
 * Two steps, because it replaces what stands in the room: take or pick the photo, then look at
 * it and say yes. `onSend` starts the job and resolves true once it is under way.
 */
export function OwnRoom({ roomName, busy, onSend, className }: { roomName: string; busy: boolean; onSend(photo: File): Promise<boolean>; className?: string }) {
  const camera = useRef<HTMLInputElement>(null);
  const library = useRef<HTMLInputElement>(null);
  const [photo, setPhoto] = useState<File | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!photo) {
      setUrl(null);
      return;
    }
    const u = URL.createObjectURL(photo);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [photo]);

  const take = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    // Upright and no larger than the pipeline needs, as for a single piece.
    if (f) void preparePhoto(f).then(setPhoto);
    e.target.value = '';
  };
  const send = async () => {
    if (photo && (await onSend(photo))) setPhoto(null);
  };
  const room = roomName.toLowerCase();
  const button = 'flex h-10 flex-1 items-center justify-center gap-1.5 rounded-full border border-line bg-surface px-3 text-[13px] font-semibold hover:border-line-strong active:scale-[0.97] disabled:opacity-60';

  return (
    <section className={cn('rounded-2xl border border-line bg-raised p-3', className)} aria-label="My room from a photo" data-testid="own-room">
      <input ref={camera} type="file" accept="image/*" capture="environment" className="sr-only" data-testid="own-room-capture" onChange={take} />
      <input ref={library} type="file" accept="image/png,image/jpeg,image/webp,image/*" className="sr-only" data-testid="own-room-import" onChange={take} />
      <h3 className="font-display text-[15px] leading-tight font-semibold">My room from a photo</h3>
      {photo ? (
        <div className="mt-2 space-y-2">
          <div className="overflow-hidden rounded-xl border border-line bg-ink">
            {/* A local object URL: next/image can't optimize it. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {url && <img src={url} alt="Your photo of your room" className={cn('mx-auto block max-h-[34dvh] w-auto max-w-full', busy && 'opacity-60')} />}
          </div>
          <p className="text-[13px] leading-snug text-muted">
            Everything of yours in this photo is rebuilt in 3D and set up in the {room}. What stands there now goes back to storage. It takes about two minutes.
          </p>
          <div className="flex gap-2">
            <button type="button" disabled={busy} onClick={() => void send()} data-testid="own-room-send"
              className="flex h-10 flex-1 items-center justify-center gap-2 rounded-full bg-main text-sm font-semibold text-on-main disabled:opacity-50">
              {busy ? <Spinner /> : null} {busy ? 'Sending…' : `Set up the ${room}`}
            </button>
            <button type="button" disabled={busy} onClick={() => setPhoto(null)} className="h-10 rounded-full border border-line bg-surface px-4 text-sm font-semibold disabled:opacity-50">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <p className="mt-1 text-[13px] leading-snug text-muted">
            One photo of the room you live in now. Your furniture, lamps, plants and pictures are rebuilt in 3D and set up in the {room}.
          </p>
          <div className="mt-2 flex gap-2">
            <button type="button" disabled={busy} onClick={() => camera.current?.click()} className={button}>
              <IconCamera width={16} height={16} /> Take a photo
            </button>
            <button type="button" disabled={busy} onClick={() => library.current?.click()} className={button}>
              <IconUpload width={16} height={16} /> Pick a photo
            </button>
          </div>
        </>
      )}
    </section>
  );
}
