/**
 * Webcam to hand landmarks, in the browser: MediaPipe's HandLandmarker on each new camera frame.
 * Landmarks are mirrored before they leave here, as HoloTouch mirrors its frames: moving a hand
 * to the right moves it to the right on screen.
 *
 * Inference runs in a Web Worker (public/hand-worker.mjs) so it never stalls the 3D render: the
 * camera stays here, each new frame goes over as a transferred ImageBitmap, and a frame that
 * arrives while the worker is busy is dropped rather than queued. Where workers or OffscreenCanvas
 * are missing, the same landmarker runs on the main thread instead.
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

/** One inference, mirrored to how the user sees themselves. */
function toFrame(stamp: number, aspect: number, landmarks: NormalizedLandmark[][], worldLandmarks: Landmark[][], sides: string[]): FrameSample {
  const hands: HandSample[] = landmarks.map((lm, i) => ({
    image: lm.map((p) => ({ x: 1 - p.x, y: p.y, z: p.z })),
    world: (worldLandmarks[i] ?? []).map((p) => ({ x: -p.x, y: p.y, z: p.z })),
    // MediaPipe names hands as if the picture were mirrored; this one is not, so swap.
    side: sides[i] === 'Left' ? 'R' : 'L',
  }));
  return { t: stamp / 1000, aspect, hands: hands.filter((h) => h.world.length === 21) };
}

async function createLandmarker(): Promise<HandLandmarker> {
  const { FilesetResolver, HandLandmarker } = await import('@mediapipe/tasks-vision');
  const fileset = await FilesetResolver.forVisionTasks(WASM_BASE);
  const options = (delegate: 'GPU' | 'CPU') => ({
    baseOptions: { modelAssetPath: config.handModelUrl, delegate },
    runningMode: 'VIDEO' as const,
    numHands: 2,
    minHandDetectionConfidence: 0.5,
    minHandPresenceConfidence: 0.5,
    minTrackingConfidence: 0.5,
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
  stamp: number;
  width: number;
  height: number;
  landmarks: NormalizedLandmark[][];
  worldLandmarks: Landmark[][];
  sides: string[];
}

/** Inference in a worker, or in this thread. Either way: hand it frames, get FrameSamples back. */
interface Detector {
  /** Send a frame; returns false when it was dropped because the detector is still busy. */
  detect(video: HTMLVideoElement, stamp: number): boolean;
  close(): void;
}

const workersAvailable = () => typeof Worker !== 'undefined' && typeof OffscreenCanvas !== 'undefined' && typeof createImageBitmap === 'function';

/** Starts the worker and waits until its landmarker is loaded. */
function startWorkerDetector(onFrame: (frame: FrameSample) => void): Promise<Detector> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(WORKER_URL, { type: 'module', name: 'hand-tracking' });
    let busy = false;
    let ready = false;
    let closed = false;
    const detector: Detector = {
      detect(video, stamp) {
        if (busy || closed) return false;
        busy = true;
        createImageBitmap(video).then(
          (bitmap) => {
            if (closed) return bitmap.close();
            worker.postMessage({ type: 'frame', bitmap, stamp }, [bitmap]);
          },
          () => {
            busy = false;
          },
        );
        return true;
      },
      close() {
        closed = true;
        worker.postMessage({ type: 'close' });
        // Give it a moment to free the landmarker, then make sure it is gone.
        setTimeout(() => worker.terminate(), 500);
      },
    };
    worker.onmessage = (e: MessageEvent<WorkerResult | { type: 'ready'; delegate: string } | { type: 'error'; message: string }>) => {
      const msg = e.data;
      if (msg.type === 'ready') {
        ready = true;
        console.info(`[hands] tracking in a worker (${msg.delegate})`);
        resolve(detector);
      } else if (msg.type === 'error') {
        worker.terminate();
        reject(new Error(msg.message));
      } else if (msg.type === 'result') {
        busy = false;
        if (!closed) onFrame(toFrame(msg.stamp, msg.width / Math.max(msg.height, 1), msg.landmarks, msg.worldLandmarks, msg.sides));
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
        const r = landmarker.detectForVideo(video, stamp);
        onFrame(toFrame(stamp, video.videoWidth / Math.max(video.videoHeight, 1), r.landmarks, r.worldLandmarks, r.handedness.map((h) => h[0]?.categoryName ?? '')));
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
    video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } },
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
  // Both at once: the model download and the camera permission prompt take the longest.
  const [cam, model] = await Promise.allSettled([openCamera(), startDetector((f) => !stopped && onFrame(f))]);
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

  const loop = () => {
    if (stopped) return;
    raf = requestAnimationFrame(loop);
    // One inference per camera frame, not per display frame.
    if (video.readyState < 2 || video.currentTime === lastFrameTime) return;
    // The landmarker wants strictly increasing timestamps, in ms.
    const stamp = Math.max(performance.now(), lastStamp + 1);
    // A frame the worker is too busy for is skipped, and the next new one is tried.
    if (!detector.detect(video, stamp)) return;
    lastFrameTime = video.currentTime;
    lastStamp = stamp;
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
