---
from: C
to: A
date: 2026-11-14
status: open
source: stream-c/twin-v0 (twin version 6)
---
# Sun Lab: walk between scan points, so the camera never goes through a wall

**Why:** In the Sun Lab the camera orbits freely (`CameraRig` in `sunlab/Scene.tsx`), so it drifts
through walls and ends up inside them. Matterport never does: the camera only stands on scan
positions and moves along straight, clear links between them. The twin now carries those links.

## What changed in the twin
- `Sweep.neighbors: string[]`: the sweeps reachable from this one along a clear straight line.
  Symmetric, and every sweep is reachable (96 sweeps, 211 links, 1 to 10 per sweep, median 4).
- Built by ray casting against the interior mesh (`services/api/twin/nav.py`): the eye-height
  line between the two scan positions is clear, at least one shoulder (15 cm either side) is
  clear, and on level ground a knee-height line is clear too (so no links over counters or beds,
  or through glass above a sill). Stairs link across floors.
- `Sweep.position` is already the camera position (about 1.5 m above the floor), in the interior frame.
- The fixture `fixtures/twin/onXQLXhfyQv.json` is regenerated (version 6). Copy it over, or call
  `GET /api/properties/{pid}/twin`.

## What you need to do
- [ ] **Room view = walk mode.** On picking a room, stand on that room's sweep nearest the room
      centroid (`sweeps.filter(s => s.room_id === room.room_id)`). Only rooms with no sweep keep
      today's `roomCamera()`.
- [ ] **Look in place.** Rotate around the camera, not around a target in front of it. With drei
      `CameraControls`: put the target 1 mm in front of the eye, set `minDistance = maxDistance`,
      disable dolly and truck, and negate the rotate speeds so dragging feels like turning your head.
- [ ] **Move only along links.** Click the floor: raycast into the GLB, then go to the
      `neighbors` entry nearest the hit point. If the hit is far from every neighbor, take the
      shortest path through the graph (breadth-first) to the sweep nearest the hit and step through it hop by hop.
      Forward key and phone joystick: go to the neighbor best aligned with the view direction
      (within about 45 degrees). Animate about 0.6 s, keeping the view direction.
- [ ] **Show where you can go:** a faint disc on the floor under each neighbor
      (`position.y - 1.5` is close enough; the room's `floor_y` is exact).
- [ ] **House view stays an orbit, but outside:** set `controls.colliderMeshes = [houseCollider]`
      so the camera stops at the walls. Raycasting the full GLB every frame is slow; a collider
      made from the twin's `walls` (extruded boxes, a few hundred triangles) is enough.

## Sketch

```ts
const byId = new Map(twin.sweeps.map((s) => [s.sweep_id, s]));
const eye = (s: Sweep) => new Vector3(s.position.x, s.position.y, s.position.z);

function lookFrom(c: CameraControls, s: Sweep, dir: Vector3, animate = true) {
  const p = eye(s);
  const t = p.clone().addScaledVector(dir.normalize(), 1e-3);
  return c.setLookAt(p.x, p.y, p.z, t.x, t.y, t.z, animate);
}

function stepToward(current: Sweep, goal: Vector3): Sweep {
  return current.neighbors
    .map((id) => byId.get(id)!)
    .reduce((best, s) => (eye(s).distanceTo(goal) < eye(best).distanceTo(goal) ? s : best), current);
}

// walk mode setup
c.minDistance = c.maxDistance = 1e-3;
c.azimuthRotateSpeed = c.polarRotateSpeed = -0.3;
c.mouseButtons.wheel = CameraControls.ACTION.NONE;
c.mouseButtons.right = CameraControls.ACTION.NONE;
c.touches.two = CameraControls.ACTION.NONE;
```

## Coming next from C
A new `interior.glb` rebuilt from the MatterPak point cloud (the current one is Matterport's
low-poly proxy; that is where the jagged tub and sliding textures come from), with the scan
photos projected back on. Same frame and asset key, so nothing changes on your side.
