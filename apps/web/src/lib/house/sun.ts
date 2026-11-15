/**
 * The sun for the rebuilt house (ported from 3d_rendering_matterport/src/sun.ts).
 *
 * One shadow map, 4096 px, seen from the sun and covering the house and its lot. It is drawn
 * again only when the sun moves or something that casts a shadow changes; every surface then
 * looks itself up in it (house.ts, SUN_LIGHT). What stands outside the box toward the sun (a
 * neighbor's roof, an oak) is drawn into the map all the same, and so shades the house.
 *
 * Where the sun is comes from the page (suncalc, A3); here it is only turned into light: how
 * bright the day is, the sun's color, and which surfaces it reaches.
 */
import {
  Box3,
  Color,
  DepthTexture,
  DoubleSide,
  FloatType,
  MathUtils,
  Matrix4,
  NearestFilter,
  OrthographicCamera,
  ShaderMaterial,
  Vector3,
  Vector4,
  WebGLRenderTarget,
  type Camera,
  type IUniform,
  type Material,
  type WebGLRenderer,
} from 'three';

const SHADOW_PX = 4096;
/** What stands this far from the house toward the sun still casts its shadow on it. */
const CASTER_REACH_M = 80;
/** A surface is in shadow only if something is this much nearer the sun than itself. */
const SHADOW_BIAS_M = 0.02;
const MAX_OPENINGS = 96;
/** A hole is cut this far to each side of the wall line: walls are thick and outlines a little off. */
const OPENING_DEPTH_M = 0.5;
/** Haze at the ground, per meter of air: the next block over is about half hidden. */
const FOG_DENSITY = 0.005;

// The two lights by time of day (docs/specs/lighting.md, 2): the sun warm, the sky cool, and the
// gap between them widest near sunset.
const SUN_NOON = new Color(1.0, 0.96, 0.88);
const SUN_GOLDEN = new Color(1.0, 0.62, 0.32);
const SKY_NOON = new Color(0.78, 0.82, 0.9);
const SKY_GOLDEN = new Color(0.42, 0.55, 0.88);
const SKY_BLUE_HOUR = new Color(0.16, 0.24, 0.48);
const SKY_NIGHT = new Color(0.09, 0.15, 0.19);

/** Uniforms every lit material declares. */
export const SUN_PARS = /* glsl */ `
  uniform float sunOn;
  uniform sampler2D sunDepth;
  uniform mat4 sunMatrix;
  uniform vec3 sunDirection;
  uniform vec3 sunColor;   // zero when the sun is down
  uniform vec3 sunSky;
  uniform float sunTexel;
  uniform float sunBias;   // in the shadow map's depth units
  uniform float sunGolden;   // 0 with the sun high, 1 with it near the horizon
  uniform float sunBlueHour; // 1 just after sunset, 0 by day and in full night
  uniform float sunNight;    // 0 by day, 1 at night
  uniform float sunWork;     // working-hours lights: 1 when they are fully on (set from House3D, not by update())
  uniform vec3 hazeColor;   // the haze lit by the sky: cool
  uniform vec3 hazeSun;     // the haze toward the sun: warm, strongest when the sun is low
  uniform float hazeDensity; // per meter, at the ground
  uniform float hazeBase;   // height of the ground the haze lies on

  // Air between the eye and a point: how much of the point is hidden, and the haze in its place.
  // Denser near the ground, thinning upward (it halves about every 17 m); the first few meters
  // are clear, so a room is not milky but the far side of the block fades.
  vec3 houseFog(vec3 color, vec3 world) {
    vec3 ray = world - cameraPosition;
    float dist = length(ray);
    float clear = max(dist - 6.0, 0.0);
    if (hazeDensity <= 0.0 || clear <= 0.0) return color;
    const float FALL = 0.04;
    float h0 = max(cameraPosition.y - hazeBase, 0.0);
    float h1 = max(world.y - hazeBase, 0.0);
    float dh = h1 - h0;
    float layer = abs(dh) > 0.01 ? (exp(-FALL * h0) - exp(-FALL * h1)) / (FALL * dh) : exp(-FALL * h0);
    float amount = 1.0 - exp(-hazeDensity * clear * layer);
    vec3 view = ray / max(dist, 1e-3);
    float toward = max(dot(view, sunDirection), 0.0);
    vec3 haze = hazeColor + hazeSun * (pow(toward, 6.0) * 0.8 + pow(toward, 32.0) * 0.6);
    return mix(color, haze, amount);
  }`;

/** A window or glass door in a wall that has no hole for it (room outlines only): the sun goes through. */
export interface SunOpening {
  /** Middle of the opening, interior frame. */
  center: Vector3;
  /** Unit vector along the wall, horizontal. */
  along: Vector3;
  half: number;
  bottom: number;
  top: number;
}

export interface SunHost {
  /** Draw only what casts shadows, with `material`, from `camera` into the current render target. */
  renderOccluders(material: Material, camera: Camera): void;
  /** Box around everything that can cast or catch a shadow; null before anything loads. */
  bounds(): Box3 | null;
}

export class SunLight {
  readonly uniforms: Record<string, IUniform> = {
    sunOn: { value: 0 },
    sunDepth: { value: null },
    sunMatrix: { value: new Matrix4() },
    sunDirection: { value: new Vector3(0, 1, 0) },
    sunColor: { value: new Color(0, 0, 0) },
    sunSky: { value: new Color(1, 1, 1) },
    sunTexel: { value: 1 / SHADOW_PX },
    sunBias: { value: 0.0005 },
    sunGolden: { value: 0 },
    sunBlueHour: { value: 0 },
    sunNight: { value: 0 },
    sunWork: { value: 0 },
    hazeColor: { value: new Color(0.72, 0.77, 0.84) },
    hazeSun: { value: new Color(0, 0, 0) },
    hazeDensity: { value: FOG_DENSITY },
    hazeBase: { value: 0 },
  };
  /** How bright the sky is now (and anything the sun does not reach). */
  readonly sky = new Color(1, 1, 1);
  /** Whether a sun is being simulated (false while the house's place is unknown). */
  on = false;
  /** Cut holes in the shadow map where `setOpenings` says (walls without real holes). */
  cutOpenings = false;

  private target: WebGLRenderTarget | null = null;
  private camera = new OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
  private depthMaterial: ShaderMaterial;
  private openings = 0;

  constructor(
    private renderer: WebGLRenderer,
    private host: SunHost,
  ) {
    this.depthMaterial = new ShaderMaterial({
      uniforms: {
        thin: { value: 0 }, // 1 while a tree's crown is drawn (the house sets it): the sun gets through in flecks
        openingCount: { value: 0 },
        openingA: { value: Array.from({ length: MAX_OPENINGS }, () => new Vector4()) }, // center x, center z, along x, along z
        openingB: { value: Array.from({ length: MAX_OPENINGS }, () => new Vector4()) }, // half width, bottom, top, half depth
      },
      vertexShader: /* glsl */ `
        varying vec3 vWorld;
        void main() {
          vec4 world = modelMatrix * vec4(position, 1.0);
          vWorld = world.xyz;
          gl_Position = projectionMatrix * viewMatrix * world;
        }`,
      fragmentShader: /* glsl */ `
        uniform float thin;
        uniform int openingCount;
        uniform vec4 openingA[${MAX_OPENINGS}];
        uniform vec4 openingB[${MAX_OPENINGS}];
        varying vec3 vWorld;
        float speck(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
        // Soft lumps, about one to a unit of p.
        float lumps(vec3 p) {
          vec3 i = floor(p);
          vec3 f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          return mix(
            mix(mix(speck(i), speck(i + vec3(1.0, 0.0, 0.0)), f.x), mix(speck(i + vec3(0.0, 1.0, 0.0)), speck(i + vec3(1.0, 1.0, 0.0)), f.x), f.y),
            mix(mix(speck(i + vec3(0.0, 0.0, 1.0)), speck(i + vec3(1.0, 0.0, 1.0)), f.x), mix(speck(i + vec3(0.0, 1.0, 1.0)), speck(i + vec3(1.0, 1.0, 1.0)), f.x), f.y),
            f.z);
        }
        void main() {
          // A tree's crown is leaves and gaps: about two fifths of the sun gets through, in patches.
          if (thin > 0.5 && lumps(vWorld * 2.6) + 0.35 * lumps(vWorld * 7.0) > 0.72) discard;
          // Glass does not stop the sun: no depth is written inside a window or a glass door.
          for (int i = 0; i < ${MAX_OPENINGS}; i++) {
            if (i >= openingCount) break;
            vec2 d = vWorld.xz - openingA[i].xy;
            vec2 along = openingA[i].zw;
            if (vWorld.y > openingB[i].y && vWorld.y < openingB[i].z
                && abs(dot(d, along)) < openingB[i].x
                && abs(d.x * along.y - d.y * along.x) < openingB[i].w) discard;
          }
          gl_FragColor = vec4(1.0);
        }`,
      side: DoubleSide,
    });
  }

  setOpenings(openings: SunOpening[]): void {
    const u = this.depthMaterial.uniforms;
    const list = openings.slice(0, MAX_OPENINGS);
    this.openings = list.length;
    list.forEach((o, i) => {
      (u.openingA!.value as Vector4[])[i]!.set(o.center.x, o.center.z, o.along.x, o.along.z);
      (u.openingB!.value as Vector4[])[i]!.set(o.half, o.bottom, o.top, OPENING_DEPTH_M);
    });
  }

  /**
   * The sun now: the unit vector toward it (interior frame) and its height in degrees; null when
   * the house's place is unknown. Redraws the shadow map.
   */
  update(toSun: Vector3 | null, elevationDeg: number): void {
    const u = this.uniforms;
    if (!toSun) {
      u.sunOn!.value = 0;
      u.sunGolden!.value = u.sunBlueHour!.value = u.sunNight!.value = 0;
      this.sky.setRGB(1, 1, 1);
      (u.hazeColor!.value as Color).setRGB(0.72, 0.77, 0.84);
      (u.hazeSun!.value as Color).setRGB(0, 0, 0);
      u.hazeDensity!.value = FOG_DENSITY;
      this.on = false;
      return;
    }
    this.on = true;
    // The time of day, as three numbers every lit material reads (docs/specs/lighting.md, 1).
    const golden = 1 - MathUtils.smoothstep(elevationDeg, 4, 45);
    const blueHour = MathUtils.smoothstep(elevationDeg, -10, -2) * (1 - MathUtils.smoothstep(elevationDeg, -2, 3));
    const night = 1 - MathUtils.smoothstep(elevationDeg, -8, 2);
    u.sunGolden!.value = golden;
    u.sunBlueHour!.value = blueHour;
    u.sunNight!.value = night;

    // The sky, the light on everything the sun does not reach: barely cool at noon, blue as the
    // sun gets low, deep blue after sunset, dark at night. Its brightness stays what it was (a
    // grey of 0.8 by day); only its colour moves.
    const day = MathUtils.smoothstep(elevationDeg, -6, 12);
    const hue = SKY_NOON.clone().lerp(SKY_GOLDEN, golden);
    hue.multiplyScalar(2.4 / (hue.r + hue.g + hue.b)); // the same brightness as a grey of 0.8
    this.sky.copy(SKY_NIGHT).lerp(hue, day).lerp(SKY_BLUE_HOUR, blueHour * 0.85);
    (u.sunSky!.value as Color).copy(this.sky);
    // Direct sun: off below the horizon; near white high up, amber near the horizon.
    const strength = MathUtils.smoothstep(elevationDeg, 0, 10) * 0.95;
    (u.sunColor!.value as Color).copy(SUN_NOON).lerp(SUN_GOLDEN, golden).multiplyScalar(strength);
    const direction = toSun.clone().normalize();
    (u.sunDirection!.value as Vector3).copy(direction);
    u.sunOn!.value = 1;

    // The haze: blue-grey where it is lit by the sky, warm toward the sun. Low sun (morning,
    // evening) means more air to light through: thicker, bluer away from the sun, amber toward it.
    (u.hazeColor!.value as Color)
      .setRGB(0.74, 0.79, 0.86)
      .lerp(new Color(0.5, 0.58, 0.74), golden)
      .lerp(new Color(0.12, 0.17, 0.34), blueHour)
      .lerp(new Color(0.05, 0.085, 0.1), night * (1 - blueHour));
    (u.hazeSun!.value as Color).copy(u.sunColor!.value as Color).multiplyScalar(0.5 + 0.9 * golden);
    u.hazeDensity!.value = FOG_DENSITY * (1 + 0.8 * golden - 0.3 * night);

    const bounds = this.host.bounds();
    if (bounds) u.hazeBase!.value = bounds.min.y;
    if (strength > 0 && bounds) this.renderShadowMap(direction, bounds);
  }

  private renderShadowMap(direction: Vector3, bounds: Box3): void {
    if (!this.target) {
      this.target = new WebGLRenderTarget(SHADOW_PX, SHADOW_PX, {
        depthTexture: new DepthTexture(SHADOW_PX, SHADOW_PX, FloatType),
        minFilter: NearestFilter,
        magFilter: NearestFilter,
      });
    }
    const center = bounds.getCenter(new Vector3());
    const radius = bounds.getSize(new Vector3()).length() / 2;
    const camera = this.camera;
    camera.left = camera.bottom = -radius;
    camera.right = camera.top = radius;
    camera.near = 0.1;
    camera.far = radius * 2 + CASTER_REACH_M + 1;
    camera.position.copy(center).addScaledVector(direction, radius + CASTER_REACH_M);
    this.uniforms.sunBias!.value = SHADOW_BIAS_M / (camera.far - camera.near);
    camera.up.set(0, 1, 0);
    if (Math.abs(direction.y) > 0.99) camera.up.set(0, 0, 1);
    camera.lookAt(center);
    camera.updateMatrixWorld();
    camera.updateProjectionMatrix();

    const renderer = this.renderer;
    const before = renderer.getRenderTarget();
    this.depthMaterial.uniforms.openingCount!.value = this.cutOpenings ? this.openings : 0;
    renderer.setRenderTarget(this.target);
    renderer.clear();
    this.host.renderOccluders(this.depthMaterial, camera);
    renderer.setRenderTarget(before);

    this.uniforms.sunDepth!.value = this.target.depthTexture;
    (this.uniforms.sunMatrix!.value as Matrix4).multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  }

  dispose(): void {
    this.target?.depthTexture?.dispose();
    this.target?.dispose();
    this.target = null;
    this.depthMaterial.dispose();
    this.uniforms.sunDepth!.value = null;
    this.uniforms.sunOn!.value = 0;
  }
}

/**
 * Toward the sun in the interior frame, from its compass position and north as measured on the
 * plan: `north` is the unit vector pointing north in the model's floor plane (Matterport model
 * frame, x and y; the interior frame is (x, z, -y)).
 */
export function sunToward(azimuthDeg: number, elevationDeg: number, north: readonly [number, number]): [number, number, number] {
  const RAD = Math.PI / 180;
  const flat = Math.cos(elevationDeg * RAD);
  const east = Math.sin(azimuthDeg * RAD) * flat;
  const northward = Math.cos(azimuthDeg * RAD) * flat;
  const [nx, ny] = north;
  const modelX = east * ny + northward * nx;
  const modelY = -east * nx + northward * ny;
  const v = new Vector3(modelX, Math.sin(elevationDeg * RAD), -modelY).normalize();
  return [v.x, v.y, v.z];
}
