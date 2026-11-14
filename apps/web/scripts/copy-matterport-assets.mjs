// Copies the Matterport Bundle SDK assets next to the app (public/matterport-assets, gitignored),
// and MediaPipe's wasm for hand tracking (public/mediapipe).
// The <matterport-viewer> web component loads them from there (asset-base="matterport-assets").
import { cp, access } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

try {
  // The package exports only its entry point (dist/...), so walk up from there.
  const pkg = path.resolve(path.dirname(require.resolve('@matterport/webcomponent')), '..');
  const src = path.join(pkg, 'built-bundle');
  await access(src);
  await cp(src, path.join(here, '..', 'public', 'matterport-assets'), { recursive: true });
  console.log('matterport assets copied to public/matterport-assets');
} catch (err) {
  // I-G21: a missing piece disables the Tour tab, it does not break install.
  console.warn(`matterport assets not copied (${err.message}); the Tour tab will be unavailable`);
}

try {
  // MediaPipe's wasm for hand tracking, served from public/mediapipe/wasm (gitignored).
  const pkg = path.dirname(require.resolve('@mediapipe/tasks-vision'));
  await cp(path.join(pkg, 'wasm'), path.join(here, '..', 'public', 'mediapipe', 'wasm'), { recursive: true });
  console.log('mediapipe wasm copied to public/mediapipe/wasm');
} catch (err) {
  // A missing piece disables hand control, it does not break install.
  console.warn(`mediapipe wasm not copied (${err.message}); hand control will be unavailable`);
}
