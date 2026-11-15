'use client';

import { Edges, useGLTF } from '@react-three/drei';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Suspense, useEffect, useMemo, useRef, type MutableRefObject } from 'react';
import { Box3, Vector3, type Group, type Mesh } from 'three';
import { drawSkeletons, watchCanvas } from '@/components/hands/HandControl';
import { AssetBoundary } from '@/components/house3d/House3D';
import { GLASS_PANEL, cn } from '@/components/ui';
import type { HandView } from '@/lib/hands/engine';
import { handEngine, onHandTick, useHands } from '@/lib/hands/store';
import { studioLive, useStudio } from '@/lib/studio/store';

/**
 * The room studio's camera panel: the camera picture with the tracked hands drawn on it, and the
 * piece of furniture held "in the hands". Two open hands held apart hold it between them, as wide
 * as the gap, so the size being marked can be seen; one open hand carries the chosen piece.
 */

const SUN = '#f1c84b';
/** How the held piece is drawn: its width against the gap between the palms, how far above their
 * midpoint it sits (in gaps), its tilt toward the viewer, and how fast it follows the hands (s). */
const HELD = { span: 0.82, lift: 0.1, tiltRad: (28 * Math.PI) / 180, easeS: 0.06, showS: 0.12, swayRad: 0.3, spinRadPerS: 0.9 } as const;
/** On one open hand: the piece's width in palm widths, and how far above the palm its foot is. */
const ON_PALM = { width: 1.5, foot: 0.55 } as const;

type Ratios = { h: number; d: number };

/** Height and depth of the ghost box, over its width: the chosen piece's, else what the hands frame. */
function ghostRatios(): Ratios {
  const s = useStudio.getState();
  const dims = s.piece?.dims;
  if (dims && dims.w > 0) return { h: dims.h / dims.w, d: dims.d / dims.w };
  const framed = studioLive.framing ?? s.mark;
  const w = framed?.width ?? null;
  return { h: 0.45, d: w && framed?.depth ? framed.depth / w : 0.5 };
}

/** A hand's palm centre in the panel, in CSS pixels from its middle (x right, y up). */
function palm(hand: HandView, w: number, h: number): [number, number] {
  const p = hand.landmarks;
  const a = p[0]!;
  const b = p[5]!;
  const c = p[17]!;
  return [((a.x + b.x + c.x) / 3 - 0.5) * w, (0.5 - (a.y + b.y + c.y) / 3) * h];
}

/** The size being marked, before a piece is chosen: a box one unit wide. */
function Ghost() {
  const mesh = useRef<Mesh>(null);
  useFrame(() => {
    const { h, d } = ghostRatios();
    mesh.current?.scale.set(1, h, d);
  });
  return (
    <mesh ref={mesh}>
      <boxGeometry args={[1, 1, 1]} />
      <meshBasicMaterial color={SUN} transparent opacity={0.18} depthWrite={false} toneMapped={false} />
      <Edges color={SUN} lineWidth={2} />
    </mesh>
  );
}

/** The chosen piece's model, centred and one unit wide. */
function Model({ url, shape }: { url: string; shape: MutableRefObject<Ratios | null> }) {
  const gltf = useGLTF(url);
  const { object, scale, ratios } = useMemo(() => {
    const root = gltf.scene.clone(true);
    const box = new Box3().setFromObject(root);
    const size = box.getSize(new Vector3());
    const w = Math.max(size.x, 1e-4);
    root.position.sub(box.getCenter(new Vector3()));
    return { object: root, scale: 1 / w, ratios: { h: size.y / w, d: size.z / w } };
  }, [gltf.scene]);
  useEffect(() => {
    shape.current = ratios;
    return () => {
      shape.current = null;
    };
  }, [ratios, shape]);
  return (
    <group scale={scale}>
      <primitive object={object} />
    </group>
  );
}

const noop = () => {};

/** The piece (or the ghost of its size) where the hands hold it. World units are CSS pixels. */
function Held({ url }: { url: string | null }) {
  const size = useThree((s) => s.size);
  /** Place, roll in the picture's plane, and size. */
  const rig = useRef<Group>(null);
  /** The tilt toward the viewer and the turn about the piece's own upright. */
  const turn = useRef<Group>(null);
  /** The loaded model's height and depth over its width; null while the ghost is shown. */
  const shape = useRef<Ratios | null>(null);
  const now = useRef({ x: 0, y: 0, scale: 0, roll: 0, yaw: 0, shown: 0 });
  // Drawn only while there is something to draw: a hand in view, or the piece still fading out.
  // A second 3D picture drawn sixty times a second for an empty panel is graphics-card time the
  // hand model waits behind.
  const invalidate = useThree((s) => s.invalidate);
  useEffect(
    () =>
      onHandTick(() => {
        if (handEngine.hands.length > 0 || now.current.shown > 0.01) invalidate();
      }),
    [invalidate],
  );

  useFrame(({ clock }, delta) => {
    const g = rig.current;
    const t = turn.current;
    if (!g || !t) return;
    const dt = Math.min(delta, 0.1);
    const s = now.current;
    const hands = handEngine.hands.filter((h) => !h.stale && h.landmarks.length >= 21);
    const [first, second] = hands;
    let target: { x: number; y: number; scale: number; roll: number } | null = null;
    let spin = false;
    if (hands.length === 2 && first && second && !first.pinching && !second.pinching) {
      // Held between two open hands: as wide as the gap, lying along the line between them.
      let a = palm(first, size.width, size.height);
      let b = palm(second, size.width, size.height);
      if (a[0] > b[0]) [a, b] = [b, a];
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      const gap = Math.hypot(dx, dy);
      target = { x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2 + HELD.lift * gap, scale: HELD.span * gap, roll: Math.atan2(dy, dx) };
    } else if (hands.length === 1 && first && !first.pinching && first.pose === 'open' && useStudio.getState().piece) {
      // Carried on one open hand, turning slowly.
      const c = palm(first, size.width, size.height);
      const p5 = first.landmarks[5]!;
      const p17 = first.landmarks[17]!;
      const palmWidth = Math.hypot((p5.x - p17.x) * size.width, (p5.y - p17.y) * size.height);
      const width = ON_PALM.width * palmWidth;
      const tall = (shape.current ?? ghostRatios()).h * width;
      target = { x: c[0], y: c[1] + ON_PALM.foot * palmWidth + tall / 2, scale: width, roll: 0 };
      spin = true;
    }
    if (target) {
      // Appearing: it starts where the hands are, not where it was last seen.
      const k = s.shown < 0.02 ? 1 : 1 - Math.exp(-dt / HELD.easeS);
      s.x += (target.x - s.x) * k;
      s.y += (target.y - s.y) * k;
      s.scale += (target.scale - s.scale) * k;
      s.roll += (target.roll - s.roll) * k;
    }
    s.shown += ((target ? 1 : 0) - s.shown) * (1 - Math.exp(-dt / HELD.showS));
    if (spin) {
      s.yaw += dt * HELD.spinRadPerS;
    } else {
      // Back from the spin the short way, then a slow sway.
      s.yaw = Math.atan2(Math.sin(s.yaw), Math.cos(s.yaw));
      s.yaw += (Math.sin(clock.elapsedTime * 0.8) * HELD.swayRad - s.yaw) * (1 - Math.exp(-dt / 0.25));
    }
    g.visible = s.shown > 0.01;
    if (!g.visible) return;
    g.position.set(s.x, s.y, 0);
    g.rotation.z = s.roll;
    g.scale.setScalar(Math.max(s.scale * s.shown, 1e-3));
    t.rotation.set(HELD.tiltRad, s.yaw, 0);
  });

  return (
    <group ref={rig} visible={false}>
      <group ref={turn}>
        {url ? (
          <AssetBoundary key={url} label="The piece" onError={noop} fallback={<Ghost />}>
            <Suspense fallback={<Ghost />}>
              <Model url={url} shape={shape} />
            </Suspense>
          </AssetBoundary>
        ) : (
          <Ghost />
        )}
      </group>
    </group>
  );
}

/** The camera picture with the hands and the piece they hold. */
export function HandCam({ className }: { className?: string }) {
  const status = useHands((s) => s.status);
  const error = useHands((s) => s.error);
  const video = useHands((s) => s.video);
  const pieceUrl = useStudio((s) => s.piece?.url ?? null);
  const host = useRef<HTMLDivElement | null>(null);
  const bones = useRef<HTMLCanvasElement | null>(null);
  const label = useRef<HTMLSpanElement | null>(null);
  const meter = useRef<HTMLSpanElement | null>(null);

  // The camera picture is the tracker's own <video>: put it in the panel.
  useEffect(() => {
    const el = host.current;
    if (!el || !video) return;
    video.className = 'absolute inset-0 h-full w-full -scale-x-100 object-cover';
    el.prepend(video);
    return () => video.remove();
  }, [video]);

  useEffect(() => {
    if (status !== 'on') {
      if (label.current) label.current.textContent = '';
      if (meter.current) meter.current.textContent = '';
      return;
    }
    const [surface, unwatch] = bones.current ? watchCanvas(bones.current) : [null, () => {}];
    let said = '';
    let meterAt = 0;
    const off = onHandTick(() => {
      // How the tracking keeps up, a few times a second: what runs the hand model, its results a
      // second, and how long a picture takes to become a result on this page (ms).
      const t = performance.now();
      if (meter.current && t - meterAt >= 250) {
        meterAt = t;
        const { rate, delayMs, on } = handEngine.stats;
        meter.current.textContent = rate ? `${on || '?'} ${rate.toFixed(0)}/s · ${delayMs.toFixed(0)} ms` : '';
      }
      const hands = handEngine.hands;
      // The hands in the picture change only when the tracker has a new result.
      if (surface) {
        let key = `${handEngine.results}`;
        for (const hand of hands) key += `|${hand.id}${hand.side}${hand.pinching ? 'p' : ''}${hand.stale ? 's' : ''}`;
        if (key !== surface.drawn) {
          drawSkeletons(surface, hands);
          surface.drawn = key;
        }
      }
      const framing = studioLive.framing;
      const piece = useStudio.getState().piece;
      const twoOpen = hands.filter((h) => !h.stale && !h.pinching).length === 2;
      const text = twoOpen && framing && typeof framing.width === 'number' ? `${(Math.round(framing.width / 0.05) * 0.05).toFixed(2)} m wide` : piece ? `${piece.label} · ${piece.dims.w.toFixed(2)} m` : '';
      if (label.current && text !== said) label.current.textContent = said = text;
    });
    return () => {
      off();
      unwatch();
    };
  }, [status]);

  return (
    <div className={cn(GLASS_PANEL, 'overflow-hidden rounded-2xl', className)} data-testid="studio-handcam">
      <div ref={host} className="relative aspect-video w-full bg-[#14181c]">
        <canvas ref={bones} className="absolute inset-0 h-full w-full" aria-hidden />
        {status === 'on' && (
          <div className="pointer-events-none absolute inset-0" aria-hidden>
            <Canvas orthographic frameloop="demand" dpr={[1, 1.5]} gl={{ alpha: true, antialias: true }} camera={{ position: [0, 0, 1000], zoom: 1, near: 1, far: 4000 }} style={{ pointerEvents: 'none' }}>
              <ambientLight intensity={1.6} />
              <directionalLight position={[200, 300, 400]} intensity={2} />
              <Held url={pieceUrl} />
            </Canvas>
          </div>
        )}
        <span className="pointer-events-none absolute top-2 left-2.5 text-[11px] font-semibold tracking-[0.08em] text-white/70 uppercase">Your hands</span>
        <span ref={meter} className="pointer-events-none absolute top-1.5 right-1.5 rounded bg-black/55 px-1.5 font-mono text-[10px] leading-4 text-white/85 tabular-nums empty:hidden" />
        <span ref={label} className="pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full bg-black/60 px-3 py-1 font-display text-sm font-semibold whitespace-nowrap text-white tabular-nums empty:hidden" />
        {status === 'starting' && <span className="absolute inset-0 grid place-items-center font-display text-base font-medium text-white/80">Starting the camera…</span>}
        {status === 'error' && (
          <div className="absolute inset-0 grid place-items-center p-4">
            <p className="text-center text-[13px] leading-5 text-[#ffb4a8]">{error ?? 'The camera could not be started.'}</p>
          </div>
        )}
        {status === 'off' && (
          <div className="absolute inset-0 grid place-items-center">
            <button type="button" onClick={() => void useHands.getState().start()} className="min-h-11 rounded-full bg-white/15 px-4 font-display text-sm font-semibold text-white hover:bg-white/25">
              Turn the camera on
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
