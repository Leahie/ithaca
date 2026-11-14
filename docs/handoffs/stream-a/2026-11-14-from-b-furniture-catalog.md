---
from: B
to: A
date: 2026-11-14
status: open
source: feat/furniture-catalog
---
# A furniture catalog, and adding pieces from the Arrange panel

**Why:** users could only get furniture by uploading a photo. There is now a catalog of 51
ready-made pieces (free Poly Haven models at real size) that anyone can add, with no keys.

## What you need to do
- [ ] **Review the web changes B made** (your code): `components/design/CatalogBrowser.tsx` (new),
      `components/house/ArrangePanel.tsx` ("Add furniture" opens the catalog; "Your furniture"
      chips place owned pieces that aren't in the room yet), `lib/api/{types,http,fixture}.ts`
      (`listCatalog`, `addFromCatalog`, `dropItem`), and the Arrange button in
      `HouseExperience.tsx`, which now shows whenever the Tour or Sun Lab is open.
- [ ] **Offer the catalog on `/me` too,** next to the upload button in `InventorySection`, so the
      inventory page has a way to add furniture that needs no photo. `CatalogBrowser` takes an
      `onAdded(item)` callback; on `/me` it only needs to refresh the list.
- [ ] **Thumbnails are remote images** from `cdn.polyhaven.com`. `CatalogBrowser` uses a plain
      `<img>`. If you prefer `next/image`, add that host to `next.config.ts`.

## Details
- `GET /api/catalog?item_class=` returns `CatalogPiece[]`: `catalog_id`, `label`, `item_class`,
  `dims_m`, `thumbnail_url`, `source`.
- `POST /api/catalog/{catalog_id}/add` returns the new `InventoryItem`. Optional body
  `{label, width_m, depth_m, height_m}`: give all three sizes to resize it. The first use of a
  piece downloads its model (about 1 MB, then cached in `data/catalog/`), so it can take a second.
- Added items have `source_image_url` set to the thumbnail and `product_url` to the Poly Haven
  page. `mask_url` is null.
- In fixture mode the catalog is empty and adding returns a "needs the API" error.
- The guide has matching tools (`catalog_search`, `catalog_add`): it can offer a bed for a bedroom
  the user has no bed for, and add it when they agree.
