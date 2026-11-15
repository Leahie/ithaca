/**
 * Webcam to hand landmarks, in the browser: MediaPipe's HandLandmarker on each new camera frame.
 * Landmarks are mirrored before they leave here, as HoloTouch mirrors its frames: moving a hand
 * to the right moves it to the right on screen.
 *
 * Inference runs in a Web Worker (public/hand-worker.mjs) so it never stalls the 3D render. Where
 * the browser can (Chrome, Edge: MediaStreamTrackProcessor), the worker reads the camera itself:
 * this thread, which also draws the house, no longer has to wait for a display frame, copy the
 * picture out and post it, so tracking keeps the camera's pace however heavy the scene. Elsewhere
 * each new frame goes over as a transferred ImageBitmap, and a frame that arrives while the worker
 * is busy is dropped rather than queued. Where workers or OffscreenCanvas are missing, the same
 * landmarker runs on the main thread instead.
 *
 * The picture is kept small (FRAME_PX): the model looks at a 224 px crop of each hand, so a
 * 720p frame buys nothing, and copying one out of the camera and into the worker for every
 * frame costs time on the thread that also draws the house.
 */

import type { HandLandmarker, Landmark, NormalizedLandmark } from '@mediapipe/tasks-vision';
import { config } from '@/lib/config';
import type { Pt } from './pinch';

export type Side = 'L' | 'R';

export interface HandSample {
  /** 21 landmarks, 0..1 of the mirrored frame (x right, y down). */
  image: Pt[];
  /** 21 landmarks in metres, about the hand's centre. */
  world: Pt[];
  /** The user's own left or right hand. */
  side: Side;
}

export interface FrameSample {
  /** performance.now() of the frame, seconds. */
  t: number;
  /** Frame width over height. */
  aspect: number;
  hands: HandSample[];
  /** How long the hand model took on this frame, ms, when known. */
  ms?: number;
  /** How long a one-pixel WebGL read waited just now, ms: the graphics card's queue (now and then, not every frame). */
  gpuMs?: number;
  /** What runs the hand model: 'GPU' or 'CPU', when known. */
  on?: string;
}

export interface Tracking {
  /** The camera picture, for a preview. Not mirrored: show it with scaleX(-1). */
  video: HTMLVideoElement;
  stop(): void;
}

const describe = (err: unknown) => (err instanceof Error ? err.message : String(err));

export class CameraError extends Error {
  constructor(cause: unknown) {
    const name = cause instanceof DOMException ? cause.name : '';
    super(
      name === 'NotAllowedError'
        ? 'Camera access was blocked. Allow the camera for this site and try again.'
        : name === 'NotFoundError'
          ? 'No camera was found.'
          : name === 'NotReadableError'
            ? 'The camera is in use by another app.'
            : `The camera could not be opened: ${describe(cause)}`,
    );
  }
}

export class ModelError extends Error {
  constructor(cause: unknown) {
    super(`The hand tracking model could not be loaded: ${describe(cause)}`);
  }
}

/** Where the MediaPipe wasm is served from: copied into public/ at install (scripts/copy-assets.mjs). */
const WASM_BASE = '/mediapipe/wasm';
const WORKER_URL = '/hand-worker.mjs';
/** The camera picture asked for, and the widest a frame is sent to the tracker (a camera may ignore the request). */
const FRAME_PX = { width: 640, height: 360 } as const;
/** A worker that has not answered a frame for this long is taken to have dropped it (ms). */
const WORKER_SILENT_MS = 3000;

/** One inference, mirrored to how the user sees themselves. */
function toFrame(stamp: number, aspect: number, landmarks: NormalizedLandmark[][], worldLandmarks: Landmark[][], sides: string[], ms?: number, gpuMs?: number, on?: string): FrameSample {
  const hands: HandSample[] = landmarks.map((lm, i) => ({
    image: lm.map((p) => ({ x: 1 - p.x, y: p.y, z: p.z })),
    world: (worldLandmarks[i] ?? []).map((p) => ({ x: -p.x, y: p.y, z: p.z })),
    // MediaPipe names hands as if the picture were mirrored; this one is not, so swap.
    side: sides[i] === 'Left' ? 'R' : 'L',
  }));
  return { t: stamp / 1000, aspect, hands: hands.filter((h) => h.world.length === 21), ms, gpuMs, on };
}

async function createLandmarker(): Promise<HandLandmarker> {
  const { FilesetResolver, HandLandmarker } = await import('@mediapipe/tasks-vision');
  const fileset = await FilesetResolver.forVisionTasks(WASM_BASE);
  const options = (delegate: 'GPU' | 'CPU') => ({
    baseOptions: { modelAssetPath: config.handModelUrl, delegate },
    runningMode: 'VIDEO' as const,
    numHands: 2,
    minHandDetectionConfidence: 0.5,
    minHandPresenceConfidence: 0.4,
    minTrackingConfidence: 0.4,
  });
  try {
    return await HandLandmarker.createFromOptions(fileset, options('GPU'));
  } catch (err) {
    console.warn('[hands] GPU delegate unavailable, using the CPU', err);
    return HandLandmarker.createFromOptions(fileset, options('CPU'));
  }
}

interface WorkerResult {
  type: 'result';
  /** When the picture was taken, ms since the epoch (the worker's clock starts elsewhere). */
  at: number;
  width: number;
  height: number;
  landmarks: NormalizedLandmark[][];
  worldLandmarks: Landmark[][];
  sides: string[];
  /** How long the inference took, ms. */
  ms?: number;
  gpuMs?: number;
  on?: string;
}

/** The worker timed the CPU against the graphics card and says which it kept (see hand-worker.mjs). */
interface WorkerDelegate {
  type: 'delegate';
  delegate: string;
  ms: number;
  was: string;
  wasMs: number;
}

/** Inference in a worker, or in this thread. Either way: hand it frames, get FrameSamples back. */
interface Detector {
  /** Send a frame; returns false when it was dropped because the detector is still busy. */
  detect(video: HTMLVideoElement, stamp: number): boolean;
  /**
   * Give the detector the camera itself, to read frames on its own. True when it now does;
   * `lost` is called if that stops, and frames must then be sent with `detect` again.
   */
  stream?(video: HTMLVideoElement, lost: () => void): boolean;
  close(): void;
}

/** Chrome's reader of a camera track's frames (not in every lib.dom yet). */
type TrackProcessor = new (init: { track: MediaStreamTrack; maxBufferSize?: number }) => { readable: ReadableStream };
const trackProcessor = (): TrackProcessor | undefined => (globalThis as { MediaStreamTrackProcessor?: TrackProcessor }).MediaStreamTrackProcessor;

/** False once this browser has refused to resize a video frame into a bitmap. */
let resizeWorks = true;

/** The frame as a bitmap for the worker, no wider than FRAME_PX. */
function grab(video: HTMLVideoElement): Promise<ImageBitmap> {
  const w = video.videoWidth;
  const h = video.videoHeight;
  if (!resizeWorks || !w || w <= FRAME_PX.width) return createImageBitmap(video);
  const options: ImageBitmapOptions = { resizeWidth: FRAME_PX.width, resizeHeight: Math.round((h * FRAME_PX.width) / w), resizeQuality: 'low' };
  return createImageBitmap(video, options).catch(() => {
    // A browser that cannot resize a video frame here: send them whole from now on.
    resizeWorks = false;
    return createImageBitmap(video);
  });
}

const workersAvailable = () => typeof Worker !== 'undefined' && typeof OffscreenCanvas !== 'undefined' && typeof createImageBitmap === 'function';

/** Starts the worker and waits until its landmarker is loaded. */
function startWorkerDetector(onFrame: (frame: FrameSample) => void): Promise<Detector> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(WORKER_URL, { type: 'module', name: 'hand-tracking' });
    let busy = false;
    let busySince = 0;
    let ready = false;
    let closed = false;
    /** The copy of the camera track the worker reads, while it does. */
    let streamed: MediaStreamTrack | null = null;
    let onLost: (() => void) | null = null;
    const endStream = () => {
      streamed?.stop();
      streamed = null;
    };
    const detector: Detector = {
      detect(video, stamp) {
        if (closed) return false;
        if (busy) {
          // One lost answer must not stop the tracking for good.
          if (performance.now() - busySince < WORKER_SILENT_MS) return false;
          console.warn('[hands] the tracking worker did not answer a frame; carrying on');
        }
        busy = true;
        busySince = performance.now();
        grab(video).then(
          (bitmap) => {
            if (closed) return bitmap.close();
            worker.postMessage({ type: 'frame', bitmap, at: performance.timeOrigin + stamp }, [bitmap]);
          },
          () => {
            busy = false;
          },
        );
        return true;
      },
      stream(video, lost) {
        const Processor = trackProcessor();
        const track = (video.srcObject as MediaStream | null)?.getVideoTracks()[0];
        if (closed || !Processor || !track) return false;
        // A copy, so the preview keeps playing whatever the worker does with its frames.
        const copy = track.clone();
        try {
          const { readable } = new Processor({ track: copy, maxBufferSize: 1 });
          worker.postMessage({ type: 'stream', readable }, [readable as unknown as Transferable]);
        } catch (err) {
          // No transferable streams here: frames go one at a time instead.
          console.info('[hands] the camera cannot be handed to the worker; sending it frames', err);
          copy.stop();
          return false;
        }
        streamed = copy;
        onLost = lost;
        return true;
      },
      close() {
        closed = true;
        endStream();
        worker.postMessage({ type: 'close' });
        // Give it a moment to free the landmarker, then make sure it is gone.
        setTimeout(() => worker.terminate(), 500);
      },
    };
    worker.onmessage = (e: MessageEvent<WorkerResult | WorkerDelegate | { type: 'ready'; delegate: string } | { type: 'error' | 'stream-error'; message: string }>) => {
      const msg = e.data;
      if (msg.type === 'ready') {
        ready = true;
        console.info(`[hands] tracking in a worker (${msg.delegate})`);
        resolve(detector);
      } else if (msg.type === 'error') {
        worker.terminate();
        reject(new Error(msg.message));
      } else if (msg.type === 'stream-error') {
        console.warn('[hands] the worker stopped reading the camera; sending it frames instead', msg.message);
        endStream();
        busy = false;
        onLost?.();
        onLost = null;
      } else if (msg.type === 'delegate') {
        // The graphics card was not keeping up with the camera, so the worker timed the CPU on the same frames.
        console.info(`[hands] tracking on the ${msg.delegate} (${msg.ms.toFixed(0)} ms a frame; the ${msg.was} took ${msg.wasMs.toFixed(0)} ms)`);
      } else if (msg.type === 'result') {
        busy = false;
        if (!closed) onFrame(toFrame(msg.at - performance.timeOrigin, msg.width / Math.max(msg.height, 1), msg.landmarks, msg.worldLandmarks, msg.sides, msg.ms, msg.gpuMs, msg.on));
      }
    };
    // A module worker that fails to load (old browser, missing file) only says so here.
    worker.onerror = (e) => {
      if (ready) return console.warn('[hands] worker error', e.message);
      worker.terminate();
      reject(new Error(e.message || 'The hand tracking worker could not start.'));
    };
    worker.postMessage({ type: 'init', modelUrl: new URL(config.handModelUrl, window.location.href).toString() });
  });
}

async function startMainThreadDetector(onFrame: (frame: FrameSample) => void): Promise<Detector> {
  const landmarker = await createLandmarker();
  console.info('[hands] tracking on the main thread');
  return {
    detect(video, stamp) {
      try {
        const began = performance.now();
        const r = landmarker.detectForVideo(video, stamp);
        const ms = performance.now() - began;
        onFrame(toFrame(stamp, video.videoWidth / Math.max(video.videoHeight, 1), r.landmarks, r.worldLandmarks, r.handedness.map((h) => h[0]?.categoryName ?? ''), ms));
      } catch (err) {
        console.warn('[hands] detection failed', err);
      }
      return true;
    },
    close: () => landmarker.close(),
  };
}

async function startDetector(onFrame: (frame: FrameSample) => void): Promise<Detector> {
  if (workersAvailable()) {
    try {
      return await startWorkerDetector(onFrame);
    } catch (err) {
      console.warn('[hands] worker unavailable, tracking on the main thread instead', err);
    }
  }
  return startMainThreadDetector(onFrame);
}

async function openCamera(): Promise<HTMLVideoElement> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { facingMode: 'user', width: { ideal: FRAME_PX.width }, height: { ideal: FRAME_PX.height }, aspectRatio: { ideal: FRAME_PX.width / FRAME_PX.height }, frameRate: { ideal: 30 } },
  });
  const video = document.createElement('video');
  video.muted = true;
  video.playsInline = true;
  video.srcObject = stream;
  await video.play();
  return video;
}

function closeCamera(video: HTMLVideoElement): void {
  (video.srcObject as MediaStream | null)?.getTracks().forEach((t) => t.stop());
  video.srcObject = null;
  video.remove();
}

/** Opens the camera and calls `onFrame` with the hands in each new camera frame until stopped. */
export async function startTracking(onFrame: (frame: FrameSample) => void): Promise<Tracking> {
  let stopped = false;
  /** Sends the newest camera frame, if there is one the tracker has not had. Set once the camera is open. */
  let pump = () => {};
  // Both at once: the model download and the camera permission prompt take the longest.
  const [cam, model] = await Promise.allSettled([
    openCamera(),
    startDetector((f) => {
      if (stopped) return;
      onFrame(f);
      // The tracker is free again: give it the next frame now, not at the next display frame.
      pump();
    }),
  ]);
  if (cam.status === 'rejected' || model.status === 'rejected') {
    if (cam.status === 'fulfilled') closeCamera(cam.value);
    if (model.status === 'fulfilled') model.value.close();
    throw cam.status === 'rejected' ? new CameraError(cam.reason) : new ModelError(model.status === 'rejected' ? model.reason : null);
  }
  const video = cam.value;
  const detector = model.value;
  let raf = 0;
  let lastFrameTime = -1;
  let lastStamp = 0;

  // Where it can, the worker reads the camera itself and this thread only takes the results.
  let streaming = detector.stream?.(video, () => (streaming = false)) ?? false;
  if (streaming) console.info('[hands] the worker reads the camera directly');

  let sending = false;
  pump = () => {
    // On the main thread `detect` answers before it returns, which calls this again: once is enough.
    if (stopped || sending || streaming) return;
    // One inference per camera frame, not per display frame.
    if (video.readyState < 2 || video.currentTime === lastFrameTime) return;
    sending = true;
    try {
      // The landmarker wants strictly increasing timestamps, in ms.
      const stamp = Math.max(performance.now(), lastStamp + 1);
      // A frame the worker is too busy for is skipped, and the next new one is tried.
      if (!detector.detect(video, stamp)) return;
      lastFrameTime = video.currentTime;
      lastStamp = stamp;
    } finally {
      sending = false;
    }
  };
  const loop = () => {
    if (stopped) return;
    raf = requestAnimationFrame(loop);
    pump();
  };
  raf = requestAnimationFrame(loop);

  return {
    video,
    stop() {
      stopped = true;
      cancelAnimationFrame(raf);
      detector.close();
      closeCamera(video);
    },
  };
}
