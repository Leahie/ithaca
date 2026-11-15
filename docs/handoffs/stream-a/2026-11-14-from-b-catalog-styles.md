---
from: B
to: A
date: 2026-11-14
status: open
source: catalog expansion
---
# The catalog now has nine looks: 160 pieces in 27 sets

**Why:** every piece came from one mid-century modern line, so every furnished room looked the
same. The catalog now covers nine looks, each made of sets that were designed to go together.

| Look | Sets | Pieces |
|---|---|---|
| Mid-century modern | Sloane living, Aiden lounge, Jonathan bedroom, Hillmoor bedroom, West kid's room, Federal dining, Anne office | 40 |
| Farmhouse | Parson living, Parson dining, Bateman bedroom | 18 |
| Industrial | Glenwood living, Glenwood bedroom, Glenwood dining, Elias office | 22 |
| Coastal | Coastal Breeze living, Chase bedroom | 15 |
| Traditional | Charles living, Newport bedroom, Dunbar dining, Classic white entryway | 20 |
| Glam | Alonzo living, Catelyn bedroom | 16 |
| Scandinavian | Ribe living, Havel bedroom, Havel office | 15 |
| Kids | Kids' bunk room | 6 |
| Outdoor | Archer patio | 8 |

A piece shared by two sets (a lamp, a rug) is counted once, under the first look that uses it.

## What changed for the web app
- `GET /api/catalog` rows now carry `style` and `sets` (set names). `?style=Farmhouse` keeps one look.
- New: `GET /api/catalog/sets[?style=]` returns `CatalogSet[]`: `set_id`, `name`, `style`, `rooms`,
  `look`, `piece_ids`, `thumbnail_urls` (its first four pieces). Nothing in the web app calls it
  yet; it is there for set cards.
- `CatalogBrowser` groups the grid by look under small headings, and its search matches a look or
  a set by name ("glam", "Parson"). No style chips: `docs/style.md` rules them out.
- Every picture in `fixtures/blobs/catalog/` (160, 2.3 MB, Git LFS) was drawn again by one
  renderer, so old and new pieces look alike.

## Where the pieces come from
All 160 are from Amazon Berkeley Objects (ABO), CC BY 4.0, credit "Amazon.com". Its listing
metadata was searched for furniture lines with a 3D model (7,953 products have one):
Stone & Beam (farmhouse, industrial, coastal, traditional), Rivet (mid-century, glam),
Ravenna Home (traditional, patio), Movian (Scandinavian), AmazonBasics and Max & Finn (kids).

Other datasets looked at, and why they were not used:

| Dataset | Licence | Verdict |
|---|---|---|
| Poly Haven models | CC0, open API | 85 furniture scans, mostly antiques. Was the catalog before ABO and was dropped: pieces picked one by one did not match. A second source for a few themed sets (Chinese, Gothic, painted cottage) is possible later. |
| Objaverse 1.0 | ODC-By; per object CC BY / CC0 / NC variants | Downloadable, but furniture is unsorted, of uneven quality and scale, with no matching lines. Not used. |
| Objaverse-XL | mixed, per object | Access through a form; licences vary by source. Not used. |
| 3D-FUTURE / 3D-FRONT | research-only terms | Needs a signed application. Not allowed here. |
| HSSD | CC BY-NC 4.0, gated on Hugging Face | Non-commercial and behind an agreement. Not used. |
| IKEA 3D Assembly Dataset | CC BY-NC-SA 4.0 | Five products, non-commercial. Not used. |
| Kenney, Quaternius (low-poly kits) | CC0 | Game-style low-poly: wrong look next to a scanned house. Not used. |
| Poly Pizza | per model, mostly CC BY | Low-poly, mixed licences. Not used. |
| Google Scanned Objects, Smithsonian Open Access, ambientCG | CC BY 4.0 / CC0 | Checked by name only: household objects and museum scans, not furniture lines. Not used. |

## How a piece is checked (providers/catalog_build.py)
- `find` searches the ABO index; `check` loads a model and compares its size with its listing;
  `rows` prints the catalog row with the measured size; `render` draws the picture.
- Every one of the 160 models was downloaded, measured, and put through the same sizing step that
  adding a piece uses (`objects/normalize.py`): all pass within 2 cm of the size in the catalog.
- The catalog's sizes are the models' own. 131 agree with the listing's dimensions (within 12 % or
  6 cm); 23 more agree with a size in the product's name, where the listing gives the shipping
  box; 6 match neither and were judged from the picture: two sets of nesting tables (the model
  shows both tables), two extending dining tables (the model is the short length), a nightstand,
  and the kids' desk (the model includes its chair).
- Three models are drawn too large by one factor in every direction (Glenwood nightstand and
  dresser, Newport dresser). They are scaled to the listed size when cached (`catalog._SCALE`).
- Dropped: king beds whose model is the queen with a new name (Parson, Glenwood, Tisbury), the
  Parson queen bed (wider and taller than sold, but not longer), a Bradbury sofa of 226k
  triangles, and pieces whose colour did not match their set.
