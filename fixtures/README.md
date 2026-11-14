# Fixtures

Stand-ins written by Stream A so the web app can run offline (WORKSTREAMS.md section 3).
Streams B and C own these paths and replace the files when their mocks land. The web app
reads whatever is here; it needs no change.

Rebuild: `pnpm --filter @ithaca/web fixtures:build` (add `--fetch` with `MATTERPORT_TOKEN_ID`
and `MATTERPORT_TOKEN_SECRET` set to pull the model again). The output is deterministic.

| Path | Real or stand-in |
|---|---|
| `twin/onXQLXhfyQv.json` | Rooms, room outlines, openings, sweeps, address and GPS are real Matterport Model API data. `interior_to_world` assumes heading 0 (`quality.alignment: gps_only`). Glazing is a guess (`double`). |
| `blobs/onXQLXhfyQv/interior.glb` | Real Matterport 50k mesh, untextured, in the interior frame. |
| `blobs/onXQLXhfyQv/context.glb`, `ortho.svg` | **Synthetic.** Ground, box neighbors and trees, so the Sun Lab has something to cast shadows. Not DSM, not Vexcel. |
| `blobs/inventory/*`, `inventory/demo.json` | Three hand-built furniture items that meet I-G15. |
| `layouts/onXQLXhfyQv.json` | Three valid variants for one living room, placed by a small search in the builder. |
| `reports/onXQLXhfyQv/sun/*`, `noise/*`, `area.json` | **Placeholder numbers** with the right schema shape. Not the sun or noise engines, not CoStar. |
| `sun/reference.json` | pvlib sun positions (`apps/web/scripts/fixtures/sun_reference.py`). |
| `memory/demo/**` | Sample memory files. |

`blobs/**/*.glb` is in Git LFS (I-G20): run `git lfs pull`.
