/**
 * Minimal glTF 2.0 binary writer for fixture assets (I-G15: meters, Y up).
 * One mesh, one primitive per part, positions + normals + uint32 indices, flat color per part.
 */
export interface Part {
  positions: number[]; // xyz triples
  indices: number[];
  color: [number, number, number];
  roughness?: number;
  doubleSided?: boolean;
}

function flatNormals(positions: number[], indices: number[]): { positions: number[]; normals: number[]; indices: number[] } {
  const outP: number[] = [];
  const outN: number[] = [];
  const outI: number[] = [];
  for (let i = 0; i < indices.length; i += 3) {
    const tri = [indices[i]!, indices[i + 1]!, indices[i + 2]!];
    const p = tri.map((v) => [positions[v * 3]!, positions[v * 3 + 1]!, positions[v * 3 + 2]!] as const);
    const ux = p[1]![0] - p[0]![0];
    const uy = p[1]![1] - p[0]![1];
    const uz = p[1]![2] - p[0]![2];
    const vx = p[2]![0] - p[0]![0];
    const vy = p[2]![1] - p[0]![1];
    const vz = p[2]![2] - p[0]![2];
    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    const l = Math.hypot(nx, ny, nz) || 1;
    nx /= l;
    ny /= l;
    nz /= l;
    for (const q of p) {
      outI.push(outP.length / 3);
      outP.push(q[0], q[1], q[2]);
      outN.push(nx, ny, nz);
    }
  }
  return { positions: outP, normals: outN, indices: outI };
}

/** Area-weighted smooth normals; keeps the vertex count for large scanned meshes. */
function smoothNormals(positions: number[], indices: number[]): number[] {
  const n = new Float64Array(positions.length);
  for (let i = 0; i < indices.length; i += 3) {
    const a = indices[i]! * 3;
    const b = indices[i + 1]! * 3;
    const c = indices[i + 2]! * 3;
    const ux = positions[b]! - positions[a]!;
    const uy = positions[b + 1]! - positions[a + 1]!;
    const uz = positions[b + 2]! - positions[a + 2]!;
    const vx = positions[c]! - positions[a]!;
    const vy = positions[c + 1]! - positions[a + 1]!;
    const vz = positions[c + 2]! - positions[a + 2]!;
    const nx = uy * vz - uz * vy;
    const ny = uz * vx - ux * vz;
    const nz = ux * vy - uy * vx;
    for (const v of [a, b, c]) {
      n[v] = n[v]! + nx;
      n[v + 1] = n[v + 1]! + ny;
      n[v + 2] = n[v + 2]! + nz;
    }
  }
  const out: number[] = new Array(positions.length);
  for (let i = 0; i < n.length; i += 3) {
    const l = Math.hypot(n[i]!, n[i + 1]!, n[i + 2]!) || 1;
    out[i] = n[i]! / l;
    out[i + 1] = n[i + 1]! / l;
    out[i + 2] = n[i + 2]! / l;
  }
  return out;
}

export function writeGlb(parts: Part[], opts: { smooth?: boolean; generator?: string } = {}): Buffer {
  const chunks: Buffer[] = [];
  let offset = 0;
  const bufferViews: object[] = [];
  const accessors: object[] = [];
  const materials: object[] = [];
  const primitives: object[] = [];

  const push = (data: Float32Array | Uint32Array, target: number): number => {
    const buf = Buffer.from(data.buffer, data.byteOffset, data.byteLength);
    bufferViews.push({ buffer: 0, byteOffset: offset, byteLength: buf.length, target });
    chunks.push(buf);
    offset += buf.length; // Float32 / Uint32 keep 4-byte alignment
    return bufferViews.length - 1;
  };

  for (const part of parts) {
    const geo = opts.smooth
      ? { positions: part.positions, normals: smoothNormals(part.positions, part.indices), indices: part.indices }
      : flatNormals(part.positions, part.indices);
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < geo.positions.length; i++) {
      const k = i % 3;
      min[k] = Math.min(min[k]!, geo.positions[i]!);
      max[k] = Math.max(max[k]!, geo.positions[i]!);
    }
    // Round-trip through Float32 so min/max match the stored values.
    const pos32 = new Float32Array(geo.positions);
    for (let k = 0; k < 3; k++) {
      min[k] = Math.fround(min[k]!);
      max[k] = Math.fround(max[k]!);
    }
    const count = geo.positions.length / 3;
    const pv = push(pos32, 34962);
    accessors.push({ bufferView: pv, componentType: 5126, count, type: 'VEC3', min, max });
    const nv = push(new Float32Array(geo.normals), 34962);
    accessors.push({ bufferView: nv, componentType: 5126, count, type: 'VEC3' });
    const iv = push(new Uint32Array(geo.indices), 34963);
    accessors.push({ bufferView: iv, componentType: 5125, count: geo.indices.length, type: 'SCALAR' });
    materials.push({
      pbrMetallicRoughness: { baseColorFactor: [...part.color, 1], metallicFactor: 0, roughnessFactor: part.roughness ?? 0.85 },
      doubleSided: part.doubleSided ?? false,
    });
    primitives.push({
      attributes: { POSITION: accessors.length - 3, NORMAL: accessors.length - 2 },
      indices: accessors.length - 1,
      material: materials.length - 1,
    });
  }

  const json = {
    asset: { version: '2.0', generator: opts.generator ?? 'ithaca fixture builder' },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [{ primitives }],
    materials,
    accessors,
    bufferViews,
    buffers: [{ byteLength: offset }],
  };
  let jsonBuf = Buffer.from(JSON.stringify(json), 'utf8');
  if (jsonBuf.length % 4) jsonBuf = Buffer.concat([jsonBuf, Buffer.alloc(4 - (jsonBuf.length % 4), 0x20)]);
  const bin = Buffer.concat(chunks);
  const header = Buffer.alloc(12);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + jsonBuf.length + 8 + bin.length, 8);
  const jsonHead = Buffer.alloc(8);
  jsonHead.writeUInt32LE(jsonBuf.length, 0);
  jsonHead.writeUInt32LE(0x4e4f534a, 4);
  const binHead = Buffer.alloc(8);
  binHead.writeUInt32LE(bin.length, 0);
  binHead.writeUInt32LE(0x004e4942, 4);
  return Buffer.concat([header, jsonHead, jsonBuf, binHead, bin]);
}

/** Axis-aligned box. `min` / `max` in item space. Outward-facing triangles. */
export function box(min: [number, number, number], max: [number, number, number], color: [number, number, number], roughness?: number): Part {
  const [x0, y0, z0] = min;
  const [x1, y1, z1] = max;
  const positions = [x0, y0, z0, x1, y0, z0, x1, y1, z0, x0, y1, z0, x0, y0, z1, x1, y0, z1, x1, y1, z1, x0, y1, z1];
  const indices = [
    0, 2, 1, 0, 3, 2, // -Z
    4, 5, 6, 4, 6, 7, // +Z
    0, 1, 5, 0, 5, 4, // -Y
    3, 7, 6, 3, 6, 2, // +Y
    0, 4, 7, 0, 7, 3, // -X
    1, 2, 6, 1, 6, 5, // +X
  ];
  return { positions, indices, color, roughness };
}
