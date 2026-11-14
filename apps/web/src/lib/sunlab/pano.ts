import type { PropertyTwin, Sweep } from '@ithaca/schema';
import { CubeTexture, CubeTextureLoader, LinearFilter, LinearMipmapLinearFilter, Matrix3, Matrix4, Quaternion, SRGBColorSpace, Vector3, type Material, type WebGLProgramParametersWithUniforms } from 'three';
import { config } from '@/lib/config';

/**
 * Scan photos projected onto the interior mesh (walk mode).
 *
 * At a scan point every pixel is drawn from that point's own 360 photo, looked up along the ray
 * from the eye: `photo = textureCube(pano, inverse(sweep.rotation) * (p - eye))`. Seen from the
 * eye that is exact however coarse the mesh is; the mesh only supplies depth, normals and shadows.
 * While moving between two scan points both projections are blended (Matterport's transition).
 * Faces come from GET /properties/{pid}/sweeps/{sweep}/skybox/{face}?res=4k (4096 px faces);
 * with &defurnished=true, Matterport's furniture-free photos (4k where nothing was painted out).
 */

export const PANO_FACES = ['px', 'nx', 'py', 'ny', 'pz', 'nz'] as const;
export const PANO_RES = '4k';

export interface PanoUniforms {
  panoA: { value: CubeTexture | null };
  panoB: { value: CubeTexture | null };
  eyeA: { value: Vector3 };
  eyeB: { value: Vector3 };
  toPanoA: { value: Matrix3 };
  toPanoB: { value: Matrix3 };
  /** 0: all A, 1: all B. */
  panoMix: { value: number };
  /** 1 projects the photos; 0 draws the mesh's own textures. */
  panoOn: { value: number };
}

export function createPanoUniforms(): PanoUniforms {
  return {
    panoA: { value: null },
    panoB: { value: null },
    eyeA: { value: new Vector3() },
    eyeB: { value: new Vector3() },
    toPanoA: { value: new Matrix3() },
    toPanoB: { value: new Matrix3() },
    panoMix: { value: 0 },
    panoOn: { value: 0 },
  };
}

/** Panoramas need the API (the faces are cut on the server); fixture mode has none. */
export const panosAvailable = (twin: PropertyTwin) => !config.useFixtures && twin.sweeps.some((s) => s.rotation);

export const sweepEye = (s: Sweep) => new Vector3(s.position.x, s.position.y, s.position.z);

/** inverse(rotation) as a mat3: interior-frame directions to the pano frame. */
export function toPano(s: Sweep): Matrix3 {
  const r = s.rotation ?? { x: 0, y: 0, z: 0, w: 1 };
  return new Matrix3().setFromMatrix4(new Matrix4().makeRotationFromQuaternion(new Quaternion(r.x, r.y, r.z, r.w).invert()));
}

export function faceUrls(propertyId: string, sweepId: string, res = PANO_RES, defurnished = false): string[] {
  const query = `?res=${res}${defurnished ? '&defurnished=true' : ''}`;
  return PANO_FACES.map((f) => `${config.apiBase}/properties/${encodeURIComponent(propertyId)}/sweeps/${encodeURIComponent(sweepId)}/skybox/${f}${query}`);
}

/**
 * Cube textures by sweep. A 4k cube with mipmaps is about 540 MB on the GPU, so only two stay
 * resident (the current one and the one being walked to); older ones are disposed. Going back is
 * a re-upload from the browser's HTTP cache, not a download.
 * Furnished and defurnished photos are cached apart, so switching keeps the other set until it ages out.
 */
export class PanoCache {
  private textures = new Map<string, Promise<CubeTexture>>();
  private order: string[] = [];
  private loader = new CubeTextureLoader();
  private prefetched = new Set<string>();
  /** Matterport's furniture-free photos instead of the scan's own. */
  defurnished = false;

  constructor(
    private propertyId: string,
    private anisotropy: number,
    private keep = 2,
  ) {}

  private key(sweepId: string): string {
    return `${this.defurnished ? 'defurnished' : 'furnished'}:${sweepId}`;
  }

  private urls(sweepId: string): string[] {
    return faceUrls(this.propertyId, sweepId, PANO_RES, this.defurnished);
  }

  get(sweepId: string): Promise<CubeTexture> {
    const key = this.key(sweepId);
    let hit = this.textures.get(key);
    if (!hit) {
      hit = new Promise<CubeTexture>((resolve, reject) => {
        const tex = this.loader.load(this.urls(sweepId), () => resolve(tex), undefined, () => reject(new Error(`panorama ${sweepId} did not load`)));
        tex.colorSpace = SRGBColorSpace;
        tex.generateMipmaps = true;
        tex.minFilter = LinearMipmapLinearFilter;
        tex.magFilter = LinearFilter;
        tex.anisotropy = this.anisotropy;
      });
      hit.catch(() => this.textures.delete(key));
      this.textures.set(key, hit);
    }
    this.order = [...this.order.filter((k) => k !== key), key];
    this.evict();
    return hit;
  }

  /** Warm the browser's HTTP cache (the faces are sent with max-age) without using GPU memory. */
  prefetch(sweepId: string): void {
    const key = this.key(sweepId);
    if (this.textures.has(key) || this.prefetched.has(key)) return;
    this.prefetched.add(key);
    for (const url of this.urls(sweepId)) void fetch(url).catch(() => this.prefetched.delete(key));
  }

  private evict(): void {
    while (this.order.length > this.keep) {
      const key = this.order.shift()!;
      const old = this.textures.get(key);
      this.textures.delete(key);
      void old?.then((t) => t.dispose()).catch(() => undefined);
    }
  }

  dispose(): void {
    for (const t of this.textures.values()) void t.then((x) => x.dispose()).catch(() => undefined);
    this.textures.clear();
    this.order = [];
  }
}

/**
 * Teach a lit material (MeshStandardMaterial, already set up by the CSM) to take its surface
 * color from the projected photos when `panoOn` is 1. The photo is both the albedo (so the
 * simulated sun lands on it with its shadows) and the glow (the light of the day of the scan,
 * scaled by the material's emissive intensity, which the sun rig sets from the sky level).
 */
export function addPanoProjection(material: Material, uniforms: PanoUniforms): void {
  const previous = material.onBeforeCompile;
  material.onBeforeCompile = (shader: WebGLProgramParametersWithUniforms, renderer) => {
    previous.call(material, shader, renderer);
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vPanoWorld;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvPanoWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        `#include <common>
uniform samplerCube panoA;
uniform samplerCube panoB;
uniform vec3 eyeA;
uniform vec3 eyeB;
uniform mat3 toPanoA;
uniform mat3 toPanoB;
uniform float panoMix;
uniform float panoOn;
varying vec3 vPanoWorld;`,
      )
      .replace(
        '#include <map_fragment>',
        `#include <map_fragment>
if (panoOn > 0.5) {
  vec3 photoA = textureCube(panoA, toPanoA * (vPanoWorld - eyeA)).rgb;
  vec3 photoB = panoMix > 0.0 ? textureCube(panoB, toPanoB * (vPanoWorld - eyeB)).rgb : photoA;
  diffuseColor.rgb = mix(photoA, photoB, panoMix);
}`,
      )
      .replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\nif (panoOn > 0.5) totalEmissiveRadiance = diffuseColor.rgb * emissive;');
  };
  const key = material.customProgramCacheKey.bind(material);
  material.customProgramCacheKey = () => `${key()}|pano`;
}

/** Unlit backdrop: wherever the mesh has no surface (windows, open doors) the photo still shows. */
export const BACKDROP_VERTEX = /* glsl */ `
varying vec3 vPanoWorld;
void main() {
  vec4 w = modelMatrix * vec4(position, 1.0);
  vPanoWorld = w.xyz;
  gl_Position = projectionMatrix * viewMatrix * w;
}`;

export const BACKDROP_FRAGMENT = /* glsl */ `
uniform samplerCube panoA;
uniform samplerCube panoB;
uniform vec3 eyeA;
uniform vec3 eyeB;
uniform mat3 toPanoA;
uniform mat3 toPanoB;
uniform float panoMix;
uniform float skyLevel;
varying vec3 vPanoWorld;
void main() {
  vec3 a = textureCube(panoA, toPanoA * (vPanoWorld - eyeA)).rgb;
  vec3 b = panoMix > 0.0 ? textureCube(panoB, toPanoB * (vPanoWorld - eyeB)).rgb : a;
  gl_FragColor = vec4(mix(a, b, panoMix) * skyLevel, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;
