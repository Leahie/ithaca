/**
 * Runtime config from the environment. A missing key disables a feature; it never stops
 * startup (I-G21). The Showcase SDK key is the only secret the frontend holds (I-G22).
 */
export const config = {
  /** A6: render every tab from fixtures with no backend running. */
  useFixtures: process.env.NEXT_PUBLIC_USE_FIXTURES === '1',
  /** REST base. The default is same-origin, proxied to the API by next.config.ts. */
  apiBase: (process.env.NEXT_PUBLIC_API_BASE ?? '/api').replace(/\/$/, ''),
  /** WebSocket origin, e.g. ws://192.168.1.20:8000. Empty = same host as the page, port 8000. */
  wsBase: (process.env.NEXT_PUBLIC_WS_BASE ?? '').replace(/\/$/, ''),
  matterportSdkKey: process.env.NEXT_PUBLIC_MATTERPORT_SDK_KEY ?? '',
  /** I-G19: the property id comes from config, never from code. */
  defaultPropertyId: process.env.NEXT_PUBLIC_DEFAULT_PROPERTY_ID ?? '',
  /** I-G18: v1 has one user, sent as X-User-Id. */
  userId: process.env.NEXT_PUBLIC_USER_ID ?? 'demo',
  /** Origin the phone can reach, for the pairing QR code. Empty = the page origin. */
  publicOrigin: (process.env.NEXT_PUBLIC_PUBLIC_ORIGIN ?? '').replace(/\/$/, ''),
} as const;

export function wsOrigin(): string {
  if (config.wsBase) return config.wsBase;
  if (typeof window === 'undefined') return 'ws://localhost:8000';
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${window.location.hostname}:8000`;
}

export function publicOrigin(): string {
  if (config.publicOrigin) return config.publicOrigin;
  return typeof window === 'undefined' ? '' : window.location.origin;
}
