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

/** Uniforms every lit material declares. */
export const SUN_PARS = /* glsl */ `
  uniform float sunOn;
  uniform sampler2D sunDepth;
  uniform mat4 sunMatrix;
  uniform vec3 sunDirection;
  uniform vec3 sunColor;   // zero when the sun is down
  uniform vec3 sunSky;
  uniform float sunTexel;
  uniform float sunBias;   // in the shadow map's depth units`;

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
      this.sky.setRGB(1, 1, 1);
      this.on = false;
      return;
    }
    this.on = true;
    // Brightness of the day: full by day, dim at dusk, dark at night.
    const day = MathUtils.smoothstep(elevationDeg, -6, 12);
    this.sky.setRGB(0.05, 0.065, 0.11).lerp(new Color(0.8, 0.8, 0.8), day);
    (u.sunSky!.value as Color).copy(this.sky);
    // Direct sun: off below the horizon, warm and weak when low, near white when high.
    const strength = MathUtils.smoothstep(elevationDeg, 0, 10) * 0.95;
    (u.sunColor!.value as Color)
      .setRGB(1.0, 0.55, 0.3)
      .lerp(new Color(1.0, 0.94, 0.82), MathUtils.smoothstep(elevationDeg, 2, 30))
      .multiplyScalar(strength);
    const direction = toSun.clone().normalize();
    (u.sunDirection!.value as Vector3).copy(direction);
    u.sunOn!.value = 1;

    const bounds = this.host.bounds();
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
