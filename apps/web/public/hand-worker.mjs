// Hand landmark inference off the main thread (see src/lib/hands/tracker.ts, which owns the camera
// and maps the results). Plain, unbundled ES module: MediaPipe loads its wasm with a runtime
// `import()` that a bundler would rewrite. /mediapipe/ is copied there at install
// (scripts/copy-assets.mjs).
//
// Messages in:  {type: 'init', modelUrl}
//               {type: 'stream', readable: ReadableStream<VideoFrame>}   (Chrome, Edge: the camera itself)
//               {type: 'frame', bitmap: ImageBitmap, at: number}         (elsewhere: one frame at a time)
// Messages out: {type: 'ready', delegate}  ·  {type: 'error', message}  ·  {type: 'stream-error', message}
//               {type: 'result', at, width, height, landmarks, worldLandmarks, sides, ms}
//               {type: 'delegate', delegate, ms, was, wasMs}   (the tracker changed how it runs)
//
// `at` is when the picture was taken, in ms since the epoch (performance.timeOrigin + now): a
// worker's performance.now() counts from a different moment than the page's.
//
// With a stream, frames come straight from the camera to here: the page's thread, busy drawing
// the house, is not in the way. Each frame is worked on as soon as the last one is done, and the
// ones that came meanwhile are dropped at the source (maxBufferSize 1), so the newest is used.
//
// Which processor the model runs on is chosen by measuring, not assumed. The graphics card is
// usually the faster one, but here it is shared with the 3D house: every inference waits its
// turn behind the scene's frame, and where WebGL is drawn in software (no usable card) it is far
// slower than the plain CPU. So: where WebGL is software, the CPU is used from the start; else
// the graphics card, and if it turns out slower than the camera delivers frames, the CPU is
// timed on the same pictures and the faster of the two is kept.

import { FilesetResolver, HandLandmarker } from '/mediapipe/vision_bundle.mjs';

let fileset = null;
let modelUrl = '';
/** The landmarker in use, and what it runs on ('GPU' or 'CPU'). */
let landmarker = null;
let delegate = 'GPU';

/** The first inferences compile shaders and warm the model up: they say nothing about the pace. */
const WARMUP_FRAMES = 5;
/** How many recent inferences the pace is read from (their median). */
const WINDOW = 15;
/** A camera frame comes every 33 ms: an inference slower than this drops frames. */
const SLOW_MS = 30;
/** A comparison runs both on this many pictures, after two to warm the newcomer up. */
const TRIAL_FRAMES = 10;
const TRIAL_WARMUP = 2;
/** The CPU must be this much quicker to take over (a near tie stays where it is). */
const SWITCH_BELOW = 0.8;
/** The pace can change (the house gets heavier to draw): compare once more, but only when the
 * graphics card has become this much slower than it was at the last comparison, and not at once. */
const MAX_TRIALS = 2;
const TRIAL_GAP_MS = 15_000;
const WORSE_BY = 1.5;

let seen = 0;
let times = [];
let trials = 0;
let lastTrialAt = -Infinity;
/** What the graphics card took per frame at the last comparison it won. */
let lastGpuMs = 0;
let building = false;
/** A comparison in progress: the other landmarker and both sets of timings. */
let trial = null;

const options = (on) => ({
  baseOptions: { modelAssetPath: modelUrl, delegate: on },
  runningMode: 'VIDEO',
  numHands: 2,
  minHandDetectionConfidence: 0.5,
  // A little lower than the defaults: a pinching hand hides its own fingertips, and a hand lost
  // there is found again only by the slower palm search.
  minHandPresenceConfidence: 0.4,
  minTrackingConfidence: 0.4,
});

const create = (on) => HandLandmarker.createFromOptions(fileset, options(on));

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0;
}

/** WebGL drawn by the processor (no graphics card the browser will use): the "GPU" path is then the slow one. */
function softwareWebGl() {
  try {
    const canvas = new OffscreenCanvas(1, 1);
    const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl');
    if (!gl) return false; // no WebGL at all: creating the landmarker says so, as before
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    const name = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER) ?? '');
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return /swiftshader|llvmpipe|softpipe|software|basic render/i.test(name);
  } catch {
    return false;
  }
}

async function init(url) {
  modelUrl = url;
  // `true`: the ES-module build of the wasm loader, which a module worker can import.
  fileset = await FilesetResolver.forVisionTasks('/mediapipe/wasm', true);
  if (softwareWebGl()) {
    console.info('[hands worker] WebGL is drawn in software here: tracking on the CPU');
    landmarker = await create('CPU');
    delegate = 'CPU';
    return;
  }
  try {
    landmarker = await create('GPU');
    delegate = 'GPU';
  } catch (err) {
    console.warn('[hands worker] GPU delegate unavailable, using the CPU', err);
    landmarker = await create('CPU');
    delegate = 'CPU';
  }
}

/** After each inference on the graphics card: is it keeping up? If not, time the CPU against it. */
function consider(ms) {
  seen++;
  if (seen <= WARMUP_FRAMES) return;
  times.push(ms);
  if (times.length > WINDOW) times.shift();
  if (delegate !== 'GPU' || building || times.length < WINDOW || trials >= MAX_TRIALS) return;
  if (performance.now() - lastTrialAt < TRIAL_GAP_MS || median(times) <= Math.max(SLOW_MS, WORSE_BY * lastGpuMs)) return;
  building = true;
  trials++;
  create('CPU').then(
    (other) => {
      building = false;
      if (!landmarker) return other.close(); // closed while it was being made
      trial = { other, mine: [], theirs: [], warmup: TRIAL_WARMUP };
    },
    (err) => {
      building = false;
      lastTrialAt = performance.now();
      console.warn('[hands worker] the CPU could not be tried', err);
    },
  );
}

/** The comparison is over: keep the faster landmarker, close the other. */
function settle() {
  const { other, mine, theirs } = trial;
  trial = null;
  lastTrialAt = performance.now();
  const gpuMs = median(mine);
  const cpuMs = median(theirs);
  if (cpuMs < gpuMs * SWITCH_BELOW) {
    landmarker.close();
    landmarker = other;
    delegate = 'CPU';
    times = [];
    self.postMessage({ type: 'delegate', delegate: 'CPU', ms: cpuMs, was: 'GPU', wasMs: gpuMs });
  } else {
    other.close();
    lastGpuMs = gpuMs;
    self.postMessage({ type: 'delegate', delegate: 'GPU', ms: gpuMs, was: 'CPU', wasMs: cpuMs });
  }
}

/** MediaPipe wants strictly increasing timestamps (ms); this worker's own clock gives them. */
let lastStamp = 0;
function nextStamp() {
  lastStamp = Math.max(performance.now(), lastStamp + 1);
  return lastStamp;
}

/** One picture through the landmarker; the caller closes it. `at`: when it was taken, epoch ms. */
function run(image, at) {
  if (!landmarker) return;
  const width = image.displayWidth ?? image.width;
  const height = image.displayHeight ?? image.height;
  const stamp = nextStamp();
  try {
    const began = performance.now();
    const r = landmarker.detectForVideo(image, stamp);
    const ms = performance.now() - began;
    // The result goes out before anything else is done with this picture.
    self.postMessage({
      type: 'result',
      at,
      width,
      height,
      landmarks: r.landmarks,
      worldLandmarks: r.worldLandmarks,
      sides: r.handedness.map((h) => h[0]?.categoryName ?? ''),
      ms,
    });
    if (!trial) {
      consider(ms);
    } else {
      // The same picture through the other one, so the two are timed on the same work.
      try {
        const other = performance.now();
        trial.other.detectForVideo(image, stamp);
        const otherMs = performance.now() - other;
        if (trial.warmup > 0) {
          trial.warmup--;
        } else {
          trial.mine.push(ms);
          trial.theirs.push(otherMs);
        }
        if (trial.mine.length >= TRIAL_FRAMES) settle();
      } catch (err) {
        // The landmarker in use has already answered for this picture: only the trial ends.
        console.warn('[hands worker] the CPU trial failed', err);
        try {
          trial?.other.close();
        } catch {}
        trial = null;
        lastTrialAt = performance.now();
      }
    }
  } catch (err) {
    // One bad frame is not fatal: report it and carry on with the next.
    self.postMessage({ type: 'result', at, width, height, landmarks: [], worldLandmarks: [], sides: [], warning: String(err) });
  }
}

/** The camera's frames, read here one after another until the stream ends or the worker closes. */
let reader = null;
let closing = false;
async function readStream(readable) {
  reader?.cancel().catch(() => {});
  const mine = readable.getReader();
  reader = mine;
  try {
    for (;;) {
      const { value: frame, done } = await mine.read();
      if (done) break;
      const at = performance.timeOrigin + performance.now();
      try {
        run(frame, at);
      } finally {
        // A frame kept open holds one of the camera's few buffers: the camera stalls without it.
        frame.close();
      }
    }
  } catch (err) {
    if (!closing && reader === mine) self.postMessage({ type: 'stream-error', message: err instanceof Error ? err.message : String(err) });
  }
}

self.onmessage = async (e) => {
  const msg = e.data;
  if (msg.type === 'init') {
    try {
      await init(msg.modelUrl);
      self.postMessage({ type: 'ready', delegate });
    } catch (err) {
      self.postMessage({ type: 'error', message: err instanceof Error ? err.message : String(err) });
    }
    return;
  }
  if (msg.type === 'stream') {
    void readStream(msg.readable);
    return;
  }
  if (msg.type === 'frame') {
    try {
      run(msg.bitmap, msg.at);
    } finally {
      msg.bitmap.close();
    }
    return;
  }
  if (msg.type === 'close') {
    closing = true;
    reader?.cancel().catch(() => {});
    reader = null;
    trial?.other.close();
    trial = null;
    landmarker?.close();
    landmarker = null;
    self.close();
  }
};
