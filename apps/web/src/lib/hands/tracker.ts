/**
 * Webcam to hand landmarks, in the browser: MediaPipe's HandLandmarker on each new camera frame.
 * Landmarks are mirrored before they leave here, as HoloTouch mirrors its frames: moving a hand
 * to the right moves it to the right on screen.
 */

import type { HandLandmarker } from '@mediapipe/tasks-vision';
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

/** Where the MediaPipe wasm is served from: copied into public/ at install (scripts/copy-matterport-assets.mjs). */
const WASM_BASE = '/mediapipe/wasm';

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
  // Both at once: the model download and the camera permission prompt take the longest.
  const [cam, model] = await Promise.allSettled([openCamera(), createLandmarker()]);
  if (cam.status === 'rejected' || model.status === 'rejected') {
    if (cam.status === 'fulfilled') closeCamera(cam.value);
    if (model.status === 'fulfilled') model.value.close();
    throw cam.status === 'rejected' ? new CameraError(cam.reason) : new ModelError(model.status === 'rejected' ? model.reason : null);
  }
  const video = cam.value;
  const landmarker = model.value;
  let raf = 0;
  let lastFrameTime = -1;
  let lastStamp = 0;
  let stopped = false;

  const loop = () => {
    if (stopped) return;
    raf = requestAnimationFrame(loop);
    // One inference per camera frame, not per display frame.
    if (video.readyState < 2 || video.currentTime === lastFrameTime) return;
    lastFrameTime = video.currentTime;
    // The landmarker wants strictly increasing timestamps, in ms.
    const stamp = Math.max(performance.now(), lastStamp + 1);
    lastStamp = stamp;
    let result;
    try {
      result = landmarker.detectForVideo(video, stamp);
    } catch (err) {
      console.warn('[hands] detection failed', err);
      return;
    }
    const aspect = video.videoWidth / Math.max(video.videoHeight, 1);
    const hands: HandSample[] = result.landmarks.map((lm, i) => ({
      image: lm.map((p) => ({ x: 1 - p.x, y: p.y, z: p.z })),
      world: (result.worldLandmarks[i] ?? []).map((p) => ({ x: -p.x, y: p.y, z: p.z })),
      // MediaPipe names hands as if the picture were mirrored; this one is not, so swap.
      side: result.handedness[i]?.[0]?.categoryName === 'Left' ? 'R' : 'L',
    }));
    onFrame({ t: stamp / 1000, aspect, hands: hands.filter((h) => h.world.length === 21) });
  };
  raf = requestAnimationFrame(loop);

  return {
    video,
    stop() {
      stopped = true;
      cancelAnimationFrame(raf);
      landmarker.close();
      closeCamera(video);
    },
  };
}
