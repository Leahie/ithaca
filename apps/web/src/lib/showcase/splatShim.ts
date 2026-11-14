/**
 * Compatibility shim for the Matterport Bundle SDK that ships in @matterport/webcomponent.
 *
 * Problem: models with an exterior Gaussian-splat capture (the "Outside" view) never finish
 * loading in bundle 26.5.4. The bundle asks the Model API for the model's splats (GraphQL
 * operation `GetSplats`), starts its splat loader, and then waits for it forever. The hosted
 * viewer (26.11) loads the same model fine, so the bundle is just behind the data format.
 *
 * Fix: answer `GetSplats` with an empty list. The bundle then skips its splat module and the
 * tour starts normally: panoramas, dollhouse mesh, SDK and scene objects all work. Only the
 * splat-based outside view is missing.
 *
 * The web component runs the bundle in this window (no iframe), so wrapping `fetch` here is
 * enough. Nothing in the vendor bundle is modified. Set NEXT_PUBLIC_MATTERPORT_SPLATS=1 to turn
 * the shim off once a bundle that can load these splats is installed.
 */
const GRAPH = /matterport\.com\/api\/mp\/models\/graph/;

let installed = false;

export function installSplatShim(): void {
  if (installed || typeof window === 'undefined' || process.env.NEXT_PUBLIC_MATTERPORT_SPLATS === '1') return;
  installed = true;
  const original = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const isSplatQuery = GRAPH.test(url) && (url.includes('operationName=GetSplats') || (typeof init?.body === 'string' && init.body.includes('GetSplats')));
    const response = await original(input, init);
    if (!isSplatQuery || !response.ok) return response;
    try {
      const json = (await response.clone().json()) as { data?: { model?: { assets?: { splats?: unknown[] } } } };
      const assets = json.data?.model?.assets;
      if (!assets?.splats?.length) return response;
      assets.splats = [];
      console.info('[tour] hid the exterior splat capture from the Matterport bundle (it cannot load it; see splatShim.ts)');
      return new Response(JSON.stringify(json), { status: response.status, statusText: response.statusText, headers: { 'content-type': 'application/json' } });
    } catch {
      return response;
    }
  };
}
