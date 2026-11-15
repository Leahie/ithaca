// Surface patterns for the rebuilt house: floor boards, tiles, wood grain, stone, lawn
// (ported from 3d_rendering_matterport/src/patterns.ts).
//
// Each is a square picture that repeats without a seam, drawn here on a canvas. A pattern only
// varies a surface's color (it is multiplied in, and averages a little under white), so the
// color itself stays the one the house's light was worked out with (pipeline/house/materials.mjs).

import * as THREE from 'three';

const SIZE = 1024;

/** A repeatable random number source. */
function random(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

const grey = (v: number, tint: [number, number, number] = [1, 1, 1]) => `rgb(${tint.map((t) => Math.round(Math.max(0, Math.min(1, v * t)) * 255)).join(',')})`;

/** Fine streaks along x (wood grain), wrapping at the edges. */
function grain(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, rnd: () => number, strength: number): void {
  for (let i = 0; i < (w * h) / 900; i++) {
    const [sx, sy, len] = [x + rnd() * w, y + rnd() * h, 20 + rnd() * 160];
    g.strokeStyle = rnd() < 0.5 ? `rgba(0,0,0,${strength * rnd()})` : `rgba(255,255,255,${strength * 0.7 * rnd()})`;
    g.lineWidth = 0.6 + rnd() * 1.2;
    g.beginPath();
    g.moveTo(sx, sy);
    g.bezierCurveTo(sx + len / 3, sy + (rnd() - 0.5) * 3, sx + (2 * len) / 3, sy + (rnd() - 0.5) * 3, Math.min(x + w, sx + len), sy + (rnd() - 0.5) * 2);
    g.stroke();
  }
}

/** Soft blotches, wrapping at the edges (for stone, plaster, grass). */
function mottle(g: CanvasRenderingContext2D, rnd: () => number, count: number, radius: number, strength: number): void {
  for (let i = 0; i < count; i++) {
    const [cx, cy, r] = [rnd() * SIZE, rnd() * SIZE, radius * (0.4 + rnd())];
    const dark = rnd() < 0.5;
    for (const [ox, oy] of [[0, 0], [SIZE, 0], [-SIZE, 0], [0, SIZE], [0, -SIZE]] as const) {
      if (cx + ox < -r || cx + ox > SIZE + r || cy + oy < -r || cy + oy > SIZE + r) continue;
      const fill = g.createRadialGradient(cx + ox, cy + oy, 0, cx + ox, cy + oy, r);
      fill.addColorStop(0, dark ? `rgba(0,0,0,${strength})` : `rgba(255,255,255,${strength})`);
      fill.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = fill;
      g.fillRect(cx + ox - r, cy + oy - r, 2 * r, 2 * r);
    }
  }
}

const DRAW: Record<string, (g: CanvasRenderingContext2D, rnd: () => number) => void> = {
  // Floor boards along x: twelve rows to a copy, boards of different lengths and shades.
  wood(g, rnd) {
    const rows = 12;
    const h = SIZE / rows;
    for (let r = 0; r < rows; r++) {
      let x = -rnd() * SIZE * 0.5;
      while (x < SIZE) {
        const w = SIZE * (0.35 + rnd() * 0.55);
        const shade = 0.9 + rnd() * 0.1;
        const tint: [number, number, number] = [1, 0.99 + rnd() * 0.01, 0.965 + rnd() * 0.03];
        for (const ox of [0, SIZE]) {
          g.fillStyle = grey(shade, tint);
          g.fillRect(x + ox, r * h, w, h);
        }
        grain(g, Math.max(0, x), r * h, Math.min(w, SIZE - Math.max(0, x)), h, rnd, 0.05);
        // The joint at the board's end.
        g.fillStyle = 'rgba(60,45,30,0.35)';
        g.fillRect(((x + w) % SIZE) - 1, r * h, 1.6, h);
        x += w;
      }
      g.fillStyle = 'rgba(60,45,30,0.3)';
      g.fillRect(0, r * h - 0.8, SIZE, 1.6);
    }
  },
  // Grain without boards: cabinet fronts, treads, rails.
  oak(g, rnd) {
    g.fillStyle = grey(0.95);
    g.fillRect(0, 0, SIZE, SIZE);
    mottle(g, rnd, 40, 220, 0.035);
    grain(g, 0, 0, SIZE, SIZE, rnd, 0.07);
  },
  // Large tiles, two across and one down a copy, with fine joints.
  tile(g, rnd) {
    for (let k = 0; k < 2; k++) {
      g.fillStyle = grey(0.94 + rnd() * 0.04);
      g.fillRect((k * SIZE) / 2, 0, SIZE / 2, SIZE);
    }
    mottle(g, rnd, 60, 260, 0.03);
    g.fillStyle = 'rgba(90,88,84,0.55)';
    for (let k = 0; k < 2; k++) g.fillRect((k * SIZE) / 2 - 1.5, 0, 3, SIZE);
    g.fillRect(0, -1.5, SIZE, 3);
    g.fillRect(0, SIZE - 1.5, SIZE, 3);
  },
  // Pale stone with a few soft veins.
  marble(g, rnd) {
    g.fillStyle = grey(0.96);
    g.fillRect(0, 0, SIZE, SIZE);
    mottle(g, rnd, 50, 300, 0.035);
    for (let i = 0; i < 9; i++) {
      let [x, y] = [rnd() * SIZE, rnd() * SIZE];
      g.strokeStyle = `rgba(120,112,100,${0.05 + rnd() * 0.08})`;
      g.lineWidth = 1 + rnd() * 2.5;
      g.beginPath();
      g.moveTo(x, y);
      for (let k = 0; k < 9; k++) {
        x += 40 + rnd() * 90;
        y += (rnd() - 0.45) * 90;
        g.lineTo(x, y);
      }
      g.stroke();
    }
  },
  // Plaster: a fine grit.
  stucco(g, rnd) {
    g.fillStyle = grey(0.96);
    g.fillRect(0, 0, SIZE, SIZE);
    mottle(g, rnd, 2600, 9, 0.05);
    mottle(g, rnd, 30, 300, 0.025);
  },
  // Square paving stones, four to a copy each way.
  paving(g, rnd) {
    const n = 4;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        g.fillStyle = grey(0.9 + rnd() * 0.08, [1, 1, 1.01]);
        g.fillRect((i * SIZE) / n, (j * SIZE) / n, SIZE / n, SIZE / n);
      }
    }
    mottle(g, rnd, 900, 14, 0.05);
    g.fillStyle = 'rgba(70,70,70,0.45)';
    for (let i = 0; i <= n; i++) {
      g.fillRect((i * SIZE) / n - 2, 0, 4, SIZE);
      g.fillRect(0, (i * SIZE) / n - 2, SIZE, 4);
    }
  },
  // A mown lawn: mottled, with short blades.
  grass(g, rnd) {
    g.fillStyle = grey(0.9);
    g.fillRect(0, 0, SIZE, SIZE);
    mottle(g, rnd, 220, 110, 0.07);
    for (let i = 0; i < 9000; i++) {
      const [x, y] = [rnd() * SIZE, rnd() * SIZE];
      g.strokeStyle = rnd() < 0.5 ? `rgba(20,40,10,${0.1 + rnd() * 0.12})` : `rgba(230,255,190,${0.08 + rnd() * 0.1})`;
      g.lineWidth = 1;
      g.beginPath();
      g.moveTo(x, y);
      g.lineTo(x + (rnd() - 0.5) * 5, y - 3 - rnd() * 6);
      g.stroke();
    }
  },
  // Upright fence boards.
  fence(g, rnd) {
    const n = 14;
    for (let i = 0; i < n; i++) {
      g.fillStyle = grey(0.84 + rnd() * 0.14);
      g.fillRect((i * SIZE) / n, 0, SIZE / n, SIZE);
      g.fillStyle = 'rgba(30,20,10,0.5)';
      g.fillRect((i * SIZE) / n - 1.2, 0, 2.4, SIZE);
    }
    g.save();
    g.translate(SIZE, 0);
    g.rotate(Math.PI / 2);
    grain(g, 0, 0, SIZE, SIZE, rnd, 0.08);
    g.restore();
  },
};

/** A pattern by name, or null if there is none of that name. */
export function makePattern(name: string): THREE.Texture | null {
  const draw = DRAW[name];
  if (!draw) return null;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = SIZE;
  const g = canvas.getContext('2d');
  if (!g) return null;
  let seed = 7;
  for (const c of name) seed = Math.imul(seed, 31) + c.charCodeAt(0);
  draw(g, random(seed));
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  return texture;
}
