import { readdir, readFile, stat } from 'node:fs/promises';
import path from 'node:path';

/**
 * Serves the repo's `fixtures/` tree at /fixtures/** for fixture mode (A6).
 * A path that ends in `/_index` returns the list of files under that directory.
 * The API resolves asset URLs in full-stack mode; this route is for offline work only.
 */
// The fixtures folder is outside the app and is read at run time; it is not part of the build.
const ROOT = path.resolve(process.env.FIXTURES_DIR ?? path.join(/* turbopackIgnore: true */ process.cwd(), '..', '..', 'fixtures'));
const ENABLED = process.env.NEXT_PUBLIC_USE_FIXTURES === '1' || process.env.NODE_ENV !== 'production';

const TYPES: Record<string, string> = {
  '.json': 'application/json',
  '.glb': 'model/gltf-binary',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.md': 'text/markdown; charset=utf-8',
};

async function listFiles(dir: string, prefix = ''): Promise<string[]> {
  const out: string[] = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) out.push(...(await listFiles(path.join(dir, entry.name), rel)));
    else out.push(rel);
  }
  return out.sort();
}

export async function GET(_req: Request, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  if (!ENABLED) return new Response('fixtures are disabled', { status: 404 });
  const parts = (await ctx.params).path;
  const wantsIndex = parts.at(-1) === '_index';
  const file = path.resolve(ROOT, ...(wantsIndex ? parts.slice(0, -1) : parts));
  if (file !== ROOT && !file.startsWith(ROOT + path.sep)) return new Response('bad path', { status: 400 });

  try {
    if (wantsIndex) return Response.json(await listFiles(file));
    if (!(await stat(file)).isFile()) return new Response('not found', { status: 404 });
    const body = await readFile(file);
    if (body.subarray(0, 40).toString('utf8').startsWith('version https://git-lfs')) {
      return new Response('This fixture is a Git LFS pointer. Run: git lfs pull', { status: 409 });
    }
    return new Response(new Uint8Array(body), {
      headers: { 'content-type': TYPES[path.extname(file).toLowerCase()] ?? 'application/octet-stream', 'cache-control': 'no-cache' },
    });
  } catch {
    return new Response('not found', { status: 404 });
  }
}
