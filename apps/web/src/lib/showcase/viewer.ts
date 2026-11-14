import { config } from '@/lib/config';
import type { MpSdk } from './sdk-types';
import { installSplatShim } from './splatShim';

/**
 * Two ways to show a Matterport space, behind one handle.
 *
 * - "embed" (default): Matterport's hosted viewer in an iframe, driven by the SDK for Embeds.
 *   It is always the current viewer, so it loads everything a model has, including the
 *   exterior ("Outside") capture. It cannot add 3D objects to the scene.
 * - "bundle": the Bundle SDK that ships in @matterport/webcomponent. It can add 3D objects
 *   (`Scene.createObjects`, needed to place furniture in the tour), but the newest bundle on
 *   npm (26.5.4) cannot load exterior splat captures, so those are hidden from it
 *   (see splatShim.ts) and the outside view is not available.
 */
export type ViewerKind = 'embed' | 'bundle';

export interface ShowcaseHandle {
  kind: ViewerKind;
  /** The SDK once it is connected: model data is readable. */
  connected: Promise<MpSdk>;
  /** The SDK once the tour is interactive (app phase "playing"). */
  playing: Promise<MpSdk>;
  /** The element that shows the space. */
  element: HTMLElement;
  dispose(): void;
}

/** URL parameters for the viewer: start at once, and leave out chrome this app replaces. */
const PARAMS = { qs: '1', play: '1', brand: '0', title: '0', help: '0', search: '0' } as const;

function whenPlaying(sdk: MpSdk): Promise<MpSdk> {
  return new Promise((resolve) => {
    const sub = sdk.App.state.subscribe((state) => {
      if (state.phase === sdk.App.Phase.PLAYING) {
        resolve(sdk);
        // Cancel after this callback returns: the subscription object may not exist yet.
        queueMicrotask(() => sub.cancel());
      }
    });
  });
}

async function mountEmbed(host: HTMLElement, modelId: string): Promise<{ element: HTMLElement; connected: Promise<MpSdk> }> {
  const { setupSdk } = await import('@matterport/sdk');
  // setupSdk creates the iframe (with the model id and the key in its URL) inside `container`
  // and connects to it. Given an existing iframe it would only connect, and never load the space.
  const connected = setupSdk(config.matterportSdkKey, {
    container: host,
    space: modelId,
    iframeQueryParams: { ...PARAMS },
    iframeAttributes: { title: 'Matterport tour', style: 'display:block;width:100%;height:100%;border:0', allow: 'fullscreen; xr-spatial-tracking; web-share; autoplay; gyroscope; accelerometer' },
  }) as unknown as Promise<MpSdk>;
  // The iframe is appended before setupSdk first awaits the connection.
  await Promise.resolve();
  return { element: host.querySelector('iframe') ?? host, connected };
}

async function mountBundle(host: HTMLElement, modelId: string): Promise<{ element: HTMLElement; connected: Promise<MpSdk> }> {
  installSplatShim(); // before the bundle makes its first request
  await import('@matterport/webcomponent'); // registers <matterport-viewer>; touches `window`
  // Built by hand: the viewer reads its config from attributes, and React 19 would set them as
  // properties on a custom element (the model id then arrives empty).
  const node = document.createElement('matterport-viewer') as HTMLElement & { sdkPromise: Promise<MpSdk> };
  const attrs: Record<string, string> = { m: modelId, 'application-key': config.matterportSdkKey, 'asset-base': '/matterport-assets', ...PARAMS };
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  host.appendChild(node);
  return { element: node, connected: node.sdkPromise };
}

export function mountShowcase(host: HTMLElement, modelId: string, kind: ViewerKind = config.matterportViewer): ShowcaseHandle {
  let element: HTMLElement | null = null;
  let disposed = false;
  const mounted = (kind === 'embed' ? mountEmbed(host, modelId) : mountBundle(host, modelId)).then((m) => {
    element = m.element;
    if (disposed) m.element.remove();
    return m;
  });
  const connected = mounted.then((m) => m.connected);
  return {
    kind,
    connected,
    playing: connected.then(whenPlaying),
    get element() {
      return element ?? host;
    },
    dispose() {
      disposed = true;
      element?.remove();
    },
  };
}
