'use client';

import type { PhotoPoint } from '@ithaca/schema';
import { useEffect, useRef, useState, type ChangeEvent, type MouseEvent } from 'react';
import { Spinner, cn } from '@/components/ui';
import { IconCamera, IconUpload } from '@/components/ui/icons';

/** Photos are sent at most this long on their longer side: plenty for SAM, and a phone photo uploads in a moment. */
const MAX_SIDE_PX = 2048;

/**
 * The photo upright (the browser applies the camera's EXIF turn when it decodes) and at most
 * MAX_SIDE_PX, as a JPEG. A tapped spot is measured on the picture as shown, so the server must
 * see the same pixels. A file the browser can't decode is sent as it is.
 */
export async function preparePhoto(file: File): Promise<File> {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_SIDE_PX / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
    if (!blob) return file;
    return new File([blob], `${(file.name || 'photo').replace(/\.[^.]+$/, '')}.jpg`, { type: 'image/jpeg' });
  } catch {
    return file;
  }
}

/**
 * Two ways to bring in a photo of a piece: the camera, or a picture already on the device (a
 * photo, a product page screenshot). `layout="card"` stacks them to fill a furniture card.
 */
export function PhotoButtons({ onPicked, disabled = false, layout = 'row' }: { onPicked(file: File): void; disabled?: boolean; layout?: 'row' | 'card' }) {
  const camera = useRef<HTMLInputElement>(null);
  const library = useRef<HTMLInputElement>(null);
  const take = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) onPicked(f);
    e.target.value = '';
  };
  const button = layout === 'card'
    ? 'flex min-h-0 flex-1 flex-col items-center justify-center gap-1 rounded-lg text-[13px] font-semibold text-muted hover:text-text active:scale-[0.97] disabled:opacity-60'
    : 'flex h-9 flex-1 items-center justify-center gap-1.5 rounded-full border border-line bg-raised px-3 text-xs font-semibold hover:border-line-strong active:scale-[0.97] disabled:opacity-60';
  return (
    <div className={layout === 'card' ? 'flex h-full min-h-36 w-full flex-col gap-1 rounded-xl border-2 border-dashed border-line-strong/60 p-1' : 'flex gap-2'} data-testid="photo-buttons">
      <input ref={camera} type="file" accept="image/*" capture="environment" className="sr-only" data-testid="capture-input" onChange={take} />
      <input ref={library} type="file" accept="image/png,image/jpeg,image/webp,image/*" className="sr-only" data-testid="import-input" onChange={take} />
      <button type="button" disabled={disabled} onClick={() => camera.current?.click()} aria-label="Take a photo of a piece" className={button}>
        <IconCamera width={layout === 'card' ? 20 : 15} height={layout === 'card' ? 20 : 15} /> Take a photo
      </button>
      {layout === 'card' && <span aria-hidden className="mx-3 h-px shrink-0 bg-line" />}
      <button type="button" disabled={disabled} onClick={() => library.current?.click()} aria-label="Import a photo of a piece" className={button}>
        <IconUpload width={layout === 'card' ? 20 : 15} height={layout === 'card' ? 20 : 15} /> Import a photo
      </button>
    </div>
  );
}

/**
 * The chosen photo, to tap the one thing to turn into 3D (SAM cuts it out, then builds the model).
 * "Every piece" sends it without a spot: up to four pieces are found in it.
 */
export function TapToPick({ file, busy, onSubmit, onCancel }: { file: File; busy: boolean; onSubmit(at: PhotoPoint | undefined): void; onCancel(): void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [at, setAt] = useState<PhotoPoint | null>(null);
  useEffect(() => {
    const u = URL.createObjectURL(file);
    setUrl(u);
    setAt(null);
    return () => URL.revokeObjectURL(u);
  }, [file]);

  const tap = (e: MouseEvent<HTMLImageElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setAt({ x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) });
  };

  return (
    <div className="space-y-2" data-testid="photo-tap">
      <p className="text-[13px] font-semibold">{at ? 'That one? Make it 3D, or tap somewhere else.' : 'Tap the piece you want in 3D.'}</p>
      <div className="relative mx-auto w-fit overflow-hidden rounded-xl border border-line bg-ink">
        {url && (
          // A local object URL: next/image can't optimize it.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={url} alt="Your photo: tap the piece to add" draggable={false} onClick={tap} data-testid="photo-tap-image"
            className={cn('block max-h-[42dvh] w-auto max-w-full cursor-crosshair select-none', busy && 'opacity-60')} />
        )}
        {at && (
          <span aria-hidden className="pointer-events-none absolute h-8 w-8 -translate-x-1/2 -translate-y-1/2 rounded-full border-[3px] border-white bg-main/30 shadow-[0_0_0_2px_rgba(0,0,0,0.45)]"
            style={{ left: `${at.x * 100}%`, top: `${at.y * 100}%` }} />
        )}
      </div>
      <div className="flex gap-2">
        <button type="button" disabled={!at || busy} onClick={() => at && onSubmit(at)} data-testid="photo-tap-submit"
          className="flex h-10 flex-1 items-center justify-center gap-2 rounded-full bg-main text-sm font-semibold text-on-main disabled:opacity-50">
          {busy ? <Spinner /> : null} {busy ? 'Sending…' : 'Make it 3D'}
        </button>
        <button type="button" disabled={busy} onClick={onCancel} className="h-10 rounded-full border border-line bg-raised px-4 text-sm font-semibold disabled:opacity-50">
          Cancel
        </button>
      </div>
      <button type="button" disabled={busy} onClick={() => onSubmit(undefined)} className="w-full text-center text-xs font-semibold text-muted underline underline-offset-2 hover:text-text disabled:opacity-50">
        Or add every piece in the photo
      </button>
    </div>
  );
}
