/**
 * Runtime config from the environment. A missing key disables a feature; it never stops
 * startup (I-G21). The Showcase SDK key is the only secret the frontend holds (I-G22).
 */
/** Parts of the app that can be switched on. The default is the tour, the Sun Lab and hand control. */
export const FEATURES = ['tour', 'sun', 'sound', 'neighborhood', 'design', 'agent', 'phone', 'me', 'hands'] as const;
export type Feature = (typeof FEATURES)[number];

function parseFeatures(raw: string | undefined): ReadonlySet<Feature> {
  const wanted = (raw ?? 'tour,sun,hands').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (wanted.includes('all')) return new Set(FEATURES);
  const set = new Set(FEATURES.filter((f) => wanted.includes(f)));
  // At least one view must exist.
  if (!(['tour', 'sun', 'sound', 'neighborhood', 'design'] as const).some((v) => set.has(v))) set.add('tour');
  return set;
}

export const config = {
  /**
   * What the house page shows: any of tour, sun, sound, neighborhood, design (views), agent
   * (chat dock), phone (pairing), me (link to /me), hands (drag and zoom the view with your hands,
   * through the webcam); or "all". Default: "tour,sun,hands".
   */
  features: parseFeatures(process.env.NEXT_PUBLIC_FEATURES),
  /** "embed" (hosted viewer, has the outside view) or "bundle" (can place 3D objects). */
  matterportViewer: process.env.NEXT_PUBLIC_MATTERPORT_VIEWER === 'bundle' ? ('bundle' as const) : ('embed' as const),
  /** A6: render every tab from fixtures with no backend running. */
  useFixtures: process.env.NEXT_PUBLIC_USE_FIXTURES === '1',
  /** REST base. The default is same-origin, proxied to the API by next.config.ts. */
  apiBase: (process.env.NEXT_PUBLIC_API_BASE ?? '/api').replace(/\/$/, ''),
  /** WebSocket origin, e.g. ws://192.168.1.20:8000. Empty = same host as the page, port 8000. */
  wsBase: (process.env.NEXT_PUBLIC_WS_BASE ?? '').replace(/\/$/, ''),
  matterportSdkKey: process.env.NEXT_PUBLIC_MATTERPORT_SDK_KEY ?? '',
  /** MediaPipe hand landmarker model. Loaded when hand control is first turned on. */
  handModelUrl: process.env.NEXT_PUBLIC_HAND_MODEL_URL || 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',
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
