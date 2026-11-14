# Stream C → Stream A: using the house twin

Everything Stream A (web) needs to use the real twin of `onXQLXhfyQv` (9414 Endicott Lane, Houston)
from branch `stream-c/twin-v0`. Questions go to the Stream C owner.

## 1. What exists today

| File | What it is |
|---|---|
| `fixtures/twin/onXQLXhfyQv.json` | The `PropertyTwin`: floors, rooms, windows/doors, sweeps, world transform. Built from real Matterport data. |
| `fixtures/blobs/onXQLXhfyQv/interior.glb` | The textured interior mesh, 17.9 MB, Git LFS. Run `git lfs pull` after checkout. |
| `packages/schema/twin.py` | The Pydantic source of truth for the twin's shape (I-G5). |

For this house: 2 floors, 24 rooms, 96 sweeps (84 inside a room, 12 outdoor scans in the yard),
18 windows, 34 doors. The house grid is turned 6.55° from true north, so its walls face 83°, 173°,
263° and 353°. CoStar lists the house as east-facing, which matches the widest exterior door
(83°).

REST is live: twin, `PUT /openings`, `SunReport`, sun and daylight moments, and asset serving.
See `docs/handoffs/stream-a/2026-11-14-from-c-rest-endpoints.md` (run with
`uvicorn services.api.twin.app:app --port 8000` until B mounts the router). Not ready yet:
`context_glb` (neighbors and trees for shadows), `ortho` and `obliques`, `NoiseReport` and
`AreaProfile` (both return 503 "not configured").

## 2. Frames and units

All of this follows WORKSTREAMS.md section 1.3; the points below are the ones that bite.

- **Interior frame = Showcase SDK frame:** right-handed, **Y up**, meters (I-G9). Room polygons,
  opening corners, sweep positions and the GLB all use it. Place things in Showcase and in the Sun
  Lab with the same numbers and no per-view offset (A5).
- **Plan view (from above):** interior **−Z is "up the page"**. In SVG, whose y axis points down,
  draw `(x, z)` as is and the plan comes out the right way round (north is roughly up for this house).
- **Room polygons** are `(x, z)` pairs, counter-clockwise seen from above, not closed (the last point
  does not repeat the first).
- **`interior_to_world`** is a 4×4 matrix, **column-major**, so `new THREE.Matrix4().fromArray(...)`
  takes it as is. World = local ENU at `(twin.lat, twin.lon)`: X east, Y north, Z up (I-G10). Only
  sun, DSM and noise code needs it.
- **Compass angles** (`facade_azimuth_deg`, sun azimuth) are degrees clockwise from true north
  (I-G11). A facade azimuth is the direction the opening's outward normal points.
- **Datetimes** are ISO 8601 with an offset, in `twin.timezone` (`America/Chicago`) (I-G13).

## 3. The twin, field by field

Real excerpts from `fixtures/twin/onXQLXhfyQv.json`:

```jsonc
// floors: index 0 is the ground floor; elevation_m is interior y of the floor surface
{ "floor_id": "tsmq1wak12rhgn0mawksxcwcd", "index": 0, "elevation_m": -0.129, "label": "Floor 1" }

// rooms
{ "room_id": "19ab05tns5h6y4qm42esqqpea", "floor_id": "tsmq1wak12rhgn0mawksxcwcd",
  "label": "Hallway", "polygon": [[-1.399, -15.197], [1.11, -15.197], [1.11, -16.157], ...],
  "floor_y": 0.008, "height_m": 2.43, "floor_material": "hardwood", "area_m2": 3.44 }

// openings: polygon corners are bottom-start, bottom-end, top-end, top-start (interior frame)
{ "opening_id": "12nmz756ixbszas0mesd8n1hb", "room_id": "n2yqr30509t7i8whfdf2htwic",
  "kind": "window", "polygon": [{"x": -10.707, "y": 3.932, "z": -9.475}, ...],
  "facade_azimuth_deg": 263.45, "glazing": "double", "stc": null, "other_room_id": null }

// an interior door: other_room_id is the room on the far side
{ "opening_id": "082w4191t9d50cq0ktx90qi7c", "room_id": "4mpd3yxnm0wrnpak1ibnr29wc",
  "kind": "door", "facade_azimuth_deg": 173.45, "glazing": "none",
  "other_room_id": "b2kez6wsw32fm2x3ui593u3hc", ... }

// sweeps: sweep_id is the Matterport pano id
{ "sweep_id": "0t5k740rf7yw1gak07xk6ri2d", "floor_id": "tsmq1wak12rhgn0mawksxcwcd",
  "room_id": "h26t7ky77ew74yb0pg8qqf5rd", "position": {"x": -2.302, "y": 1.446, "z": -14.624},
  "label": "63" }

"assets":  { "interior_glb": "blobs/onXQLXhfyQv/interior.glb", "context_glb": null, "ortho": null, "obliques": [] }
"quality": { "alignment": "georef", "openings": "detected", "context": "none", "traffic": "none" }
```

Things to know:

- **Room labels repeat:** there are 6 bedrooms, 5 bathrooms, 3 "Living Room"s, 2 kitchens, 2 laundries
  and 2 closets. Show them with something that tells them apart, e.g. "Bedroom · Floor 2 · 20.6 m²".
  Always key on `room_id`, never on the label.
- **Floor 2's 85 m² "Living Room"** is the landing and loft over the stairs.
- **`floor_y` vs `elevation_m`:** use `room.floor_y` to put furniture on a room's floor. It can differ
  from its floor's `elevation_m` by a few centimeters (sunken rooms).
- **Sweeps outside rooms** (`room_id: null`) are yard scans. Don't offer them as room destinations.
- **Openings come from Matterport's own wall annotations** (`quality.openings = "detected"`). The
  annotation UI can start from these and let the user fix or add, rather than starting empty.
  Window glazing is assumed `"double"` until someone annotates it.
- **`quality`** tells the UI and the agent how much to trust things. `alignment: "georef"` means the
  world placement comes from Matterport's georeference (checked to within 0.5 m); `"icp"` will mean
  it was refined against Vexcel's aerial building outline.
- **Asset fields are URLs from the API, keys in the file.** `GET /api/properties/{pid}/twin` returns
  full URLs (`http://.../api/assets/blobs/onXQLXhfyQv/interior.glb`). The raw JSON file holds keys
  relative to `fixtures/`; only in fixture mode, serve `fixtures/` statically and prefix `/fixtures/`.

### Differences from WORKSTREAMS.md section 2.1

All additive except the second point:

- `quality.alignment` has a new value, `"georef"`.
- `assets.interior_glb` is `string | null` (it is set for this house).
- New optional fields: `Floor.label`, `Room.area_m2`, `Opening.other_room_id`, `Sweep.label`.
- `Vec3` is `{x, y, z}` and lives in `twin.py` for now.

### TypeScript types

Generate them from the schema instead of hand-writing them (I-G5). Until B's codegen exists, the JSON
Schema comes from:

```bash
python -c "import json; from packages.schema.twin import PropertyTwin; print(json.dumps(PropertyTwin.model_json_schema(), indent=2))" > property_twin.schema.json
```

## 4. Loading the interior GLB

The GLB is meshopt-compressed (`EXT_meshopt_compression` + `KHR_mesh_quantization`). It passes the
Khronos glTF validator with 0 errors and 0 warnings. It is already in the interior frame (meters,
Y up), so add it to the scene with **no transform**.

```tsx
// React Three Fiber + drei: useGLTF decodes meshopt by default
import { useGLTF } from "@react-three/drei";

function Interior({ url }: { url: string }) {
  const { scene } = useGLTF(url); // e.g. "/fixtures/blobs/onXQLXhfyQv/interior.glb"
  return <primitive object={scene} />;
}
```

```ts
// plain three.js
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";

const loader = new GLTFLoader();
loader.setMeshoptDecoder(MeshoptDecoder);
```

About the mesh:

- 434k triangles, 140 materials (one per texture atlas), textures at most 1024 px.
- It includes some outdoor geometry from the yard scans (trees, patio). Clip by height or bounding box
  if it gets in the way.
- Walls are a few large flat triangles each, which is normal for Matterport meshes.

## 5. Recipes

### Sun direction in the Sun Lab (A3)

suncalc measures azimuth from **south** toward west, so add π to get a compass bearing. Then go
from ENU to the interior frame with the inverse of `interior_to_world`.

```ts
import * as THREE from "three";
import SunCalc from "suncalc";

const interiorToWorld = new THREE.Matrix4().fromArray(twin.interior_to_world);
const worldToInterior = interiorToWorld.clone().invert();

/** Unit vector from the scene toward the sun, in the interior frame. */
export function sunDirectionInterior(date: Date): THREE.Vector3 {
  const { azimuth, altitude } = SunCalc.getPosition(date, twin.lat, twin.lon);
  const bearing = azimuth + Math.PI; // clockwise from true north
  const enu = new THREE.Vector3(
    Math.cos(altitude) * Math.sin(bearing), // east
    Math.cos(altitude) * Math.cos(bearing), // north
    Math.sin(altitude),                     // up
  );
  return enu.transformDirection(worldToInterior);
}

// directional light: light.position.copy(target).addScaledVector(sunDirectionInterior(d), 50)
```

The result must match C's pvlib reference within 0.5° (A3). C will ship
`fixtures/sun/reference.json` with the sun engine.

### Drawing a floor plan

```tsx
const rooms = twin.rooms.filter((r) => r.floor_id === floorId);
<svg viewBox={...}>
  {rooms.map((r) => (
    <polygon key={r.room_id} points={r.polygon.map(([x, z]) => `${x},${z}`).join(" ")} />
  ))}
</svg>
```

An opening's footprint on the plan is the segment from `polygon[0]` to `polygon[1]`, drawn as `(x, z)`.
`out/twin/*.png` are reference renders. To regenerate them:
`python -m services.api.twin.ingest onXQLXhfyQv --plot`.

### Going to a room (`viewer.goto {room_id}`)

Pick a sweep in that room, then move Showcase there:

```ts
function sweepForRoom(roomId: string) {
  const room = twin.rooms.find((r) => r.room_id === roomId)!;
  const inRoom = twin.sweeps.filter((s) => s.room_id === roomId);
  const [cx, cz] = centroid(room.polygon);
  // the sweep closest to the room's middle gives the best first view
  return inRoom.sort((a, b) => dist(a, cx, cz) - dist(b, cx, cz))[0]?.sweep_id;
}
await mpSdk.Sweep.moveTo(sweepForRoom(roomId), { transition: mpSdk.Sweep.Transition.FLY });
```

## 6. What C needs from A

1. **Frame check (C6), about 5 minutes once Showcase is embedded:** log `mpSdk.Sweep.data` for this
   model and compare a few sweeps with `twin.sweeps`. Expected: the Showcase `sid` equals `sweep_id`,
   and the Showcase `position` equals the twin's `position` to within a centimeter. If they differ,
   tell C before placing any furniture. Everything in I-G9 depends on this.
2. **Tell C if the meshopt GLB causes trouble** in any loader you use. C can ship an uncompressed
   GLB with smaller textures instead.
3. **Annotation UI:** the openings already exist (section 3). Save edits with
   `PUT /api/properties/{pid}/openings` and the full `Opening[]` (same shape as `twin.openings`);
   validation rules are in the REST endpoints handoff note.

## 7. Rebuilding (C does this; for reference)

```bash
pip install -r services/api/twin/requirements.txt
python -m services.api.twin.ingest onXQLXhfyQv          # twin JSON from saved responses, no keys needed
python -m services.api.twin.mesh onXQLXhfyQv            # interior.glb (needs Matterport keys)
python -m pytest -q
```
