# Lighting: warm where the sun reaches, cool where it does not

Owner: A. Code: `apps/web/src/lib/house/sun.ts` (the sun, the sky, the time-of-day curve),
`apps/web/src/lib/house/house.ts` (the house's shaders and its `setLight`),
`apps/web/src/components/house3d/House3D.tsx` (feeds the sun to the house and to furniture),
`apps/web/src/components/house3d/HouseAO.tsx` (shadow in corners).
Status: built, uncommitted (15 Nov 2026). Sections 1–6 and 8 are in; section 7 is not.

## The idea: one light shift

Before this work the house was lit in one neutral white: a grey sky light, a sun that went white
once it was 30° up, nearly white bounced light and white lamps. Every room looked the same at
10 am, 4 pm and 9 pm, and too bright.

Real rooms are lit by lights of different colour, and the eye reads time from the gap between
them:

- **Warm**: the sun where it lands, and the lamps.
- **Cool**: the open sky, and light bounced around a room. That's everything the sun does not
  reach directly: walls facing away, the back of a room, corners, shade outside.

**The light shift** is the gap between those two. It is small at noon (high white sun, pale sky),
widens through the afternoon, and is widest in the last hour before sunset (amber sun, blue
shade). After sunset the sun is gone, the sky goes blue, then a dark teal, and small warm lamp
pools are left against it.

The rooms are also **dim on purpose**: deep corners, a few pools of light, and a patch of sun as
by far the brightest thing in a room. Nothing indoors reaches pure white.

Everything below serves that one shift, driven by one clock.

## 1. One clock for the time of day

`SunLight.update` (sun.ts) works out, from the sun's elevation:

```ts
// 0 at noon-high sun, 1 when the sun is near the horizon.
const golden = 1 - MathUtils.smoothstep(elevationDeg, 4, 45);
// 0 by day, 1 in the blue hour just after sunset, back to 0 in full night.
const blueHour = MathUtils.smoothstep(elevationDeg, -10, -2) * (1 - MathUtils.smoothstep(elevationDeg, -2, 3));
// 0 by day, 1 at night.
const night = 1 - MathUtils.smoothstep(elevationDeg, -8, 2);
```

They go out as uniforms (`sunGolden`, `sunBlueHour`, `sunNight`) in `SUN_PARS`, so every lit
material, the sky and the haze can read them. **Nothing has its own clock**, so the warm and the
cool always move together.

## 2. The colours, by time of day

| Moment | Elevation | Sun (`sunColor`, before strength) | Sky / shade (`sky`) | Lamps |
|---|---|---|---|---|
| Noon | 45°+ | `1.00, 0.96, 0.88` (barely warm) | `0.78, 0.82, 0.90` (barely cool) | full, near white |
| Afternoon | 20° | warmer, blended by `golden` | bluer, blended by `golden` | full |
| Golden hour | 5° | `1.00, 0.62, 0.32` (amber) | `0.42, 0.55, 0.88` (blue) | full |
| Blue hour | −4° | off | `0.16, 0.24, 0.48` (deep blue) | warming, dimming |
| Night | −10° | off | `0.09, 0.15, 0.19` (dark teal) | `1.00, 0.78, 0.52` (~2700K), ×0.55 |

The sky's colour is a hue only: its brightness stays what it was (an average of 0.8 by day), so
exposure does not jump. Indoors at night the teal sky shows as a faint cool fill in the rooms,
under the lamps' warm pools.

## 3. Light bounced around a room: even and a little cool

The bake holds, per group of windows, what a room looks like lit by sun coming through it
(`houseSunA/B/C`), scaled at run time by `sunThrough`.

**Changed after review.** The first version made this bounce warm and stronger at golden hour.
Seen in the app, it made sunlit rooms glow white from the window and drop to dark when the sun
moved behind the trees. The user's direction: diffuse light should be **even, and cooler than the
light coming in**, not radiating from the window. So now:

- `THROWN_BACK` is neutral to slightly cool, `0.93, 0.96, 1.02`.
- The bounce levels off toward `BOUNCE_MAX` (0.3): `0.3 * (1 - exp(-3 * bounce / 0.3))`. A
  sunlit room is brighter than a shaded one, but its walls do not go white.
- It no longer counts as warm in the grade (section 5).
- Furniture and room outlines (lit by `PLAIN`, with no lightmap) get the usual floor's daylight
  plus the room's bounce (`houseAmbient` / `indoor`) and the lamps (`houseGlow` / `indoorWarm`).
- Each piece of furniture then takes the light of the spot it stands on (`houseLocal`): rays
  straight down around its footprint find the floor, read the sky and lamp lightmaps there (a CPU
  copy, at most 512²), and divide by the usual floor's level (`House.lightAround`). A sofa in a
  room the bake left dim is as dim as that room's floor, instead of lit like the house's average.

## 4. Sun patches with soft edges

`SUN_LIGHT` in house.ts looks the sun up in the shadow map with 8 taps on a disc, rotated per
pixel so the pattern does not band. The disc grows when the sun is low (long shadows are softer):

```glsl
float radius = sunTexel * mix(1.5, 4.0, sunGolden);
```

The shadow bias grows with the radius, so a wide disc does not shade the surface it is on (the
blotches seen in review).

**Removed after review:** a warm glow spreading about 0.4 m around each patch. It went against
the even, cool bounce of section 3. The patch itself is still the warmest, brightest thing in a
room.

The blotches left on outside walls at 16:15 are the dappled shade of the trees (the shadow map's
`thin` canopy flecks), not a bug. The cool shade makes them stand out more than before.

## 5. The grade: cool shade, warm light

At the end of `HOUSE_BODY`, after the light is applied:

1. **Split the light by where it came from.** `houseWarm` is the part from the sun and the lamps.
   Sky light and the room's bounce are the rest.
2. **Push the gap a little further, by time.** Where the cool part dominates, the colour leans
   toward `0.86, 0.92, 1.08`; where the warm part dominates, toward `1.08, 0.96, 0.84`. Strength
   `0.15 + 0.5 * sunGolden + 0.4 * sunBlueHour`, at most 0.75.
3. **Cap and contrast.** A surface's colour is pressed toward 0.85 above 0.55 (slope 2/3, not a
   hard cut, so a white rug keeps its weave), so nothing is pure white. Light above 0.75 is
   compressed toward 1.25 before it meets the colour, so a pale surface in a sun patch is bright
   but not flat white. Then an S-curve on brightness only (`l * l * (3 - 2l)`), blended in at 0.2,
   deepens the darks and leaves colours alone. An ACES curve was tried first and dropped: it
   washed the shadows out.
4. **Grain in the shade.** Shaded surfaces carry a fine grain, about 4 mm, fixed to the surface so
   it does not crawl when the camera moves. It is strongest in the dark and gone in full light, so
   a dim wall reads as a surface, not a flat fill.

## 6. The sky outside the windows

`SKY_FRAGMENT` follows the same clock:

- toward the horizon it fades into the haze (sun.ts, `houseFog`): blue-grey away from the sun,
  warm toward it;
- the horizon on the sun's side goes toward `1.0, 0.62, 0.38` by `sunGolden`;
- overhead goes toward violet-blue `0.20, 0.22, 0.48` by `sunBlueHour`.

Through a window this is what tells you the hour, so it matches the room's colours.

## 7. Not built: shafts of light

A half-resolution pass that marches 16 steps from the camera through the sun's shadow map and
adds warm haze where a ray passes through sun indoors. Desktop only, if the rest stays in budget.

## 8. A darker room: shadow in the corners, lamps as pools

Feedback after sections 1–6: the interior was too bright and too even, corners were a flat grey,
and the lamps lit rooms as strongly as the sun. The aim: a dim room with deep corners, small warm
pools, and the sun clearly the brightest thing.

**8a. Less light indoors.** `FLOOR_DAYLIGHT` 0.4 → 0.32 and `FLOOR_LAMPLIGHT` 0.62 → 0.22 set
how bright a room's usual floor comes out. Direct sun stays at 2.4. Open ground keeps its old
level (`OPEN_DAYLIGHT` 0.4), so the outside does not dim.

**8b. Lamps are small pools, not a second sun.** The baked lamp lightmap (`houseLamps`) already
knows where each lamp is. Its falloff is steepened: `pow(lamp / peak, fall) * peak`, with `peak`
2.5× the usual floor's lamp level (`houseLampPeak`).

- **By day:** full strength, `fall` 1.8.
- **At night:** ×0.55 (`LAMPS_AT_NIGHT`) and `fall` 3.2 (`LAMP_FALL_NIGHT`), so the pools are
  smaller and the gaps between them go dark.
- Colour goes from near white by day to ~2700K at night (section 2).

The first plan dimmed lamps by day and kept them full at night. The user asked for the opposite,
small pools at night, and that is what is built.

**Switched on 8 am to 5 pm only.** Later the user asked for the lamps to be on only in working
hours, so that early morning, evening and night show what daylight alone does to the house: an
atmospheric, dark house at night. `HouseLight` reads the house's local hour (`SunState.hour`) and
eases a switch (`houseLampsLit`, 0.4 s) to 1 from 8:00 to 17:00 and to 0 otherwise. It scales the
lamps' light on every surface, the working-hours lights (`sunWork`) and the lamp fittings' glow (a
switched-off fitting is a pale face lit by the room's daylight).

**8c. Shadow in corners and along edges (ambient occlusion).** The bake that made the lightmaps
lives in another project, so this is worked out each frame from the screen. `HouseAO.tsx` uses
three's own `GTAOPass` (no new dependency), at half resolution: radius 0.3 m, scale 1.5,
12 samples. Crisp contact shadows where a door, a cabinet or a wall meets another surface, and
long enough that furniture (which the bake never saw) sits in its own shadow on the floor. It runs only indoors (the Tour inside a room);
outside, the haze already sets things apart.

**8d. Cool in the corners.** The AO blend is tinted: an occluded corner goes toward a deep
blue-grey (`0.10, 0.12, 0.18`), with strength `0.55 + 0.35 * sunGolden + 0.25 * sunNight`
(at most 1), and only 15% weaker under the working-hours lights. A corner far from the window reads cool and dark, and a sun patch or lamp pool next
to it reads warm.

**8e. Smoother lightmaps.** The baked lightmaps were blotchy. They now get an edge-preserving
smoothing: neighbouring texels are averaged only if they are within 30% of the centre texel, or
within half a usual floor's level. Noise goes, and edges (a shadow line, a corner) stay sharp.

## What it costs

| Part | Cost |
|---|---|
| 1, 2, 3, 6 | A few uniforms per frame. None per pixel. |
| 4 | 8 shadow-map taps per pixel (9 before). |
| 5, 8e | About 25 instructions per pixel, plus 8e's lightmap neighbour reads. |
| 8c, 8d | A second draw of the scene (depth and normals) and a half-resolution AO pass, indoors only. |

Budget: the Tour holds 60 fps on the demo laptop and 30 fps on a mid-range phone.
**Not measured yet**: every check so far ran in a hidden browser pane, which throttles drawing.
8c has no fallback for slow phones yet (quarter resolution, or off below 30 fps).

## How to check it

Inside Living Room and Inside Family Room, plus Outside, at 10:00, 12:00, 15:30, 16:15, 17:20
(golden hour on 14 Nov) and 21:20:

- **By day:** walls are clearly lit but not white; corners and the bases of cabinets and doors
  darker than the middle of a wall. A sun patch is the brightest thing in view.
- **12:00 → 16:15:** brightness falls smoothly, with no cliff when the sun stops coming in through
  a window.
- **16:15–17:20:** sun patches amber, shade and corners blue-grey, orange horizon through the
  windows.
- **21:20:** lamps off: a dark room in the faint teal of the night sky, a teal-dark outside.
- Press play from 15:00 to 21:00. No jumps in brightness or colour.
