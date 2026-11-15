'use client';

import { useFrame, useThree } from '@react-three/fiber';
import { useEffect, useMemo } from 'react';
import { Color, HalfFloatType, WebGLRenderTarget } from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { GTAOPass } from 'three/examples/jsm/postprocessing/GTAOPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { useHouseLighting } from './House3D';

/**
 * Shadow in the corners (docs/specs/lighting.md, 8c and 8d): where two walls, a wall and the
 * floor or the ceiling meet, under cabinets and around furniture, less of the room's light gets
 * in. The bake that made the lightmaps cannot see that, so it is worked out each frame from the
 * screen (three's GTAO, at half resolution, reaching AO_RADIUS_M from an edge).
 *
 * The shadow is not black: it leans a deep blue-grey, more so as the sun gets low and at night,
 * so a corner far from the window reads cool while the edge of a sun patch or a lamp's pool stays
 * warm (the light shift inside one room).
 *
 * Only indoors (`enabled`): outside, the haze already sets things apart. When off, the scene is
 * drawn as before, straight to the screen.
 */

/** What a fully shaded corner is multiplied by: dark, and cooler than it is dark. */
const CORNER = new Color(0.1, 0.12, 0.18);
/**
 * How far from an edge the shadow reaches (m). Short enough that a door or a skirting board
 * throws a crisp contact shadow, not a smudge (the bake has the broad soft shading already); long
 * enough that a sofa or a chair, which the bake never saw, sits in its own shadow on the floor.
 */
const AO_RADIUS_M = 0.3;
/** The AO is worked out at this fraction of the screen's resolution. */
const AO_SCALE = 0.5;

// GTAOBlendShader with a colour: the picture is multiplied by `tint` where occluded, by white where open.
const TINTED_BLEND = /* glsl */ `
  uniform float intensity;
  uniform vec3 tint;
  uniform sampler2D tDiffuse;
  varying vec2 vUv;
  void main() {
    vec4 texel = texture2D(tDiffuse, vUv);
    vec3 shade = mix(tint, vec3(1.0), texel.r);
    gl_FragColor = vec4(mix(vec3(1.0), shade, intensity), texel.a);
  }`;

export function HouseAO({ enabled }: { enabled: boolean }) {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const dpr = useThree((s) => s.viewport.dpr);
  const lighting = useHouseLighting();

  const chain = useMemo(() => {
    // The canvas's own anti-aliasing is lost when drawing into a target: the target has its own.
    const target = new WebGLRenderTarget(1, 1, { type: HalfFloatType, samples: 4 });
    const composer = new EffectComposer(gl, target);
    composer.addPass(new RenderPass(scene, camera));
    const ao = new GTAOPass(scene, camera, 1, 1);
    ao.updateGtaoMaterial({ radius: AO_RADIUS_M, distanceFallOff: 1, thickness: 0.5, scale: 1.5, samples: 12, screenSpaceRadius: false });
    ao.blendMaterial.fragmentShader = TINTED_BLEND;
    ao.blendMaterial.uniforms.tint = { value: CORNER.clone() };
    ao.blendMaterial.needsUpdate = true;
    const setSize = ao.setSize.bind(ao);
    ao.setSize = (w: number, h: number) => setSize(Math.max(1, Math.round(w * AO_SCALE)), Math.max(1, Math.round(h * AO_SCALE)));
    composer.addPass(ao);
    composer.addPass(new OutputPass());
    return { composer, ao };
  }, [gl, scene, camera]);

  useEffect(() => {
    chain.composer.setPixelRatio(dpr);
    chain.composer.setSize(size.width, size.height);
  }, [chain, size.width, size.height, dpr]);

  useEffect(
    () => () => {
      chain.composer.dispose();
      chain.ao.dispose();
    },
    [chain],
  );

  // Priority 1: this draws the frame (R3F stops drawing it itself).
  useFrame((_, delta) => {
    if (!enabled) {
      gl.render(scene, camera);
      return;
    }
    const u = lighting.sun.uniforms;
    const golden = (u.sunGolden!.value as number) ?? 0;
    const night = (u.sunNight!.value as number) ?? 0;
    const work = (u.sunWork?.value as number) ?? 0; // working-hours lights: corners a little lighter, furniture still grounded
    chain.ao.blendIntensity = Math.min(1, 0.55 + 0.35 * golden + 0.25 * night) * (1 - 0.15 * work);
    chain.composer.render(delta);
  }, 1);

  return null;
}
