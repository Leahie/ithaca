import type { Opening } from '@ithaca/schema';
import { MeshDepthMaterial, Vector3, Vector4 } from 'three';

/**
 * Sunlight through openings.
 *
 * A scanned interior mesh usually has solid geometry where the windows are, so a shadow map
 * would block the sun at every window. Each glazed opening becomes a "portal": a box around
 * its polygon inside which shadow-casting fragments are discarded. The wall still renders;
 * it just stops casting a shadow there, so light enters exactly where the openings are.
 */
export const MAX_PORTALS = 64;
/** Half the depth of the cut through the wall, in meters. */
const HALF_THICKNESS = 0.45;

export interface Portal {
  center: Vector3;
  u: Vector3; // unit, along the width
  v: Vector3; // unit, along the height
  halfWidth: number;
  halfHeight: number;
}

/** Windows and sliders pass light. A door passes light only if it is glazed. */
export function passesLight(o: Opening): boolean {
  return o.kind === 'window' || o.kind === 'slider' || o.glazing !== 'none';
}

export function portalFromOpening(o: Opening): Portal | null {
  if (o.polygon.length < 4) return null;
  const [p0, p1, , p3] = o.polygon.map((p) => new Vector3(p.x, p.y, p.z)) as [Vector3, Vector3, Vector3, Vector3];
  const center = o.polygon.reduce<Vector3>((acc, p) => acc.add(new Vector3(p.x, p.y, p.z)), new Vector3()).multiplyScalar(1 / o.polygon.length);
  const du = p1.clone().sub(p0);
  const dv = p3.clone().sub(p0);
  const halfWidth = du.length() / 2;
  const halfHeight = dv.length() / 2;
  if (halfWidth < 1e-3 || halfHeight < 1e-3) return null;
  return { center, u: du.normalize(), v: dv.normalize(), halfWidth, halfHeight };
}

export interface PortalUniforms {
  uPortalCount: { value: number };
  uPortalC: { value: Vector4[] };
  uPortalU: { value: Vector4[] };
  uPortalV: { value: Vector4[] };
}

export function createPortalUniforms(): PortalUniforms {
  const fill = () => Array.from({ length: MAX_PORTALS }, () => new Vector4());
  return { uPortalCount: { value: 0 }, uPortalC: { value: fill() }, uPortalU: { value: fill() }, uPortalV: { value: fill() } };
}

export function setPortals(uniforms: PortalUniforms, openings: Opening[]): number {
  let n = 0;
  for (const o of openings) {
    if (n >= MAX_PORTALS) break;
    if (!passesLight(o)) continue;
    const p = portalFromOpening(o);
    if (!p) continue;
    uniforms.uPortalC.value[n]!.set(p.center.x, p.center.y, p.center.z, HALF_THICKNESS);
    uniforms.uPortalU.value[n]!.set(p.u.x, p.u.y, p.u.z, p.halfWidth);
    uniforms.uPortalV.value[n]!.set(p.v.x, p.v.y, p.v.z, p.halfHeight);
    n++;
  }
  uniforms.uPortalCount.value = n;
  return n;
}

/** Depth material for the shadow pass that leaves holes at the portals. */
export function createPortalDepthMaterial(uniforms: PortalUniforms): MeshDepthMaterial {
  const material = new MeshDepthMaterial();
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vPortalWorld;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvPortalWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        `#include <common>
varying vec3 vPortalWorld;
uniform int uPortalCount;
uniform vec4 uPortalC[${MAX_PORTALS}];
uniform vec4 uPortalU[${MAX_PORTALS}];
uniform vec4 uPortalV[${MAX_PORTALS}];`,
      )
      .replace(
        'void main() {',
        `void main() {
  for (int i = 0; i < ${MAX_PORTALS}; i++) {
    if (i >= uPortalCount) break;
    vec3 d = vPortalWorld - uPortalC[i].xyz;
    vec3 n = cross(uPortalU[i].xyz, uPortalV[i].xyz);
    if (abs(dot(d, uPortalU[i].xyz)) < uPortalU[i].w && abs(dot(d, uPortalV[i].xyz)) < uPortalV[i].w && abs(dot(d, n)) < uPortalC[i].w) discard;
  }`,
      );
  };
  material.customProgramCacheKey = () => 'ithaca-portal-depth';
  return material;
}
