/**
 * The house rebuilt from the floor plan, as the Sun Lab draws it (ported from
 * 3d_rendering_matterport/src/house.ts; the model and its light come from that project's
 * pipeline, `pnpm house all`, and sit in the twin's `assets.house` folder).
 *
 * The pipeline does the building: straight walls with real thickness, door and window holes,
 * floors, ceilings, stairs, cabinets, the yard, all as flat closed surfaces; and the light that
 * falls on each of them, worked out once (two lightmaps: daylight from the sky, and the lamps).
 * Here the model is only loaded and drawn:
 *
 *     color = surface color x pattern x (daylight x how bright the sky is + lamplight + sun)
 *
 * The sun is the one light that moves (sun.ts): it is added on top, with its shadow map. A
 * room the sun shines into also glows with the light its floor and walls throw back: the
 * pipeline keeps, for each group of windows, what the room looks like lit through that group
 * (three more lightmaps), and here each is scaled by how much sun is coming in through it now.
 *
 * Around the house stands what Vexcel's aerial survey shows of the block (the neighbors'
 * houses, the trees, the garage), drawn with the survey's photo. It is there to cast its
 * shadows on the house: an oak across the street decides when the morning sun reaches a window.
 * Mirrors show the house as it is around them (a second picture, drawn from behind the glass).
 * Ceilings and roofs are drawn only from inside; from outside the house is seen without its
 * lid, with walls that face away not drawn (a "dollhouse").
 *
 * The house is in the interior frame: Matterport's model frame (x, y, z) is drawn at (x, z, -y).
 */
import {
  BackSide,
  Box3,
  BufferAttribute,
  BufferGeometry,
  Color,
  DataTexture,
  DoubleSide,
  FrontSide,
  Group,
  LinearFilter,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  Ray,
  SRGBColorSpace,
  ShaderMaterial,
  SphereGeometry,
  TextureLoader,
  Vector3,
  type Camera,
  type IUniform,
  type Material,
  type MeshStandardMaterial,
  type Object3D,
  type Texture,
  type WebGLRenderer,
} from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { Reflector } from 'three/examples/jsm/objects/Reflector.js';
import { MeshBVH } from 'three-mesh-bvh';
import { makePattern } from './patterns';
import { SUN_PARS } from './sun';

export interface HouseInfo {
  built: string;
  lightmap: { size: number; texel: number };
  storeys: { label: string; z: number; ceiling: number }[];
  /** Set once the light is baked: what full white in each picture stands for, and how bright the rooms' floors are. */
  light: { range: { sky: number; lamps: number; sun?: number }; floors: { sky: number; lamps: number }; groups?: number; outdoors?: number } | null;
  /** Which way the house faces, measured on Vexcel's aerial survey: north on the plan (model frame x, y). */
  orientation?: { source: string; turn: number; north: [number, number]; matterportTurn: number } | null;
  /** The windows and glass doors (model frame; half-size vectors), each with its group and the skylight that came in through it in the bake. */
  sun?: { portals: { center: number[]; across: number[]; up: number[]; normal: number[]; group: number; flux?: number }[] };
  /** The block around the house, from the aerial survey. */
  context?: { model: string; photo: string } | null;
}

// How bright the rooms should come out on screen: the usual floor gets this much light from
// each source (1 = full white for a white surface, before the roll-off).
const FLOOR_DAYLIGHT = 0.4;
const FLOOR_LAMPLIGHT = 0.62;
const LAMP_COLOR = new Color(1, 0.98, 0.95);
/** Layers hidden when looking at the house from outside. */
const LID = new Set(['ceiling', 'roof']);
/** A mirror farther away than this is drawn as plain silver (each one in view costs a second picture). */
const MIRROR_REACH_M = 9;
/** The sun against the open sky's light, for what a sunlit room throws back. */
const SUN_TO_SKY = 3.5;
/** Sunlight thrown back by a wood floor and pale walls is a little warm. */
const THROWN_BACK = new Color(1, 0.93, 0.82);
/** Out of doors is drawn this much darker than it is, as in the bake. */
const OUTDOOR_EXPOSURE = 1 / 12;
/** The photo from above is a picture of sunlit things: this much of it is taken as their color. */
const PHOTO_ALBEDO = 0.55;

const HOUSE_PARS = /* glsl */ `
  uniform sampler2D houseSky;
  uniform sampler2D houseLamps;
  uniform float houseBaked;   // 1 when the lightmaps are there
  uniform vec3 houseSkyTint;  // how bright (and what color) daylight is now
  uniform vec3 houseLampTint; // the lamps
  uniform vec3 houseOpen;     // daylight on open ground
  uniform sampler2D houseSunA; // per group of windows: the daylight that came in through it and was thrown back
  uniform sampler2D houseSunB;
  uniform sampler2D houseSunC;
  uniform vec3 houseSunWA;    // how much sun is coming in through each group now
  uniform vec3 houseSunWB;
  uniform vec3 houseSunWC;
  uniform vec3 houseSunTint;
  varying vec2 vHouseLight;
  varying vec3 vHouseWorld;
  varying vec3 vHouseNormal;`;

/** For things without a lightmap of their own (furniture, room outlines, plain ground). */
const PLAIN_PARS = /* glsl */ `
  uniform vec3 houseAmbient;
  varying vec3 vHouseWorld;
  varying vec3 vHouseNormal;`;

// Runs after the surface's own color is in diffuseColor. HOUSE_LIGHT is the light on it.
const HOUSE_BODY = /* glsl */ `
  vec3 houseLight;
  HOUSE_LIGHT
  diffuseColor.rgb *= houseLight;
  // Bright things roll off instead of clipping.
  diffuseColor.rgb = diffuseColor.rgb / (1.0 + max(diffuseColor.rgb - 0.7, 0.0) * 0.8);`;

/** The sun on a surface, where the shadow map says it reaches. */
const SUN_LIGHT = /* glsl */ `
  if (sunOn > 0.0) {
    vec3 sunNormal = normalize(vHouseNormal);
    if (!gl_FrontFacing) sunNormal = -sunNormal;
    float sunFacing = dot(sunNormal, sunDirection);
    float sunLit = 0.0;
    if (sunFacing > 0.0 && sunColor.r > 0.0) {
      vec4 inSun = sunMatrix * vec4(vHouseWorld + sunNormal * 0.03, 1.0);
      vec3 at = inSun.xyz / inSun.w * 0.5 + 0.5;
      if (all(greaterThan(at, vec3(0.0))) && all(lessThan(at, vec3(1.0)))) {
        for (int x = -1; x <= 1; x++) {
          for (int y = -1; y <= 1; y++) {
            sunLit += at.z - sunBias <= textureLod(sunDepth, at.xy + vec2(x, y) * sunTexel, 0.0).r ? 1.0 : 0.0;
          }
        }
        sunLit /= 9.0;
      } else {
        sunLit = 1.0; // beyond the shadow map (it covers the house and its lot): open ground
      }
    }
    houseLight += sunColor * (sunLit * sunFacing) * 2.4;
  }`;

const LIT = /* glsl */ `
  if (houseBaked > 0.5) {
    houseLight = texture2D(houseSky, vHouseLight).rgb * houseSkyTint + texture2D(houseLamps, vHouseLight).rgb * houseLampTint;
    // The sun that comes in through each group of windows, thrown back into the room.
    houseLight += houseSunTint * (dot(texture2D(houseSunA, vHouseLight).rgb, houseSunWA) + dot(texture2D(houseSunB, vHouseLight).rgb, houseSunWB) + dot(texture2D(houseSunC, vHouseLight).rgb, houseSunWC));
  } else {
    // Not baked yet: a little shading by the way a surface faces, so that corners show.
    houseLight = vec3(0.62 + 0.3 * dot(normalize(vHouseNormal), vec3(0.35, 0.8, 0.45)));
  }
  ${SUN_LIGHT}`;

/** What stands around the house has no lightmap: the open sky by the way a surface faces, and the sun. */
const OUTDOORS = /* glsl */ `
  houseLight = houseOpen * (0.62 + 0.38 * normalize(vHouseNormal).y);
  ${SUN_LIGHT}`;

/** The light on things that are not lit by the lightmap: lamps' faces, glass, water. */
const UNLIT: Record<string, string> = {
  light: 'houseLight = vec3(1.7);',
  glass: 'houseLight = sunOn > 0.0 ? 0.35 + 0.65 * sunSky : vec3(1.0);',
  water: 'houseLight = (sunOn > 0.0 ? sunSky : vec3(0.85)) * (0.8 + 0.2 * sin(vHouseWorld.x * 3.1 + vHouseWorld.z * 2.3));',
  aerial: OUTDOORS,
  canopy: OUTDOORS,
  context: OUTDOORS,
};

/** Without a lightmap: the room's usual light by the way a surface faces, and the sun. */
const PLAIN = /* glsl */ `
  houseLight = houseAmbient * (0.7 + 0.3 * normalize(vHouseNormal).y);
  ${SUN_LIGHT}`;

const SKY_FRAGMENT = /* glsl */ `
  uniform vec3 tint;      // the day's brightness (dark at night)
  uniform vec3 toSun;
  uniform float sunUp;    // 0 when no sun is being simulated
  varying vec3 vDirection;
  void main() {
    vec3 d = normalize(vDirection);
    float up = max(d.y, 0.0);
    // Blue overhead, pale at the horizon, a dull ground below it.
    vec3 sky = mix(vec3(0.62, 0.74, 0.88), vec3(0.16, 0.36, 0.72), pow(up, 0.5));
    vec3 ground = vec3(0.3, 0.33, 0.28);
    vec3 color = mix(ground, sky, smoothstep(-0.02, 0.02, d.y)) * tint;
    if (sunUp > 0.0) {
      float near = max(dot(d, toSun), 0.0);
      color += vec3(1.0, 0.9, 0.7) * (pow(near, 900.0) * 6.0 + pow(near, 12.0) * 0.25) * sunUp;
    }
    gl_FragColor = vec4(color, 1.0);
    #include <colorspace_fragment>
  }`;

/** What does not cast a shadow (or is not there to be drawn into the sun's shadow map). */
const NO_SHADOW = new Set(['glass', 'sky', 'mirror', 'light', 'water']);

/**
 * Light anything without a lightmap (furniture, room outlines, a plain ground) the way the
 * house is lit: `ambient` is the light around it (`plainLight`), the sun comes on top.
 * Marks the material's meshes as casting nothing by itself: the caller sets `userData.caster`.
 */
export function shadePlain(material: MeshBasicMaterial, ambient: IUniform<Color>, sunUniforms: Record<string, IUniform>): void {
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, sunUniforms, { houseAmbient: ambient });
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vHouseWorld;\nvarying vec3 vHouseNormal;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvHouseWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;\nvHouseNormal = mat3(modelMatrix) * normal;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${PLAIN_PARS}\n${SUN_PARS}`)
      .replace('#include <map_fragment>', `#include <map_fragment>\n${HOUSE_BODY.replace('HOUSE_LIGHT', PLAIN)}`);
  };
  material.customProgramCacheKey = () => 'house-plain';
}

/** The light around things without a lightmap: indoors (`inside`) a room's usual light, else open ground. */
export function plainLight(sky: Color, sunOn: boolean, inside: boolean, out: Color): Color {
  const day = sunOn ? sky : new Color(1, 1, 1);
  if (!inside) return out.copy(day).multiplyScalar(0.9);
  return out.copy(day).multiplyScalar(FLOOR_DAYLIGHT).add(LAMP_COLOR.clone().multiplyScalar(FLOOR_LAMPLIGHT));
}

export class House {
  /** The whole house, interior frame. */
  readonly group = new Group();
  /** Everything solid, for rays (how much sun gets in through each window). */
  bvh: MeshBVH | null = null;
  info: HouseInfo;
  triangles = 0;

  readonly uniforms: Record<string, IUniform> = {
    houseSky: { value: null },
    houseLamps: { value: null },
    houseBaked: { value: 0 },
    houseSkyTint: { value: new Color(1, 1, 1) },
    houseLampTint: { value: new Color(1, 1, 1) },
    houseOpen: { value: new Color(1, 1, 1) },
    houseSunA: { value: null },
    houseSunB: { value: null },
    houseSunC: { value: null },
    houseSunWA: { value: new Vector3() },
    houseSunWB: { value: new Vector3() },
    houseSunWC: { value: new Vector3() },
    houseSunTint: { value: new Color(0, 0, 0) },
  };

  /** From the pictures' values to light on screen. */
  private gain = { sky: 1, lamps: 1, sun: 0, open: 1 };
  private lid: Object3D[] = [];
  /** The lawn beyond the lot: left out once the surroundings are there to be seen instead. */
  private far: Object3D[] = [];
  /** The box around the house and its lot (without that far lawn): what the sun's shadow map must cover. */
  private lot = new Box3();
  private mirrors: { glass: Reflector; plain: Mesh; center: Vector3 }[] = [];
  private sky: Mesh<SphereGeometry, ShaderMaterial>;
  private solid: Mesh | null = null;
  private materials: Material[] = [];
  private textures: Texture[] = [];
  private disposed = false;

  private constructor(info: HouseInfo) {
    this.info = info;
    this.sky = new Mesh(
      new SphereGeometry(1200, 32, 16),
      new ShaderMaterial({
        uniforms: { tint: { value: new Color(1, 1, 1) }, toSun: { value: new Vector3(0, 1, 0) }, sunUp: { value: 0 } },
        vertexShader: /* glsl */ `
          varying vec3 vDirection;
          void main() {
            vDirection = position;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          }`,
        fragmentShader: SKY_FRAGMENT,
        side: BackSide,
        depthTest: false,
        depthWrite: false,
      }),
    );
    this.sky.renderOrder = -2;
    this.sky.frustumCulled = false;
    this.sky.userData.kind = 'sky';
    this.sky.raycast = () => {}; // clicks go to the house, not the sky behind it
    this.group.add(this.sky);
  }

  /** The house described by the house.json at `url`; its model and pictures are beside it. */
  static async load(renderer: WebGLRenderer, url: string, info: HouseInfo, sunUniforms: Record<string, IUniform>, changed: () => void): Promise<House> {
    const base = new URL(url, window.location.href);
    const stamp = encodeURIComponent(info.built);
    const at = (name: string) => new URL(`${name}?v=${stamp}`, base).toString();
    const response = await fetch(at('house.glb'));
    if (!response.ok) throw new Error(`house.glb: HTTP ${response.status}`);
    const gltf = await new GLTFLoader().parseAsync(await response.arrayBuffer(), '');
    const house = new House(info);
    const anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());

    const made = new Map<string, MeshBasicMaterial>();
    const patterns = new Map<string, Texture | null>();
    const solid: Float32Array[] = [];
    gltf.scene.updateMatrixWorld(true);
    gltf.scene.traverse((o) => {
      const mesh = o as Mesh<BufferGeometry, MeshStandardMaterial>;
      if (!mesh.isMesh) return;
      const extras = mesh.userData as { material: string; layer: string; storey: number; both: boolean };
      const from = mesh.material;
      const given = from.userData as { kind?: string; texture?: string; repeat?: number };
      const kind = given.kind ?? '';
      // What a ray cannot pass: everything but lamps' faces and water.
      if (kind !== 'light' && kind !== 'water') {
        const flat = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry;
        solid.push(flat.getAttribute('position').array as Float32Array);
      }
      if (kind === 'hidden') return;
      house.triangles += (mesh.geometry.index?.count ?? mesh.geometry.getAttribute('position').count) / 3;
      if (kind === 'mirror') {
        house.addMirror(mesh.geometry, from.color);
        return;
      }
      const key = `${extras.material}|${extras.both}`;
      let material = made.get(key);
      if (!material) {
        material = new MeshBasicMaterial({ color: from.color, side: extras.both ? DoubleSide : FrontSide });
        material.name = extras.material;
        if (kind === 'glass') {
          material.transparent = true;
          material.opacity = from.opacity;
          material.depthWrite = false;
        }
        if (given.texture) {
          if (!patterns.has(given.texture)) patterns.set(given.texture, makePattern(given.texture));
          const pattern = patterns.get(given.texture);
          if (pattern) {
            // One copy of the pattern covers `repeat` meters; the model's texture coordinates are meters.
            material.map = pattern.clone();
            material.map.repeat.setScalar(1 / (given.repeat ?? 1));
            material.map.anisotropy = anisotropy;
            material.map.needsUpdate = true;
            house.textures.push(material.map);
          }
        }
        house.shade(material, sunUniforms, kind);
        made.set(key, material);
        house.materials.push(material);
      }
      const part = new Mesh(mesh.geometry, material);
      // Each part is one material all over the house: there is nothing to gain from leaving it
      // out of a view, and a mirror's slanted view would leave it out wrongly.
      part.frustumCulled = false;
      part.name = mesh.name;
      part.userData = { ...extras, kind, caster: !NO_SHADOW.has(kind) };
      if (kind === 'glass') part.renderOrder = 2;
      house.group.add(part);
      if (LID.has(extras.layer)) house.lid.push(part);
      if (extras.layer === 'far') house.far.push(part);
      else if (kind !== 'light' && kind !== 'water') house.lot.union(mesh.geometry.boundingBox ?? new Box3().setFromBufferAttribute(mesh.geometry.getAttribute('position') as BufferAttribute));
      from.dispose();
    });
    for (const pattern of patterns.values()) pattern?.dispose(); // the materials hold their own copies
    const all = new Float32Array(solid.reduce((n, a) => n + a.length, 0));
    let offset = 0;
    for (const a of solid) {
      all.set(a, offset);
      offset += a.length;
    }
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new BufferAttribute(all, 3));
    geometry.computeBoundingBox();
    house.bvh = new MeshBVH(geometry);
    house.solid = new Mesh(geometry, new MeshBasicMaterial());

    // The light, if it has been baked.
    const loader = new TextureLoader();
    const light = (name: string) =>
      new Promise<Texture | null>((resolve) => {
        loader.load(
          at(name),
          (texture) => {
            texture.colorSpace = SRGBColorSpace; // stored with the display curve: more steps in the dark
            texture.flipY = false;
            texture.generateMipmaps = false;
            texture.minFilter = LinearFilter;
            resolve(texture);
          },
          undefined,
          () => resolve(null),
        );
      });
    // Until a group's picture is there (or if the bake made none), nothing is thrown back.
    const none = new DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);
    none.needsUpdate = true;
    house.textures.push(none);
    for (const name of ['houseSunA', 'houseSunB', 'houseSunC']) house.uniforms[name]!.value = none;
    if (info.light) {
      const { range, floors } = info.light;
      const exposure = Math.min(60, FLOOR_DAYLIGHT / Math.max(floors.sky, 1e-3));
      house.gain.sky = range.sky * exposure;
      house.gain.lamps = range.lamps * Math.min(60, FLOOR_LAMPLIGHT / Math.max(floors.lamps, 1e-3));
      house.gain.sun = (range.sun ?? 0) * exposure * SUN_TO_SKY;
      house.gain.open = (info.light.outdoors ?? OUTDOOR_EXPOSURE) * exposure;
      void Promise.all([light('light_sky.png'), light('light_lamps.png')]).then(([sky, lamps]) => {
        if (house.disposed) return void (sky?.dispose(), lamps?.dispose());
        if (!sky || !lamps) return;
        house.textures.push(sky, lamps);
        house.uniforms.houseSky!.value = sky;
        house.uniforms.houseLamps!.value = lamps;
        house.uniforms.houseBaked!.value = 1;
        changed();
      });
      ['houseSunA', 'houseSunB', 'houseSunC'].slice(0, Math.ceil((info.light.groups ?? 0) / 3)).forEach((name, k) => {
        void light(`light_sun_${k}.png`).then((picture) => {
          if (house.disposed) return void picture?.dispose();
          if (!picture) return;
          house.textures.push(picture);
          house.uniforms[name]!.value = picture;
          changed();
        });
      });
    } else {
      house.gain.open = 0.9;
    }
    if (info.context) void house.loadContext(info.context, at, renderer, sunUniforms, changed);
    house.setLight(new Color(1, 1, 1), null, new Color(0, 0, 0));
    return house;
  }

  /** What Vexcel's aerial survey shows around the house: the land with its photo, the neighbors' houses, the trees. */
  private async loadContext(context: NonNullable<HouseInfo['context']>, at: (name: string) => string, renderer: WebGLRenderer, sunUniforms: Record<string, IUniform>, changed: () => void): Promise<void> {
    const [response, photo] = await Promise.all([fetch(at(context.model)), new TextureLoader().loadAsync(at(context.photo)).catch(() => null)]);
    if (!response.ok || !photo || this.disposed) return void photo?.dispose();
    const gltf = await new GLTFLoader().parseAsync(await response.arrayBuffer(), '');
    if (this.disposed) return void photo.dispose();
    photo.colorSpace = SRGBColorSpace;
    photo.flipY = false;
    photo.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    photo.needsUpdate = true;
    this.textures.push(photo);
    gltf.scene.updateMatrixWorld(true);
    gltf.scene.traverse((o) => {
      const mesh = o as Mesh<BufferGeometry, MeshStandardMaterial>;
      if (!mesh.isMesh) return;
      const kind = (mesh.material.userData as { kind?: string }).kind ?? 'context';
      const pictured = kind !== 'context'; // walls are plain
      const material = new MeshBasicMaterial({
        color: pictured ? new Color().setScalar(PHOTO_ALBEDO) : mesh.material.color,
        map: pictured ? photo : null,
        side: kind === 'canopy' ? DoubleSide : FrontSide,
      });
      this.shade(material, sunUniforms, kind);
      this.materials.push(material);
      const part = new Mesh(mesh.geometry, material);
      part.frustumCulled = false;
      part.userData = { layer: 'context', kind, caster: true };
      if (kind === 'canopy') {
        // A crown lets the sun through in flecks: the shadow map's material thins it (sun.ts).
        const thin = (on: number) => (_r: unknown, _s: unknown, _c: unknown, _g: unknown, drawn: Material) => {
          const uniform = (drawn as ShaderMaterial).uniforms?.thin;
          if (!uniform) return;
          uniform.value = on;
          (drawn as ShaderMaterial).uniformsNeedUpdate = true;
        };
        part.onBeforeRender = thin(1);
        part.onAfterRender = thin(0);
      }
      this.group.add(part);
      mesh.material.dispose();
    });
    // The photo shows what is beyond the lot: the plain lawn out there is not needed.
    for (const part of this.far) part.visible = false;
    changed();
  }

  /** The box around the house and its lot: where the sun's shadows must be sharp. */
  get bounds(): Box3 | null {
    return this.lot.isEmpty() ? (this.solid?.geometry.boundingBox ?? null) : this.lot;
  }

  /** Whether the ceilings and the roof are drawn. */
  lidOn = true;

  /** From outside, the ceilings and the roof are left off. */
  setLid(on: boolean): void {
    this.lidOn = on;
    for (const part of this.lid) part.visible = on;
  }

  /**
   * Daylight now: the sun's sky color, the way to the sun (null when no sun is simulated) and
   * the sun's color (black when it is down).
   */
  setLight(sky: Color, toSun: Vector3 | null, sun: Color): void {
    // With the sun simulated its sky color dims the daylight; the lamps stay as they are.
    const day = toSun ? sky : new Color(1, 1, 1);
    (this.uniforms.houseSkyTint!.value as Color).copy(day).multiplyScalar(this.gain.sky);
    (this.uniforms.houseOpen!.value as Color).copy(day).multiplyScalar(this.gain.open);
    (this.uniforms.houseLampTint!.value as Color).copy(LAMP_COLOR).multiplyScalar(this.gain.lamps);
    (this.uniforms.houseSunTint!.value as Color).copy(sun).multiply(THROWN_BACK).multiplyScalar(this.gain.sun);
    this.sunThrough(toSun);
    const u = this.sky.material.uniforms;
    (u.tint!.value as Color).copy(day);
    u.sunUp!.value = toSun && toSun.y > -0.05 ? 1 : 0;
    if (toSun) (u.toSun!.value as Vector3).copy(toSun);
  }

  /**
   * How much sun is coming in through each group of windows, against the skylight that came in
   * through it in the bake: a window counts by its size, by how squarely the sun stands to it,
   * and by how much of it the sun reaches past the eaves, the patio's roof and the fences.
   */
  private sunThrough(toSun: Vector3 | null): void {
    const weights = new Array<number>(9).fill(0);
    const portals = this.info.sun?.portals ?? [];
    if (toSun && toSun.y > 0 && this.bvh && this.gain.sun > 0) {
      const sun = new Array<number>(9).fill(0);
      const sky = new Array<number>(9).fill(0);
      const ray = new Ray(new Vector3(), toSun.clone().normalize());
      for (const p of portals) {
        const flux = p.flux ?? 0;
        if (p.group > 8 || !(flux > 0)) continue;
        sky[p.group]! += flux;
        const area = 4 * Math.hypot(...p.across) * Math.hypot(...p.up);
        if (flux < 0.03 * area) continue; // shut: a door's leaf stands in it
        const [n0, n1, n2] = p.normal as [number, number, number];
        // The model's frame (x, y, z) is the interior frame's (x, z, -y).
        const facing = n0 * ray.direction.x + n2 * ray.direction.y - n1 * ray.direction.z;
        if (facing <= 0) continue;
        let open = 0;
        for (const a of [-0.6, 0, 0.6]) {
          for (const b of [-0.6, 0, 0.6]) {
            const point = [0, 1, 2].map((k) => p.center[k]! + p.across[k]! * a + p.up[k]! * b + p.normal[k]! * 0.15) as [number, number, number];
            ray.origin.set(point[0], point[2], -point[1]);
            if (!this.bvh.raycastFirst(ray, DoubleSide, 0, 300)) open++;
          }
        }
        sun[p.group]! += (area * facing * open) / 9;
      }
      for (let g = 0; g < 9; g++) weights[g] = sky[g]! > 0.05 ? sun[g]! / sky[g]! : 0;
    }
    (this.uniforms.houseSunWA!.value as Vector3).fromArray(weights, 0);
    (this.uniforms.houseSunWB!.value as Vector3).fromArray(weights, 3);
    (this.uniforms.houseSunWC!.value as Vector3).fromArray(weights, 6);
  }

  /** Call before each frame: the sky stays around the camera; only mirrors close by reflect. */
  update(camera: Camera): void {
    this.sky.position.copy(camera.position);
    for (const mirror of this.mirrors) {
      const near = mirror.center.distanceTo(camera.position) < MIRROR_REACH_M;
      mirror.glass.visible = near;
      mirror.plain.visible = !near;
    }
  }

  /** Mirrors: each flat piece of this part of the model (all the mirrors of a storey come as one), drawn as what is seen in it. */
  private addMirror(geometry: BufferGeometry, color: Color): void {
    // Triangles that share a corner are one mirror.
    const index = geometry.index!.array;
    const count = geometry.getAttribute('position').count;
    const parent = new Int32Array(count).map((_, i) => i);
    const find = (a: number) => {
      while (parent[a] !== a) a = parent[a] = parent[parent[a]!]!;
      return a;
    };
    for (let t = 0; t < index.length; t += 3) {
      parent[find(index[t + 1]!)] = find(index[t]!);
      parent[find(index[t + 2]!)] = find(index[t]!);
    }
    const pieces = new Map<number, number[]>();
    for (let t = 0; t < index.length; t += 3) {
      const root = find(index[t]!);
      const list = pieces.get(root) ?? [];
      list.push(index[t]!, index[t + 1]!, index[t + 2]!);
      pieces.set(root, list);
    }
    for (const corners of pieces.values()) this.addOneMirror(geometry, corners, color);
  }

  private addOneMirror(geometry: BufferGeometry, corners: number[], color: Color): void {
    // The piece's own frame: its middle, and two directions in its plane.
    const from = geometry.getAttribute('position') as BufferAttribute;
    const position = new BufferAttribute(new Float32Array(corners.length * 3), 3);
    corners.forEach((corner, i) => position.setXYZ(i, from.getX(corner), from.getY(corner), from.getZ(corner)));
    const normal = new Vector3().fromBufferAttribute(geometry.getAttribute('normal') as BufferAttribute, corners[0]!).normalize();
    const center = new Box3().setFromBufferAttribute(position).getCenter(new Vector3());
    const right = new Vector3().crossVectors(new Vector3(0, 1, 0), normal).normalize();
    const top = new Vector3().crossVectors(normal, right);
    const local = new Float32Array(position.count * 3);
    const p = new Vector3();
    for (let i = 0; i < position.count; i++) {
      p.fromBufferAttribute(position, i).sub(center);
      local.set([p.dot(right), p.dot(top), 0], i * 3);
    }
    const flat = new BufferGeometry();
    flat.setAttribute('position', new BufferAttribute(local, 3));
    flat.setAttribute('uv', new BufferAttribute(new Float32Array(position.count * 2), 2));
    const place = (object: Object3D) => {
      object.position.copy(center);
      object.quaternion.setFromRotationMatrix(new Matrix4().makeBasis(right, top, normal));
      object.updateMatrixWorld(true);
    };
    const glass = new Reflector(flat, { textureWidth: 768, textureHeight: 768, color: new Color(0.86, 0.88, 0.88), multisample: 2 });
    glass.userData.kind = 'mirror';
    place(glass);
    const plain = new Mesh(flat, new MeshBasicMaterial({ color: color.clone().multiplyScalar(0.8) }));
    plain.userData.kind = 'mirror';
    place(plain);
    plain.visible = false;
    this.materials.push(plain.material as Material, glass.material as Material);
    this.group.add(glass, plain);
    this.mirrors.push({ glass, plain, center });
  }

  private shade(material: MeshBasicMaterial, sunUniforms: Record<string, IUniform>, kind: string): void {
    const body = HOUSE_BODY.replace('HOUSE_LIGHT', UNLIT[kind] ?? LIT);
    material.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, this.uniforms, sunUniforms);
      shader.vertexShader = shader.vertexShader
        .replace('#include <common>', '#include <common>\nattribute vec2 uv1;\nvarying vec3 vHouseWorld;\nvarying vec3 vHouseNormal;\nvarying vec2 vHouseLight;')
        .replace('#include <project_vertex>', '#include <project_vertex>\nvHouseWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;\nvHouseNormal = mat3(modelMatrix) * normal;\nvHouseLight = uv1;');
      shader.fragmentShader = shader.fragmentShader.replace('#include <common>', `#include <common>\n${HOUSE_PARS}\n${SUN_PARS}`).replace('#include <map_fragment>', `#include <map_fragment>\n${body}`);
    };
    material.customProgramCacheKey = () => `house-${kind in UNLIT ? kind : 'lit'}`;
  }

  dispose(): void {
    this.disposed = true;
    for (const part of this.group.children as Mesh[]) if (part !== this.sky) part.geometry.dispose();
    for (const mirror of this.mirrors) mirror.glass.dispose();
    for (const material of this.materials) material.dispose();
    for (const texture of this.textures) texture.dispose();
    this.sky.geometry.dispose();
    this.sky.material.dispose();
    this.solid?.geometry.dispose();
    (this.solid?.material as Material | undefined)?.dispose();
    this.bvh = null;
  }
}

/** The house.json at `url`, fetched once per page. */
const infos = new Map<string, Promise<HouseInfo>>();
export function loadHouseInfo(url: string): Promise<HouseInfo> {
  let p = infos.get(url);
  if (!p) {
    p = fetch(url).then(async (r) => {
      if (!r.ok) throw new Error(`house.json: HTTP ${r.status}`);
      return (await r.json()) as HouseInfo;
    });
    p.catch(() => infos.delete(url)); // a failure is tried again next time
    infos.set(url, p);
  }
  return p;
}
