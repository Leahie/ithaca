// Copies MediaPipe's wasm for hand tracking next to the app (public/mediapipe, gitignored).
import { cp, access } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

try {
  // MediaPipe's wasm for hand tracking, served from public/mediapipe/wasm (gitignored).
  const pkg = path.dirname(require.resolve('@mediapipe/tasks-vision'));
  await cp(path.join(pkg, 'wasm'), path.join(here, '..', 'public', 'mediapipe', 'wasm'), { recursive: true });
  // The ES-module bundle, for public/hand-worker.mjs: a worker the bundler does not touch.
  await cp(path.join(pkg, 'vision_bundle.mjs'), path.join(here, '..', 'public', 'mediapipe', 'vision_bundle.mjs'));
  console.log('mediapipe wasm and bundle copied to public/mediapipe');
} catch (err) {
  // A missing piece disables hand control, it does not break install.
  console.warn(`mediapipe wasm not copied (${err.message}); hand control will be unavailable`);
}
