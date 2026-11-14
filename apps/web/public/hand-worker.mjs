// Hand landmark inference off the main thread (see src/lib/hands/tracker.ts, which owns the camera
// and maps the results). Plain, unbundled ES module: MediaPipe loads its wasm with a runtime
// `import()` that a bundler would rewrite. /mediapipe/ is copied there at install
// (scripts/copy-matterport-assets.mjs).
//
// Messages in:  {type: 'init', modelUrl}  ·  {type: 'frame', bitmap: ImageBitmap, stamp: number}
// Messages out: {type: 'ready', delegate}  ·  {type: 'error', message}
//               {type: 'result', stamp, width, height, landmarks, worldLandmarks, sides}

import { FilesetResolver, HandLandmarker } from '/mediapipe/vision_bundle.mjs';

let landmarker = null;

async function init(modelUrl) {
  // `true`: the ES-module build of the wasm loader, which a module worker can import.
  const fileset = await FilesetResolver.forVisionTasks('/mediapipe/wasm', true);
  const options = (delegate) => ({
    baseOptions: { modelAssetPath: modelUrl, delegate },
    runningMode: 'VIDEO',
    numHands: 2,
    minHandDetectionConfidence: 0.5,
    minHandPresenceConfidence: 0.5,
    minTrackingConfidence: 0.5,
  });
  try {
    landmarker = await HandLandmarker.createFromOptions(fileset, options('GPU'));
    return 'GPU';
  } catch (err) {
    console.warn('[hands worker] GPU delegate unavailable, using the CPU', err);
    landmarker = await HandLandmarker.createFromOptions(fileset, options('CPU'));
    return 'CPU';
  }
}

self.onmessage = async (e) => {
  const msg = e.data;
  if (msg.type === 'init') {
    try {
      const delegate = await init(msg.modelUrl);
      self.postMessage({ type: 'ready', delegate });
    } catch (err) {
      self.postMessage({ type: 'error', message: err instanceof Error ? err.message : String(err) });
    }
    return;
  }
  if (msg.type === 'frame') {
    const { bitmap, stamp } = msg;
    try {
      if (!landmarker) return;
      const r = landmarker.detectForVideo(bitmap, stamp);
      self.postMessage({
        type: 'result',
        stamp,
        width: bitmap.width,
        height: bitmap.height,
        landmarks: r.landmarks,
        worldLandmarks: r.worldLandmarks,
        sides: r.handedness.map((h) => h[0]?.categoryName ?? ''),
      });
    } catch (err) {
      // One bad frame is not fatal: report it and carry on with the next.
      self.postMessage({ type: 'result', stamp, width: bitmap.width, height: bitmap.height, landmarks: [], worldLandmarks: [], sides: [], warning: String(err) });
    } finally {
      bitmap.close();
    }
    return;
  }
  if (msg.type === 'close') {
    landmarker?.close();
    landmarker = null;
    self.close();
  }
};
