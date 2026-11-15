/**
 * The house rebuilt from the floor plan, as the Tour draws it (ported from
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
 * lid, with walls that face away not drawn (a "dollhouse"). Looking down into a room, the walls
 * that stand between the camera and it are faded, so what is in the room shows (setCutaway).
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
  LinearMipmapLinearFilter,
  MathUtils,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  Ray,
  SRGBColorSpace,
  ShaderMaterial,
  SphereGeometry,
  TextureLoader,
  Triangle,
  Vector2,
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
const FLOOR_DAYLIGHT = 0.32;
const FLOOR_LAMPLIGHT = 0.22;
/** The lamps at night against by day: dimmer, and (LAMP_FALL_*) their light reaches less far. */
const LAMPS_AT_NIGHT = 0.55;
const LAMP_FALL_DAY = 1.8;
const LAMP_FALL_NIGHT = 3.2;
/** The most the sun thrown back into a room adds to its light (1 = full white); it levels off toward this. */
const BOUNCE_MAX = 0.3;
/** Daylight on open ground and the sun thrown back indoors keep the level they had: only the rooms' own light is lower (docs/specs/lighting.md, 8a). */
const OPEN_DAYLIGHT = 0.4;
const LAMP_COLOR = new Color(1, 0.98, 0.95);
/** Lamps at night, about 2700 K: warm windows against the blue outside. */
const LAMP_NIGHT = new Color(1, 0.78, 0.52);
/** Working-hours lights (sunWork): about 3800 K, and this many times the lamps' day strength, with their falloff flattened. */
const WORK_LAMP = new Color(1, 0.9, 0.78);
const WORK_LAMP_GAIN = 2;
/** Layers hidden when looking at the house from outside. */
const LID = new Set(['ceiling', 'roof']);
/** Layers faded from above where they stand in the way of the room looked into (setCutaway). */
const SCREEN = new Set(['wall', 'outside', 'joinery']);
/** How much of a wall in the way still shows (1 = all of it). */
const SCREEN_OPACITY = 0.1;
/**
 * Which surfaces are in the way: those at least `along` metres nearer the camera than the spot
 * looked at (faded in over that range), facing the camera by `facing` (the cosine, across the
 * floor), and not more than `below` under the spot's floor. `fadeS`: seconds to fade in or out.
 */
const CUTAWAY = { along: [0.3, 1.2], facing: [0.2, 0.55], below: 0.3, fadeS: 0.6 } as const;
/** A mirror farther away than this is drawn as plain silver (each one in view costs a second picture). */
const MIRROR_REACH_M = 9;
/** The sun against the open sky's light, for what a sunlit room throws back. */
const SUN_TO_SKY = 3.5;
/** Sunlight thrown around a room reads neutral to a little cool next to the sun that came in. */
const THROWN_BACK = new Color(0.93, 0.96, 1.02);
/** Out of doors is drawn this much darker than it is, as in the bake. */
const OUTDOOR_EXPOSURE = 1 / 12;
/** The photo from above is a picture of sunlit things: this much of it is taken as their color. */
const PHOTO_ALBEDO = 0.55;
/**
 * The most the sun adds on top of a room's own (compressed) light. A sun patch comes out about
 * twice as bright as the shade next to it, as in a photo of a sunlit room: a pale rug in it goes
 * near white and warm, and a sofa's sunlit top stands out from its shaded sides and the shadow it throws.
 */
const SUN_HEADROOM = 1.1;

const HOUSE_PARS = /* glsl */ `
  uniform sampler2D houseSky;
  uniform sampler2D houseLamps;
  uniform float houseBaked;   // 1 when the lightmaps are there
  uniform vec3 houseSkyTint;  // how bright (and what color) daylight is now
  uniform vec3 houseLampTint; // the lamps
  uniform float houseLampsLit; // 1 when the lamps are switched on (8 am to 5 pm), 0 when off
  uniform float houseLampGain; // the lamps' strength before the time of day: for the working-hours lights
  uniform float houseLampPeak; // a lamp's lightmap value near the lamp: brighter pools, darker gaps between
  uniform float houseLampFall; // how steeply a lamp's light drops off: steeper at night, so its pool is smaller
  uniform vec3 houseOpen;     // daylight on open ground
  uniform sampler2D houseSunA; // per group of windows: the daylight that came in through it and was thrown back
  uniform sampler2D houseSunB;
  uniform sampler2D houseSunC;
  uniform vec3 houseSunWA;    // how much sun is coming in through each group now
  uniform vec3 houseSunWB;
  uniform vec3 houseSunWC;
  uniform vec3 houseSunTint;
  uniform float houseLightTexel; // one texel of the lightmaps, in their texture coordinates
  uniform vec2 houseLightFloor;  // a usual floor's value in the sky and the lamp lightmaps
  varying vec2 vHouseLight;

  // The bake is noisy: on a flat wall its light comes out in soft blotches a few texels across.
  // Average the texels around, but only those within about 30% of this one: the noise goes,
  // while a real edge in the light (a door's contact shadow, a corner) stays sharp, and a texel
  // of another part of the map next door is left out.
  vec3 houseLightmap(sampler2D map, vec2 uv, float usual) {
    vec3 c = texture2D(map, uv).rgb;
    float lc = dot(c, vec3(0.299, 0.587, 0.114));
    // Where the light is dim the noise is large against it: there the tolerance does not shrink
    // below half of a usual floor's light.
    float tol = 0.3 * lc + usual + 1e-5;
    vec3 sum = c;
    float total = 1.0;
    vec2 t = vec2(houseLightTexel);
    vec2 offs[12];
    offs[0] = vec2(2.0, 0.0); offs[1] = vec2(-2.0, 0.0); offs[2] = vec2(0.0, 2.0); offs[3] = vec2(0.0, -2.0);
    offs[4] = vec2(3.5, 3.5); offs[5] = vec2(-3.5, 3.5); offs[6] = vec2(3.5, -3.5); offs[7] = vec2(-3.5, -3.5);
    offs[8] = vec2(7.0, 0.0); offs[9] = vec2(-7.0, 0.0); offs[10] = vec2(0.0, 7.0); offs[11] = vec2(0.0, -7.0);
    for (int i = 0; i < 12; i++) {
      vec3 s = texture2D(map, uv + offs[i] * t).rgb;
      float d = (dot(s, vec3(0.299, 0.587, 0.114)) - lc) / tol;
      float w = exp(-d * d);
      sum += s * w;
      total += w;
    }
    vec3 near = sum / total;
    // Then the room's light over about 30-65 cm (the map's mips 3 and 4): a flat wall lit by
    // diffuse light comes out even, the way it is. Where the light really changes (a corner, a
    // door's contact shadow) this level differs a lot from the near one, and the near one is kept.
    vec3 wide = mix(textureLod(map, uv, 3.0).rgb, textureLod(map, uv, 4.0).rgb, 0.6);
    float lw = dot(wide, vec3(0.299, 0.587, 0.114));
    float dev = (dot(near, vec3(0.299, 0.587, 0.114)) - lw) / (0.9 * lw + 2.0 * usual + 1e-5);
    return mix(near, wide, exp(-dev * dev));
  }
  varying vec3 vHouseWorld;
  varying vec3 vHouseNormal;`;

/**
 * How far a wall stands in the way of the spot looked down at (0 to 1; House.inTheWay is the same
 * on the CPU). The top of a wall faces up and stays: it marks where the faded wall stands.
 */
const SCREEN_PARS = /* glsl */ `
  uniform float houseCutaway; // 0 to 1: how far walls in the way are faded now
  uniform vec3 houseFocus;    // the spot looked down at
  float houseInTheWay() {
    if (houseCutaway <= 0.0 || vHouseWorld.y < houseFocus.y - ${CUTAWAY.below.toFixed(2)}) return 0.0;
    vec2 toCamera = cameraPosition.xz - houseFocus.xz;
    float reach = length(toCamera);
    if (reach < 1e-3) return 0.0;
    vec2 dir = toCamera / reach;
    vec3 n = normalize(vHouseNormal);
    if (!gl_FrontFacing) n = -n;
    float across = length(n.xz);
    if (across < 0.5) return 0.0;
    float along = dot(vHouseWorld.xz - houseFocus.xz, dir);
    return houseCutaway
      * smoothstep(${CUTAWAY.along[0].toFixed(2)}, ${CUTAWAY.along[1].toFixed(2)}, along)
      * smoothstep(${CUTAWAY.facing[0].toFixed(2)}, ${CUTAWAY.facing[1].toFixed(2)}, dot(n.xz, dir) / across);
  }`;

/** For things without a lightmap of their own (furniture, room outlines, plain ground). */
const PLAIN_PARS = /* glsl */ `
  uniform vec3 houseAmbient;
  uniform vec3 houseGlow;   // indoors: the lamps (plainLamps)
  uniform vec2 houseLocal;  // the daylight and the lamplight where it stands, against a usual floor's (House.lightAround)
  varying vec3 vHouseWorld;
  varying vec3 vHouseNormal;`;

// Runs after the surface's own color is in diffuseColor. HOUSE_LIGHT is the light on it.
const HOUSE_BODY = /* glsl */ `
  vec3 houseLight;
  vec3 houseWarm = vec3(0.0); // the part of houseLight that came from the sun or the lamps
  vec3 houseSun = vec3(0.0);  // the part of houseLight that is the sun itself (SUN_LIGHT)
  HOUSE_LIGHT
  // No surface is pure white (docs/specs/lighting.md, 8a): pale colours are pressed toward 0.85,
  // not cut off at it, so the weave of a white rug or the grain of pale wood still shows.
  diffuseColor.rgb = min(diffuseColor.rgb, 0.55 + (diffuseColor.rgb - 0.55) * 0.667);
  // The room's own light past its usual level is compressed before it meets the surface's colour
  // (at most 1.25). The sun has its own headroom on top (SUN_HEADROOM): squeezed into the same
  // 1.25, a sun patch on a pale rug came out barely brighter than the shade beside it, and a
  // piece of furniture in the sun looked as flat as one out of it.
  {
    vec3 base = houseLight - houseSun;
    float l = dot(base, vec3(0.299, 0.587, 0.114));
    float kept = l <= 0.75 ? l : 0.75 + 0.5 * (1.0 - exp(-(l - 0.75) / 0.5));
    float k = kept / max(l, 1e-4);
    float ls = dot(houseSun, vec3(0.299, 0.587, 0.114));
    float ks = ls > 1e-4 ? ${SUN_HEADROOM.toFixed(2)} * (1.0 - exp(-ls / ${SUN_HEADROOM.toFixed(2)})) / ls : 1.0;
    houseLight = base * k + houseSun * ks;
    houseWarm = (houseWarm - houseSun) * k + houseSun * ks;
  }
  diffuseColor.rgb *= houseLight;
  // The light shift (docs/specs/lighting.md, 5): what the sky alone lights leans blue, what the
  // sun or the lamps light leans amber, the more so as the sun gets low and after sunset.
  {
    float total = dot(houseLight, vec3(0.299, 0.587, 0.114));
    float warmth = clamp(dot(houseWarm, vec3(0.299, 0.587, 0.114)) / max(total, 1e-4), 0.0, 1.0);
    float shift = (sunOn > 0.0 ? min(0.15 + 0.5 * sunGolden + 0.4 * sunBlueHour, 0.75) : 0.0) * (1.0 - 0.5 * sunWork);
    diffuseColor.rgb *= mix(vec3(1.0), mix(vec3(0.86, 0.92, 1.08), vec3(1.08, 0.96, 0.84), smoothstep(0.15, 0.7, warmth)), shift);
    // Shade is teal at any hour: what only the sky and the room's bounce light reach leans
    // blue-green, the more so the darker it is; what the sun or a lamp lights keeps its warmth.
    float shade = (1.0 - smoothstep(0.1, 0.75, dot(diffuseColor.rgb, vec3(0.299, 0.587, 0.114)))) * (1.0 - smoothstep(0.25, 0.65, warmth));
    diffuseColor.rgb *= mix(vec3(1.0), vec3(0.82, 0.94, 1.06), shade * 0.45 * (1.0 - 0.5 * sunWork));
  }
  // Bright things roll off instead of clipping.
  diffuseColor.rgb = diffuseColor.rgb / (1.0 + max(diffuseColor.rgb - 0.7, 0.0) * 0.8);
  // More contrast (docs/specs/lighting.md, 8e): darks deeper, on brightness only, so colours keep.
  {
    float l = clamp(dot(diffuseColor.rgb, vec3(0.299, 0.587, 0.114)), 0.0, 1.0);
    float curved = l * l * (3.0 - 2.0 * l);
    diffuseColor.rgb *= mix(1.0, curved / max(l, 1e-4), 0.2);
  }
  // Grain in the shade: a surface out of the light shows a fine, even noise (as a photo or a
  // soft shadow's edge does), not a smooth flat fill. Fixed to the surface in specks of about
  // 4 mm, so it does not crawl when the camera moves; strongest in the dark, gone in full light.
  {
    vec3 cell = floor(vHouseWorld * 250.0);
    float grain = fract(sin(dot(cell, vec3(12.9898, 78.233, 37.719))) * 43758.5453) - 0.5;
    float dark = 1.0 - smoothstep(0.08, 0.55, dot(diffuseColor.rgb, vec3(0.299, 0.587, 0.114)));
    diffuseColor.rgb *= 1.0 + grain * 0.09 * dark * (1.0 - 0.5 * sunWork);
  }
  // The air between here and the eye (sun.ts, houseFog).
  diffuseColor.rgb = houseFog(diffuseColor.rgb, vHouseWorld);`;

/**
 * The sun on a surface, where the shadow map says it reaches. The patch's edge is soft (more so
 * when the sun is low and shadows are long). Only the patch is lit by the sun itself and reads
 * warm; the light it throws around the room is the flat, cooler bounce (LIT).
 */
const SUN_LIGHT = /* glsl */ `
  if (sunOn > 0.0 && sunColor.r > 0.0) {
    vec3 sunNormal = normalize(vHouseNormal);
    if (!gl_FrontFacing) sunNormal = -sunNormal;
    float sunFacing = dot(sunNormal, sunDirection);
    float sunLit = 0.0;
    if (sunFacing > 0.0) sunLit = houseSunAt(vHouseWorld + sunNormal * 0.03, sunTexel * mix(1.25, 2.0, sunGolden));
    vec3 sunHere = sunColor * (sunLit * sunFacing) * 2.4;
    houseLight += sunHere;
    houseWarm += sunHere;
    houseSun = sunHere;
  }`;

/** How much of a disc around `p` the sun reaches (0 to 1), by the shadow map; `radius` in its units. */
const SUN_AT = /* glsl */ `
  float houseSunAt(vec3 p, float radius) {
    vec4 inSun = sunMatrix * vec4(p, 1.0);
    vec3 at = inSun.xyz / inSun.w * 0.5 + 0.5;
    if (any(lessThanEqual(at, vec3(0.0))) || any(greaterThanEqual(at, vec3(1.0)))) return 1.0; // beyond the map: open ground
    // Eight taps spread evenly on a disc, the same for every pixel: a half-lit edge comes out as a
    // smooth gradient, not speckle. (A pattern turned per pixel left a grainy streak where a
    // wall meets the ceiling: some taps there land past the ceiling's edge.)
    mat2 r = mat2(1.0, 0.0, 0.0, 1.0);
    // A wider disc reaches farther across a slanted surface: the bias grows with it.
    float bias = sunBias * (1.0 + radius / sunTexel);
    float lit = 0.0;
    lit += at.z - bias <= textureLod(sunDepth, at.xy + r * vec2(-0.613, 0.617) * radius, 0.0).r ? 1.0 : 0.0;
    lit += at.z - bias <= textureLod(sunDepth, at.xy + r * vec2(0.170, -0.040) * radius, 0.0).r ? 1.0 : 0.0;
    lit += at.z - bias <= textureLod(sunDepth, at.xy + r * vec2(-0.299, 0.791) * radius, 0.0).r ? 1.0 : 0.0;
    lit += at.z - bias <= textureLod(sunDepth, at.xy + r * vec2(0.645, 0.493) * radius, 0.0).r ? 1.0 : 0.0;
    lit += at.z - bias <= textureLod(sunDepth, at.xy + r * vec2(-0.651, -0.717) * radius, 0.0).r ? 1.0 : 0.0;
    lit += at.z - bias <= textureLod(sunDepth, at.xy + r * vec2(0.421, 0.027) * radius, 0.0).r ? 1.0 : 0.0;
    lit += at.z - bias <= textureLod(sunDepth, at.xy + r * vec2(-0.817, -0.271) * radius, 0.0).r ? 1.0 : 0.0;
    lit += at.z - bias <= textureLod(sunDepth, at.xy + r * vec2(0.977, -0.108) * radius, 0.0).r ? 1.0 : 0.0;
    return lit / 8.0;
  }`;

const LIT = /* glsl */ `
  if (houseBaked > 0.5) {
    // Lamps light pools around themselves and drop off between them (docs/specs/lighting.md, 8b).
    vec3 lamp = houseLightmap(houseLamps, vHouseLight, houseLightFloor.y);
    // Working hours (sunWork): every lamp on, bright and even enough to judge furniture by,
    // in a neutral warm white (about 3800 K) instead of the time of day's colour.
    float lampFall = mix(houseLampFall, 1.0, sunWork);
    vec3 lampTint = mix(houseLampTint, vec3(${WORK_LAMP.r.toFixed(3)}, ${WORK_LAMP.g.toFixed(3)}, ${WORK_LAMP.b.toFixed(3)}) * houseLampGain * ${WORK_LAMP_GAIN.toFixed(2)}, sunWork);
    vec3 lampLight = min(pow(lamp / houseLampPeak, vec3(lampFall)) * houseLampPeak, lamp) * lampTint;
    // By day the lamps are a neutral fill, not a warm light against the shade: their warmth comes in with the night.
    houseWarm = lampLight * mix(0.25, 1.0, sunOn > 0.0 ? sunNight : 1.0) * (1.0 - 0.75 * sunWork);
    // The sun that comes in through each group of windows, thrown back into the room. It is
    // diffuse: the room is lit about evenly, not brighter by the window, so the bake's gradient
    // is flattened hard toward BOUNCE_MAX. It is a neutral, slightly cool light (houseSunTint):
    // the warm thing is the patch of sun itself, so it does not count toward houseWarm.
    // Read at a coarse level (about 65 cm): these bakes are the noisiest, and the light is diffuse anyway.
    float thrown = dot(textureLod(houseSunA, vHouseLight, 4.0).rgb, houseSunWA) + dot(textureLod(houseSunB, vHouseLight, 4.0).rgb, houseSunWB) + dot(textureLod(houseSunC, vHouseLight, 4.0).rgb, houseSunWC);
    vec3 bounce = houseSunTint * thrown;
    bounce = ${BOUNCE_MAX.toFixed(2)} * (1.0 - exp(-3.0 * bounce / ${BOUNCE_MAX.toFixed(2)}));
    houseLight = houseLightmap(houseSky, vHouseLight, houseLightFloor.x) * houseSkyTint + bounce + lampLight;
    // At night a room is not black: a faint teal light from the city sky and the windows reaches everywhere.
    houseLight += vec3(0.035, 0.055, 0.065) * sunNight;
  } else {
    // Not baked yet: a little shading by the way a surface faces, so that corners show.
    houseLight = vec3(0.62 + 0.3 * dot(normalize(vHouseNormal), vec3(0.35, 0.8, 0.45)));
  }
  ${SUN_LIGHT}`;

/**
 * The back of a ceiling or a roof, seen through a crack where a ceiling does not meet its wall: the
 * dark of the attic, not the light the outside of the roof gets.
 */
const BACK_OF_LID = /* glsl */ `
  if (!gl_FrontFacing) {
    houseLight *= 0.2;
    houseWarm = vec3(0.0);
  }`;

/** What stands around the house has no lightmap: the open sky by the way a surface faces, and the sun. */
const OUTDOORS = /* glsl */ `
  houseLight = houseOpen * (0.62 + 0.38 * normalize(vHouseNormal).y);
  ${SUN_LIGHT}`;

/** The light on things that are not lit by the lightmap: lamps' faces, glass, water. */
const UNLIT: Record<string, string> = {
  // Switched off, a lamp's face is a pale fitting lit by the room's daylight.
  light:
    'houseLight = mix((sunOn > 0.0 ? sunSky : vec3(1.0)) * 0.3, vec3(1.7) * houseLampTint / max(max(houseLampTint.r, houseLampTint.g), 1e-3), houseLampsLit); houseWarm = houseLight * houseLampsLit;',
  glass: 'houseLight = sunOn > 0.0 ? 0.35 + 0.65 * sunSky : vec3(1.0);',
  water: 'houseLight = (sunOn > 0.0 ? sunSky : vec3(0.85)) * (0.8 + 0.2 * sin(vHouseWorld.x * 3.1 + vHouseWorld.z * 2.3));',
  aerial: OUTDOORS,
  canopy: OUTDOORS,
  context: OUTDOORS,
};

/** Without a lightmap: the room's usual light by the way a surface faces, and the sun. */
const PLAIN = /* glsl */ `
  float plainUp = normalize(vHouseNormal).y;
  // The sky's light comes down from the windows and the ceiling: a top gets the most, the sides
  // less, an underside (a seat's skirt, the space under a table) little, so a piece has form.
  houseLight = houseAmbient * houseLocal.x * (0.62 + 0.38 * plainUp);
  // The warm light around it: the lamps.
  houseWarm = houseGlow * houseLocal.y * (0.75 + 0.25 * plainUp);
  // Working hours: the room's lamps all on (as LIT does for the house's own surfaces). They hang
  // from the ceiling: a seat or a table top gets what the floor gets, the sides of a piece less,
  // its underside little, so it has form instead of glowing evenly.
  houseWarm += vec3(${WORK_LAMP.r.toFixed(3)}, ${WORK_LAMP.g.toFixed(3)}, ${WORK_LAMP.b.toFixed(3)}) * ${(FLOOR_LAMPLIGHT * WORK_LAMP_GAIN).toFixed(3)} * sunWork * houseLocal.y * (0.4 + 0.6 * smoothstep(-0.6, 1.0, plainUp));
  houseLight += houseWarm;
  // The faint teal of the night that reaches every room (as LIT): at night a piece is dim, not black.
  houseLight += vec3(0.035, 0.055, 0.065) * sunNight;
  ${SUN_LIGHT}`;

const SKY_FRAGMENT = /* glsl */ `
  uniform vec3 tint;      // the day's brightness (dark at night)
  uniform vec3 toSun;
  uniform float sunUp;    // 0 when no sun is being simulated
  uniform vec3 hazeColor;  // the haze (sun.ts): the horizon fades into it as the ground does
  uniform vec3 hazeSun;
  uniform float sunGolden;
  uniform float sunBlueHour;
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
    // Near the horizon the sky is seen through the most air: it turns to the haze the ground fades into.
    float toward = max(dot(d, toSun), 0.0) * sunUp;
    vec3 haze = hazeColor + hazeSun * (pow(toward, 6.0) * 0.8 + pow(toward, 32.0) * 0.6);
    color = mix(color, haze, (1.0 - smoothstep(-0.05, 0.35, d.y)) * 0.85);
    // After sunset the sky overhead goes violet-blue; with the sun low its side of the horizon glows orange.
    color = mix(color, vec3(0.2, 0.22, 0.48), sunBlueHour * smoothstep(0.0, 0.6, d.y) * 0.7);
    vec2 across = normalize(d.xz + 1e-5);
    vec2 sunAcross = normalize(toSun.xz + 1e-5);
    float sunSide = pow(max(dot(across, sunAcross), 0.0), 3.0) * (1.0 - smoothstep(-0.02, 0.4, d.y));
    color += vec3(1.0, 0.62, 0.38) * sunSide * max(sunGolden * step(-0.12, toSun.y), sunBlueHour) * 0.55;
    gl_FragColor = vec4(color, 1.0);
    #include <colorspace_fragment>
  }`;

/** The windows' glass, drawn into the depth only (see House.backing). */
const GLASS_DEPTH = new MeshBasicMaterial({ colorWrite: false, side: DoubleSide });

/** What does not cast a shadow (or is not there to be drawn into the sun's shadow map). */
const NO_SHADOW = new Set(['glass', 'sky', 'mirror', 'light', 'water']);

/**
 * Light anything without a lightmap (furniture, room outlines, a plain ground) the way the
 * house is lit: `ambient` is the light around it (`plainLight`), the sun comes on top. `local`
 * scales the daylight (x) and the lamps (y) to the spot the thing stands on (House.lightAround).
 * Marks the material's meshes as casting nothing by itself: the caller sets `userData.caster`.
 */
export function shadePlain(
  material: MeshBasicMaterial,
  ambient: IUniform<Color>,
  sunUniforms: Record<string, IUniform>,
  warm: IUniform<Color> = { value: new Color(0, 0, 0) },
  local: IUniform<Vector2> = { value: new Vector2(1, 1) },
): void {
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, sunUniforms, { houseAmbient: ambient, houseGlow: warm, houseLocal: local });
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vHouseWorld;\nvarying vec3 vHouseNormal;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvHouseWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;\nvHouseNormal = mat3(modelMatrix) * normal;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${PLAIN_PARS}\n${SUN_PARS}\n${SUN_AT}`)
      .replace('#include <map_fragment>', `#include <map_fragment>\n${HOUSE_BODY.replace('HOUSE_LIGHT', PLAIN)}`);
  };
  material.customProgramCacheKey = () => 'house-plain';
}

/**
 * The sky's light around things without a lightmap: indoors (`inside`) a room's usual daylight,
 * else open ground. Indoors the lamps come on top (`plainLamps`).
 */
export function plainLight(sky: Color, sunOn: boolean, inside: boolean, out: Color): Color {
  const day = sunOn ? sky : new Color(1, 1, 1);
  if (!inside) return out.copy(day).multiplyScalar(0.9);
  return out.copy(day).multiplyScalar(FLOOR_DAYLIGHT);
}

/** How strong the lamps are, by night (0 to 1): full by day, dimmer at night, when they are all the light there is and should only light around themselves. */
function lampsOn(night: number): number {
  return 1 - (1 - LAMPS_AT_NIGHT) * night;
}

/** A room's lamps on things without a lightmap, as on a usual floor: `lit` is how far they are switched on (0 to 1). */
export function plainLamps(night: number, lit: number, out: Color): Color {
  return out.copy(LAMP_COLOR).lerp(LAMP_NIGHT, night).multiplyScalar(FLOOR_LAMPLIGHT * lampsOn(night) * lit);
}

/** A lightmap read back for the CPU: linear brightness per texel, `size` x `size`. */
interface Lightmap {
  size: number;
  value: Float32Array;
}

/** The largest lightmap kept for the CPU: it is only read for the light around a piece of furniture. */
const LIGHTMAP_READ = 512;

function readLightmap(texture: Texture): Lightmap | null {
  const image = texture.image as CanvasImageSource & { width: number; height: number };
  if (!image?.width) return null;
  const size = Math.min(LIGHTMAP_READ, image.width);
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  if (!ctx) return null;
  ctx.drawImage(image, 0, 0, size, size);
  let rgba: Uint8ClampedArray;
  try {
    rgba = ctx.getImageData(0, 0, size, size).data;
  } catch {
    return null; // a picture from another origin without CORS cannot be read back
  }
  const value = new Float32Array(size * size);
  // Stored with the display curve (as the shader's sRGB texture is): back to linear light.
  const linear = (c: number) => Math.pow(c / 255, 2.2);
  for (let i = 0; i < value.length; i++) value[i] = 0.299 * linear(rgba[i * 4]!) + 0.587 * linear(rgba[i * 4 + 1]!) + 0.114 * linear(rgba[i * 4 + 2]!);
  return { size, value };
}

/** A lightmap's value around (u, v), averaged over a few centimetres so the bake's noise does not count. */
function sampleLightmap(map: Lightmap, u: number, v: number): number {
  const cx = Math.round(u * map.size);
  const cy = Math.round(v * map.size);
  let sum = 0;
  let n = 0;
  for (let dy = -2; dy <= 2; dy++) {
    for (let dx = -2; dx <= 2; dx++) {
      const x = cx + dx;
      const y = cy + dy;
      if (x < 0 || y < 0 || x >= map.size || y >= map.size) continue;
      sum += map.value[y * map.size + x]!;
      n++;
    }
  }
  return n ? sum / n : 0;
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
    houseLampsLit: { value: 1 },
    houseLampGain: { value: 1 },
    houseLampPeak: { value: 1 },
    houseLampFall: { value: LAMP_FALL_DAY },
    houseLightTexel: { value: 1 / 2048 },
    houseLightFloor: { value: new Vector2(0.05, 0.05) },
    houseOpen: { value: new Color(1, 1, 1) },
    houseSunA: { value: null },
    houseSunB: { value: null },
    houseSunC: { value: null },
    houseSunWA: { value: new Vector3() },
    houseSunWB: { value: new Vector3() },
    houseSunWC: { value: new Vector3() },
    houseSunTint: { value: new Color(0, 0, 0) },
    houseCutaway: { value: 0 },
    houseFocus: { value: new Vector3() },
  };

  /** From the pictures' values to light on screen. */
  private gain = { sky: 1, lamps: 1, sun: 0, open: 1 };
  /**
   * The house's own parts, for showing and hiding: `lid` (ceilings, roofs) is left off from
   * outside; `far` is the lawn beyond the lot, left out once the surroundings are there to be seen
   * instead; `storey` hides the floors above one being looked down into; `ghost` is the see-through
   * copy of a wall, drawn only while walls in the way are faded.
   */
  private parts: { object: Object3D; storey: number | null; lid: boolean; far: boolean; ghost?: boolean }[] = [];
  /** Whether walls in the way of the spot looked down at are faded (setCutaway), and how far they are now. */
  private cutawayOn = false;
  private cutaway = 0;
  private lastUpdate = 0;
  /** Whether the surroundings are in (the far lawn is then not needed). */
  private surroundings = false;
  /** The highest storey drawn, or null for all of them. */
  private top: number | null = null;
  /** Everything that casts a shadow is drawn, whatever the view (while the sun's shadow map is made). */
  private everything = false;
  /** Resolves once every picture and the surroundings have arrived (or failed to). */
  ready: Promise<void> = Promise.resolve();
  /** The box around the house and its lot (without that far lawn): what the sun's shadow map must cover. */
  private lot = new Box3();
  private mirrors: { glass: Reflector; plain: Mesh; center: Vector3; storey: number | null }[] = [];
  private sky: Mesh<SphereGeometry, ShaderMaterial>;
  /**
   * Inside, a crack in the rebuilt mesh (a ceiling's edge that stops short of its wall) would show
   * the sky. This dark sphere is drawn after everything solid and fills only what nothing else
   * covers; the windows' glass is drawn into the depth first (GLASS_DEPTH), so the sky still shows
   * through them. It covers only what is well above the eye: an open doorway still shows the sky.
   */
  private backing: Mesh<SphereGeometry, ShaderMaterial>;
  /** The sun's uniforms (sun.ts), for the time of day. */
  private sunUniforms: Record<string, IUniform> | null = null;
  /** The glow of the sun thrown back into the rooms, for things without a lightmap (plainLight). */
  readonly glow = new Color(0, 0, 0);
  /** Goes up when lightAround can answer (the lightmaps have been read). */
  lightVersion = 0;
  private solid: Mesh | null = null;
  private solidUv: Float32Array | null = null;
  private lightPixels: { sky: Lightmap; lamps: Lightmap; usual: [sky: number, lamps: number] } | null = null;
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
    this.backing = new Mesh(
      new SphereGeometry(1000, 24, 12),
      new ShaderMaterial({
        uniforms: { shade: { value: new Color(0.1, 0.1, 0.1) } },
        vertexShader: /* glsl */ `
          varying vec3 vDirection;
          void main() {
            vDirection = position;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          }`,
        fragmentShader: /* glsl */ `
          uniform vec3 shade;
          varying vec3 vDirection;
          void main() {
            if (normalize(vDirection).y < 0.2) discard;
            gl_FragColor = vec4(shade, 1.0);
            #include <colorspace_fragment>
          }`,
        side: BackSide,
        depthWrite: false,
      }),
    );
    this.backing.renderOrder = 1.5;
    this.backing.frustumCulled = false;
    this.backing.userData.kind = 'sky';
    this.backing.raycast = () => {};
    this.backing.visible = false;
    this.group.add(this.backing);
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
    // The sky shares the haze's uniforms, so it moves with the sun.
    house.sunUniforms = sunUniforms;
    Object.assign(house.sky.material.uniforms, {
      hazeColor: sunUniforms.hazeColor,
      hazeSun: sunUniforms.hazeSun,
      sunGolden: sunUniforms.sunGolden,
      sunBlueHour: sunUniforms.sunBlueHour,
    });
    const anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());

    const made = new Map<string, MeshBasicMaterial>();
    const patterns = new Map<string, Texture | null>();
    const solid: Float32Array[] = [];
    // Each solid triangle's place in the lightmaps (-1 where it has none), for lightAround.
    const solidUv: Float32Array[] = [];
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
        const position = flat.getAttribute('position');
        solid.push(position.array as Float32Array);
        const uv1 = flat.getAttribute('uv1');
        solidUv.push(uv1 ? Float32Array.from({ length: position.count * 2 }, (_, i) => (i & 1 ? uv1.getY(i >> 1) : uv1.getX(i >> 1))) : new Float32Array(position.count * 2).fill(-1));
      }
      if (kind === 'hidden') return;
      house.triangles += (mesh.geometry.index?.count ?? mesh.geometry.getAttribute('position').count) / 3;
      if (kind === 'mirror') {
        house.addMirror(mesh.geometry, from.color, extras.storey ?? null);
        return;
      }
      // Ceilings and roofs are drawn from both sides: where a ceiling's edge does not quite meet the
      // wall, the roof's underside fills the crack instead of the sky behind it (BACK_OF_LID). From
      // outside the lid is not drawn at all, so the dollhouse view is unchanged.
      const lid = LID.has(extras.layer);
      const screen = SCREEN.has(extras.layer) && !(kind in UNLIT);
      const key = `${extras.material}|${extras.both}|${lid}|${screen}`;
      let material = made.get(key);
      if (!material) {
        material = new MeshBasicMaterial({ color: from.color, side: extras.both || lid ? DoubleSide : FrontSide });
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
        house.shade(material, sunUniforms, kind, lid && !extras.both, screen ? 'solid' : null);
        made.set(key, material);
        house.materials.push(material);
      }
      const part = new Mesh(mesh.geometry, material);
      // Each part is one material all over the house: there is nothing to gain from leaving it
      // out of a view, and a mirror's slanted view would leave it out wrongly.
      part.frustumCulled = false;
      part.name = mesh.name;
      part.userData = { ...extras, kind, caster: !NO_SHADOW.has(kind), screen };
      if (kind === 'glass') part.renderOrder = 2;
      house.group.add(part);
      house.parts.push({ object: part, storey: extras.storey ?? null, lid: LID.has(extras.layer), far: extras.layer === 'far' });
      if (screen) {
        // A wall is drawn solid where it is not in the way, and by this see-through copy where it
        // is: the copy does not write depth, so what is behind it is drawn whatever the order.
        let faded = made.get(`${key}|faded`);
        if (!faded) {
          faded = new MeshBasicMaterial({ color: material.color, map: material.map, side: material.side, transparent: true, depthWrite: false });
          faded.name = `${extras.material} faded`;
          house.shade(faded, sunUniforms, kind, false, 'faded');
          made.set(`${key}|faded`, faded);
          house.materials.push(faded);
        }
        const ghost = new Mesh(mesh.geometry, faded);
        ghost.frustumCulled = false;
        ghost.name = mesh.name;
        ghost.userData = { kind: 'ghost', caster: false };
        ghost.raycast = () => {};
        ghost.visible = false;
        house.group.add(ghost);
        house.parts.push({ object: ghost, storey: extras.storey ?? null, lid: false, far: false, ghost: true });
      }
      if (kind === 'glass') {
        // The glass in the depth only, before the backing: the backing stays out of the windows.
        const depth = new Mesh(mesh.geometry, GLASS_DEPTH);
        depth.renderOrder = 1;
        depth.frustumCulled = false;
        depth.userData = { kind: 'glass-depth', caster: false };
        depth.raycast = () => {};
        house.group.add(depth);
        house.parts.push({ object: depth, storey: extras.storey ?? null, lid: LID.has(extras.layer), far: extras.layer === 'far' });
      }
      // The lawn beyond the lot is not part of what the shadow map must cover.
      if (extras.layer !== 'far' && kind !== 'light' && kind !== 'water') house.lot.union(mesh.geometry.boundingBox ?? new Box3().setFromBufferAttribute(mesh.geometry.getAttribute('position') as BufferAttribute));
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
    const uvs = new Float32Array(solidUv.reduce((n, a) => n + a.length, 0));
    offset = 0;
    for (const a of solidUv) {
      uvs.set(a, offset);
      offset += a.length;
    }
    house.solidUv = uvs;
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
            // Mipmaps: the shader reads a coarse level to even out the bake's noise (houseLightmap).
            texture.generateMipmaps = true;
            texture.minFilter = LinearMipmapLinearFilter;
            resolve(texture);
          },
          undefined,
          () => resolve(null),
        );
      });
    const pending: Promise<unknown>[] = [];
    // Until a group's picture is there (or if the bake made none), nothing is thrown back.
    const none = new DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);
    none.needsUpdate = true;
    house.textures.push(none);
    for (const name of ['houseSunA', 'houseSunB', 'houseSunC']) house.uniforms[name]!.value = none;
    if (info.light) {
      const { range, floors } = info.light;
      const exposure = Math.min(60, OPEN_DAYLIGHT / Math.max(floors.sky, 1e-3));
      house.gain.sky = range.sky * exposure * (FLOOR_DAYLIGHT / OPEN_DAYLIGHT);
      house.gain.lamps = range.lamps * Math.min(60, FLOOR_LAMPLIGHT / Math.max(floors.lamps, 1e-3));
      house.uniforms.houseLampGain!.value = house.gain.lamps;
      // Near a lamp the lightmap is a few times the usual floor's; the falloff is steepened below that.
      house.uniforms.houseLightTexel!.value = 1 / Math.max(1, info.lightmap.size);
      (house.uniforms.houseLightFloor!.value as Vector2).set(floors.sky / Math.max(range.sky, 1e-6), floors.lamps / Math.max(range.lamps, 1e-6));
      house.uniforms.houseLampPeak!.value = Math.min(1, (2.5 * floors.lamps) / Math.max(range.lamps, 1e-6));
      house.gain.sun = (range.sun ?? 0) * exposure * SUN_TO_SKY;
      house.gain.open = (info.light.outdoors ?? OUTDOOR_EXPOSURE) * exposure;
      pending.push(Promise.all([light('light_sky.png'), light('light_lamps.png')]).then(([sky, lamps]) => {
        if (house.disposed) return void (sky?.dispose(), lamps?.dispose());
        if (!sky || !lamps) return;
        house.textures.push(sky, lamps);
        house.uniforms.houseSky!.value = sky;
        house.uniforms.houseLamps!.value = lamps;
        house.uniforms.houseBaked!.value = 1;
        const skyPixels = readLightmap(sky);
        const lampPixels = readLightmap(lamps);
        if (skyPixels && lampPixels) {
          house.lightPixels = { sky: skyPixels, lamps: lampPixels, usual: [floors.sky / Math.max(range.sky, 1e-6), floors.lamps / Math.max(range.lamps, 1e-6)] };
          house.lightVersion++;
        }
        changed();
      }));
      ['houseSunA', 'houseSunB', 'houseSunC'].slice(0, Math.ceil((info.light.groups ?? 0) / 3)).forEach((name, k) => {
        pending.push(light(`light_sun_${k}.png`).then((picture) => {
          if (house.disposed) return void picture?.dispose();
          if (!picture) return;
          house.textures.push(picture);
          house.uniforms[name]!.value = picture;
          changed();
        }));
      });
    } else {
      house.gain.open = 0.9;
    }
    if (info.context) pending.push(house.loadContext(info.context, at, renderer, sunUniforms, changed).catch(() => {}));
    house.ready = Promise.all(pending).then(() => {});
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
    this.surroundings = true;
    this.applyVisibility();
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
    this.applyVisibility();
  }

  /** Draw the storeys up to `top` only (looking down into a room), or all of them (null). */
  setTopStorey(top: number | null): void {
    this.top = top;
    this.applyVisibility();
  }

  /** Looking down at `focus` (on its floor): the walls between the camera and it fade. Null: none do. */
  setCutaway(focus: { x: number; y: number; z: number } | null): void {
    this.cutawayOn = !!focus;
    if (focus) (this.uniforms.houseFocus!.value as Vector3).set(focus.x, focus.y, focus.z);
  }

  /** Whether a click on `object` at `point` goes through it, to what is behind: a wall faded out of the way. */
  seesThrough(object: Object3D, point: Vector3, normal: Vector3 | undefined, eye: Vector3): boolean {
    if (!object.userData.screen || !normal) return false;
    const n = normal.clone().transformDirection(object.matrixWorld);
    if (n.dot(new Vector3().subVectors(eye, point)) < 0) n.negate();
    return this.inTheWay(point, n, eye) > 0.5;
  }

  /** SCREEN_PARS's houseInTheWay, for a point on a surface facing `normal` (toward the eye). */
  private inTheWay(point: Vector3, normal: Vector3, eye: Vector3): number {
    const focus = this.uniforms.houseFocus!.value as Vector3;
    if (this.cutaway <= 0 || point.y < focus.y - CUTAWAY.below) return 0;
    const reach = Math.hypot(eye.x - focus.x, eye.z - focus.z);
    const across = Math.hypot(normal.x, normal.z);
    if (reach < 1e-3 || across < 0.5) return 0;
    const dx = (eye.x - focus.x) / reach;
    const dz = (eye.z - focus.z) / reach;
    const along = (point.x - focus.x) * dx + (point.z - focus.z) * dz;
    const facing = (normal.x * dx + normal.z * dz) / across;
    return this.cutaway * MathUtils.smoothstep(along, CUTAWAY.along[0], CUTAWAY.along[1]) * MathUtils.smoothstep(facing, CUTAWAY.facing[0], CUTAWAY.facing[1]);
  }

  /** The storey whose floor is nearest a height (interior frame y). */
  storeyAt(y: number): number {
    let best = 0;
    this.info.storeys.forEach((s, i) => {
      if (Math.abs(s.z - y) < Math.abs((this.info.storeys[best]?.z ?? 0) - y)) best = i;
    });
    return best;
  }

  /** While on, everything that casts a shadow is drawn: the lid and every storey (for the sun's shadow map). */
  setEverything(on: boolean): void {
    this.everything = on;
    this.applyVisibility();
  }

  private applyVisibility(): void {
    this.backing.visible = this.lidOn && !this.everything;
    for (const p of this.parts) {
      if (p.far && this.surroundings) p.object.visible = false;
      else if (this.everything) p.object.visible = true;
      else p.object.visible = (!p.ghost || this.cutaway > 0) && (this.lidOn || !p.lid) && (this.top === null || p.storey === null || p.storey <= this.top);
    }
  }

  /**
   * Daylight now: the sun's sky color, the way to the sun (null when no sun is simulated) and
   * the sun's color (black when it is down), and how far the lamps are switched on (0 to 1).
   */
  setLight(sky: Color, toSun: Vector3 | null, sun: Color, lampsLit = 1): void {
    // With the sun simulated its sky color dims the daylight; the lamps stay as they are.
    const day = toSun ? sky : new Color(1, 1, 1);
    (this.uniforms.houseSkyTint!.value as Color).copy(day).multiplyScalar(this.gain.sky);
    (this.uniforms.houseOpen!.value as Color).copy(day).multiplyScalar(this.gain.open);
    const mood = this.sunUniforms;
    const night = toSun ? ((mood?.sunNight?.value as number) ?? 0) : 0;
    // The lamps: a neutral fill by day, warmer as it gets dark, and nothing when switched off.
    (this.uniforms.houseLampTint!.value as Color)
      .copy(LAMP_COLOR)
      .lerp(LAMP_NIGHT, night)
      .multiplyScalar(this.gain.lamps * lampsOn(night) * lampsLit);
    this.uniforms.houseLampsLit!.value = lampsLit;
    this.uniforms.houseLampFall!.value = LAMP_FALL_DAY + (LAMP_FALL_NIGHT - LAMP_FALL_DAY) * night;
    // The sun thrown around a room: as bright as the sun is, but not its colour.
    const sunLevel = (sun.r + sun.g + sun.b) / 3;
    (this.uniforms.houseSunTint!.value as Color).copy(THROWN_BACK).multiplyScalar(sunLevel * this.gain.sun);
    const through = this.sunThrough(toSun);
    // Furniture and room outlines have no lightmap: they get the room's glow as one colour.
    this.glow.copy(this.uniforms.houseSunTint!.value as Color).multiplyScalar(FLOOR_DAYLIGHT * Math.min(through, 1.5));
    const u = this.sky.material.uniforms;
    // The sky picture has its own blue: only part of the sky light's colour goes on top of it.
    const grey = (day.r + day.g + day.b) / 3;
    (u.tint!.value as Color).setRGB(grey, grey, grey).lerp(day, 0.5);
    // A crack reads as the dark of a seam, a little lighter by day.
    (this.backing.material.uniforms.shade!.value as Color).setRGB(grey, grey, grey).multiplyScalar(0.12).addScalar(0.02);
    u.sunUp!.value = toSun && toSun.y > -0.05 ? 1 : 0;
    if (toSun) (u.toSun!.value as Vector3).copy(toSun);
  }

  /**
   * The daylight and the lamplight on the floor around (x, z), below `y`, against a usual floor's
   * (1 = as lit as a usual floor). Furniture has no lightmap of its own: it takes the light of the
   * spot it stands on, so a sofa in a dim corner is as dim as the corner. Null until the
   * lightmaps are there, or where there is no floor below.
   */
  lightAround(x: number, y: number, z: number, reach: number): { sky: number; lamps: number } | null {
    const bvh = this.bvh;
    const uv = this.solidUv;
    const maps = this.lightPixels;
    if (!bvh || !uv || !maps) return null;
    const position = bvh.geometry.getAttribute('position');
    const ray = new Ray(new Vector3(), new Vector3(0, -1, 0));
    const a = new Vector3();
    const b = new Vector3();
    const c = new Vector3();
    const weights = new Vector3();
    let sky = 0;
    let lamps = 0;
    let hits = 0;
    const r = Math.min(reach, 0.8);
    for (const [dx, dz] of [[0, 0], [r, 0], [-r, 0], [0, r], [0, -r], [r * 0.7, r * 0.7], [-r * 0.7, -r * 0.7], [r * 0.7, -r * 0.7], [-r * 0.7, r * 0.7]] as const) {
      ray.origin.set(x + dx, y + 0.5, z + dz);
      const hit = bvh.raycastFirst(ray, DoubleSide, 0, 1.5);
      if (!hit?.face) continue;
      const { a: ia, b: ib, c: ic } = hit.face;
      if (uv[ia * 2]! < 0) continue;
      a.fromBufferAttribute(position, ia);
      b.fromBufferAttribute(position, ib);
      c.fromBufferAttribute(position, ic);
      Triangle.getBarycoord(hit.point, a, b, c, weights);
      const u = uv[ia * 2]! * weights.x + uv[ib * 2]! * weights.y + uv[ic * 2]! * weights.z;
      const v = uv[ia * 2 + 1]! * weights.x + uv[ib * 2 + 1]! * weights.y + uv[ic * 2 + 1]! * weights.z;
      sky += sampleLightmap(maps.sky, u, v);
      lamps += sampleLightmap(maps.lamps, u, v);
      hits++;
    }
    if (!hits) return null;
    return {
      sky: MathUtils.clamp(sky / hits / Math.max(maps.usual[0], 1e-6), 0.05, 2.5),
      lamps: MathUtils.clamp(lamps / hits / Math.max(maps.usual[1], 1e-6), 0, 2.5),
    };
  }

  /**
   * How much sun is coming in through each group of windows, against the skylight that came in
   * through it in the bake: a window counts by its size, by how squarely the sun stands to it,
   * and by how much of it the sun reaches past the eaves, the patio's roof and the fences.
   */
  private sunThrough(toSun: Vector3 | null): number {
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
    // How much sun is coming in, all windows together, against the skylight through them.
    const lit = weights.filter((w) => w > 0);
    return lit.length ? lit.reduce((a, b) => a + b, 0) / lit.length : 0;
  }

  /** Call before each frame: the sky stays around the camera; only mirrors close by reflect. */
  update(camera: Camera): void {
    const now = performance.now();
    const dt = this.lastUpdate ? Math.min(0.1, (now - this.lastUpdate) / 1000) : 0;
    this.lastUpdate = now;
    const target = this.cutawayOn ? 1 : 0;
    if (this.cutaway !== target) {
      const was = this.cutaway;
      const step = dt / CUTAWAY.fadeS;
      this.cutaway = target > was ? Math.min(target, was + step) : Math.max(target, was - step);
      this.uniforms.houseCutaway!.value = this.cutaway;
      if ((was > 0) !== (this.cutaway > 0)) this.applyVisibility();
    }
    this.sky.position.copy(camera.position);
    this.backing.position.copy(camera.position);
    for (const mirror of this.mirrors) {
      const shown = this.top === null || mirror.storey === null || mirror.storey <= this.top;
      const near = mirror.center.distanceTo(camera.position) < MIRROR_REACH_M;
      mirror.glass.visible = shown && near;
      mirror.plain.visible = shown && !near;
    }
  }

  /** Mirrors: each flat piece of this part of the model (all the mirrors of a storey come as one), drawn as what is seen in it. */
  private addMirror(geometry: BufferGeometry, color: Color, storey: number | null): void {
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
    for (const corners of pieces.values()) this.addOneMirror(geometry, corners, color, storey);
  }

  private addOneMirror(geometry: BufferGeometry, corners: number[], color: Color, storey: number | null): void {
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
    this.mirrors.push({ glass, plain, center, storey });
  }

  /** `screen`: a wall that fades out of the way from above, drawn where it is not in the way ('solid') or where it is ('faded'). */
  private shade(material: MeshBasicMaterial, sunUniforms: Record<string, IUniform>, kind: string, lid = false, screen: 'solid' | 'faded' | null = null): void {
    let body = HOUSE_BODY.replace('HOUSE_LIGHT', (UNLIT[kind] ?? LIT) + (lid ? BACK_OF_LID : ''));
    if (screen === 'solid') body = `if (houseInTheWay() > 0.0) discard;\n${body}`;
    if (screen === 'faded') body = `float houseWay = houseInTheWay();\nif (houseWay <= 0.0) discard;\n${body}\ndiffuseColor.a *= 1.0 - houseWay * ${(1 - SCREEN_OPACITY).toFixed(3)};`;
    material.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, this.uniforms, sunUniforms);
      shader.vertexShader = shader.vertexShader
        .replace('#include <common>', '#include <common>\nattribute vec2 uv1;\nvarying vec3 vHouseWorld;\nvarying vec3 vHouseNormal;\nvarying vec2 vHouseLight;')
        .replace('#include <project_vertex>', '#include <project_vertex>\nvHouseWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;\nvHouseNormal = mat3(modelMatrix) * normal;\nvHouseLight = uv1;');
      shader.fragmentShader = shader.fragmentShader.replace('#include <common>', `#include <common>\n${HOUSE_PARS}\n${screen ? SCREEN_PARS : ''}\n${SUN_PARS}\n${SUN_AT}`).replace('#include <map_fragment>', `#include <map_fragment>\n${body}`);
    };
    material.customProgramCacheKey = () => `house-${kind in UNLIT ? kind : 'lit'}${lid ? '-lid' : ''}${screen ? `-${screen}` : ''}`;
  }

  dispose(): void {
    this.disposed = true;
    for (const part of this.group.children as Mesh[]) if (part !== this.sky) part.geometry.dispose();
    for (const mirror of this.mirrors) mirror.glass.dispose();
    for (const material of this.materials) material.dispose();
    for (const texture of this.textures) texture.dispose();
    this.sky.geometry.dispose();
    this.sky.material.dispose();
    this.backing.material.dispose();
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
