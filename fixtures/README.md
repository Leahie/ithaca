# Fixtures

What the web app runs on in fixture mode (A6), and what the backend replays in tests (I-G20).

| Path | Owner | Real or stand-in |
|---|---|---|
| `twin/onXQLXhfyQv.json` | C | Real. Built by `services/api/twin/ingest.py` from Matterport data. See `docs/stream-a-handoff.md`. |
| `blobs/onXQLXhfyQv/interior.glb` | C | Real textured Matterport mesh, meshopt-compressed, interior frame. |
| `integrations/**` | C | Recorded API responses for replay. |
| `sun/reference.json` | C | pvlib sun positions. Written by `apps/web/scripts/fixtures/sun_reference.py` until C ships its own. |
| `blobs/inventory/*`, `inventory/demo.json` | B | Stand-in. Three hand-built furniture items that meet I-G15. |
| `layouts/onXQLXhfyQv.json` | B | Stand-in. Three valid variants for one living room. |
| `memory/demo/**` | B | Stand-in. Sample memory files. |
| `reports/onXQLXhfyQv/sun/*`, `noise/*`, `area.json` | C | **Placeholder numbers** with the right schema shape. Not the sun or noise engines, not CoStar. |

The stand-ins are written by Stream A from the twin:
`pnpm --filter @ithaca/web fixtures:build`. Run it again when the twin changes. The output is
deterministic. The owners replace these files when their mocks land; the web app reads whatever
is here and needs no change.

`blobs/**` is in Git LFS: run `git lfs pull`.
