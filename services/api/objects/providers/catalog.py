"""The furniture catalog: ready-made pieces that belong together.

The pieces are real products from the Amazon Berkeley Objects dataset (ABO,
https://amazon-berkeley-objects.s3.amazonaws.com/index.html), which publishes a 3D model and
photos for each under CC BY 4.0 (credit: Amazon.com). They come from several of its furniture
lines, each with its own look (STYLES): mid-century modern, farmhouse, industrial, coastal,
traditional, glam, Scandinavian, plus a kids' room and a patio. Within a line the named
collections were designed to match (a bed with its nightstand and dresser, a dining table with
its sideboard), so a room furnished from one SET looks like a room, not a second-hand shop.
Models are at real size in metres, stand on y = 0 and face +Z. Credit is shown wherever the
catalog is (SOURCE).

OBJECT_PROVIDER=catalog also uses these as stand-ins for a photographed piece: the default model
of its class, sized to the photo. A model is downloaded the first time it is needed and cached
under data/catalog/abo/, with its textures shrunk to 1024 px (the originals are up to 4096 px and
16 MB a piece, too heavy for a browser showing a whole house). `python -m
services.api.objects.providers.catalog` downloads every model now.

Adding pieces: providers/catalog_build.py searches the dataset, measures a model against its
listing, prints the row for it and draws its picture (fixtures/blobs/catalog/<id>.jpg).
"""

from __future__ import annotations

import io
import json
import logging
import struct
from pathlib import Path

import httpx

from services.api.integrations.config import REPO_ROOT, env
from services.api.objects.providers.base import Model3D, Piece, Segment
from services.api.objects.providers.stub import BoxTo3D

log = logging.getLogger(__name__)

# (ABO model id, furniture class, label, width, depth, height in metres, photo path, product name)
# The sizes are the model's own, measured (catalog_build.py check) and compared with the listing's
# size or, where the listing only gives the shipping box, with the width in the product's name.
_ROWS: list[tuple[str, str, str, float, float, float, str, str]] = [
    ("B072FVHS9G", "sofa", "Sloane sofa, pebble grey", 2.06, 0.97, 0.87, "39/39992106.jpg", "Amazon Brand – Rivet Sloane Mid-Century Modern Sofa with Tufted Back, 79.9W, Pebble"),
    ("B0719WQH8N", "loveseat", "Sloane loveseat, pebble grey", 1.65, 0.95, 0.92, "02/02c92f39.jpg", "Amazon Brand – Rivet Sloane Mid-Century Modern Sofa with Tufted Back, 64.2W, Pebble"),
    ("B0714QGLSD", "armchair", "Sloane armchair, pebble grey", 0.85, 0.94, 0.97, "7e/7eee102c.jpg", "Amazon Brand – Rivet Sloane Mid-Century Modern Armchair with Tapered Legs, 32.7W, Pebble"),
    ("B071W5VJF4", "ottoman", "Sloane ottoman, pebble grey", 0.82, 0.61, 0.44, "9a/9ae7d2f0.jpg", "Amazon Brand – Rivet Sloane Mid-Century Angled Leg Modern Ottoman, 31.9W, Pebble"),
    ("B07P6JTVZV", "armchair", "Cove accent chair, caramel leather", 0.83, 0.87, 0.90, "f8/f86e6baa.jpg", "Amazon Brand – Rivet Cove Modern Tufted Accent Chair with Tapered Legs, Mid-Century, 32.7W, Caramel"),
    ("B072ZN4NDS", "coffee_table", "Two-drawer coffee table, grey and walnut", 1.20, 0.70, 0.40, "cf/cff3bdd5.jpg", "Amazon Brand – Rivet Mid-Century Lacquer Two-Drawer Coffee Table, Grey and Walnut"),
    ("B07GDSF3MR", "coffee_table", "Round coffee table, walnut", 0.80, 0.80, 0.36, "32/32cd9937.jpg", "Amazon Brand - Rivet Round Coffee Table with Solid Wood Legs, 80 x 80 x 36cm, MDF with Walnut Veneer/Solid Bee"),
    ("B07BVHKPFS", "tv_stand", "Bowlyn media stand, walnut", 1.63, 0.48, 0.61, "2f/2fc198b8.jpg", "Amazon Brand – Rivet Bowlyn Mid-Century Modern Wood TV Media Table Stand, 64, Walnut"),
    ("B07GDZ8HN6", "side_table", "One-drawer side table, walnut", 0.43, 0.43, 0.51, "af/af8a4df6.jpg", "Amazon Brand - Rivet 1-Drawer End/Side Table, 43 x 43 x 52cm, MDF with Walnut Veneer/Black Metal Frame"),
    ("B07B7ND7YH", "side_table", "Nesting side tables, brass and walnut", 0.53, 0.75, 0.56, "e9/e94e8413.jpg", "Amazon Brand – Rivet Mid-Century Nesting Metal Side Table, Set of 2, Brass and Walnut"),
    ("B07HK5HDPZ", "floor_lamp", "Floor lamp, antique brass", 0.31, 0.30, 1.50, "44/446404a2.jpg", "Amazon Brand – Rivet Modern Metal Floor Lamp with Bulb, 59H, Antique Brass"),
    ("B07B4WH5K3", "rug", "Striated jute rug, linen, large", 3.20, 2.43, 0.05, "1a/1a52b9ad.jpg", "Amazon Brand – Rivet Contemporary Striated Jute Area Rug, 10' 6 x 8', Linen"),
    ("B07B4WKMB1", "rug", "Striated jute rug, oatmeal, small", 1.79, 1.15, 0.02, "d9/d910a666.jpg", "Amazon Brand – Rivet Contemporary Striated Jute Area Rug, 5' 9 x 3' 9, Oatmeal"),
    ("B07QC6X16K", "plant", "Ceramic planter on a stand, white and tan", 0.31, 0.31, 0.43, "1f/1f3384a9.jpg", "Amazon Brand – Rivet Mid-Century Ceramic Planter with Iron Stand, 17H, White and Tan"),
    ("B07HSCJZQM", "bookshelf", "Roxmere ladder bookcase, acacia", 0.65, 0.45, 2.00, "c3/c3249cb5.jpg", "Amazon Brand – Rivet Roxmere Modern Ladder Bookcase, 23.6W, Acacia Wood and Dark Metal"),
    ("B075X2WN11", "sofa", "Aiden leather sofa, cognac", 2.20, 0.89, 0.78, "bb/bb983934.jpg", "Amazon Brand – Rivet Aiden Tufted Mid-Century Leather Bench Seat Sofa, Without Side Pillows, 86.6W, Cognac"),
    ("B075X2WN27", "armchair", "Aiden leather chair, cognac", 0.93, 0.80, 0.77, "94/94871e53.jpg", "Amazon Brand – Rivet Aiden Tufted Mid-Century Modern Leather Accent Chair, 35.4W, Cognac"),
    ("B07QJX1JY9", "sectional", "Aiden leather sectional, cognac", 2.18, 1.51, 0.88, "23/23dafd0a.jpg", "Amazon Brand – Rivet Aiden Mid-Century Leather Sectional with Tapered Wood Legs, 86W, Cognac"),
    ("B07B4SBZFN", "bed", "Jonathan queen bed, walnut", 1.66, 2.23, 1.36, "fe/fe7b18f1.jpg", "Rivet Jonathan Mid-Century Modern Wood Queen Bed, 64.5 W, Walnut"),
    ("B07B4W31C3", "bed", "Jonathan full bed, walnut", 1.51, 2.09, 1.30, "fe/fe7b18f1.jpg", "Rivet Jonathan Mid-Century Modern Wood Full Bed, 59.5W, Walnut"),
    ("B07B4VXWTY", "bed", "Jonathan king bed, walnut", 1.66, 2.23, 1.32, "fe/fe7b18f1.jpg", "Amazon Brand – Rivet Jonathan Mid-Century Modern Wood King Bed Frame Platform with Headboard, 81.5W, Walnut"),
    ("B07B4W2FFY", "nightstand", "Jonathan nightstand, walnut", 0.52, 0.44, 0.61, "3f/3f527ee3.jpg", "Amazon Brand – Rivet Jonathan Mid-Century Modern Wood Bedside Nightstand, 20, Walnut"),
    ("B07B4SBZDV", "dresser", "Jonathan dresser, walnut", 1.47, 0.50, 0.89, "94/9441a53e.jpg", "Amazon Brand – Rivet Jonathan Mid-Century Modern Wood Bedroom Dresser, Brass-Toned Hardware, 58 Inch Wide, Wal"),
    ("B07B4YZWRK", "dresser", "Jonathan tall dresser, walnut", 0.92, 0.50, 1.36, "06/060cfb81.jpg", "Amazon Brand – Rivet Jonathan Mid-Century Modern Tall Wood Bedroom Dresser Drawers, 54 Inch Height, Brass Hard"),
    ("B07B4YXNR3", "bed", "Hillmoor queen bed, light grey", 1.66, 2.23, 1.15, "50/5045b0c2.jpg", "Amazon Brand – Rivet Hillmoor Mid-Century Queen Bed with Headboard, 66W, Light Gray"),
    ("B072ZMHBKQ", "nightstand", "Nightstand, grey and walnut", 0.37, 0.29, 0.51, "8e/8eb95ea0.jpg", "Amazon Brand – Rivet Mid-Century Modern Lacquer Side End Table Nightstand, Grey and Walnut"),
    ("B0856FL9HR", "bed", "Pine platform bed, twin, espresso", 0.96, 1.90, 0.62, "28/2887b77c.jpg", "Amazon Brand – Rivet Modern Solid Pine Wood Platform Bed, Twin, 38.39W, Antique Espresso"),
    ("B0856FK4BW", "bed", "Pine platform bed, full, espresso", 1.35, 1.93, 0.62, "8b/8b7224e1.jpg", "Amazon Brand – Rivet Modern Solid Pine Wood Platform Bed, Full, 53.9W, Antique Espresso"),
    ("B07177355J", "nightstand", "West nightstand, dark oak", 0.56, 0.44, 0.58, "b3/b3b371c0.jpg", "Amazon Brand – Rivet West, Mid-Century, Oak Nightstand Bedside, 1-Drawer Table, 22W, Dark Oak Finish"),
    ("B071SHBTM5", "dresser", "West chest of drawers, dark oak", 0.96, 0.52, 1.32, "48/488235f2.jpg", "Amazon Brand – Rivet West, Mid-Century, Oak Distinct Grain Chest Bedroom Dresser, 38W, Dark Oak Finish"),
    ("B07B86VTX4", "dining_table", "Federal dining table, seats six", 1.52, 0.99, 0.76, "37/37192b81.jpg", "Amazon Brand – Rivet Federal Mid-Century Modern Medium Wood Dining Table, 60-82L, Brown"),
    ("B07B85FJD5", "dining_table", "Federal dining table, small", 0.99, 0.84, 0.76, "91/91d88b57.jpg", "Amazon Brand – Rivet Federal Mid-Century Modern Small Wood Dining Table, 39-57L, Brown"),
    ("B07B78LXYF", "chair", "Federal dining chair, grey", 0.53, 0.68, 0.92, "dd/dd24653f.jpg", "Amazon Brand – Rivet Federal Mid-Century Modern Wood Dining Room Kitchen Chairs, Set of 2, Grey"),
    ("B07B79WSKT", "cabinet", "Federal sideboard", 1.57, 0.50, 0.94, "79/795a58b9.jpg", "Amazon Brand – Rivet Federal Mid-Century Modern Wood Dining Buffet Sideboard, Cabinet Credenza, Storage, Brown"),
    ("B07TSD3DYP", "stool", "Decatur counter stool, light grey", 0.46, 0.56, 0.94, "d8/d87fba31.jpg", "Amazon Brand – Rivet Decatur Modern Kitchen Counter-Height Stool, 37H, Light Grey"),
    ("B07TYTR8J5", "stool", "Malida swivel counter stool, grey", 0.55, 0.56, 0.94, "29/2958b275.jpg", "Amazon Brand – Rivet Malida Mid-Century Modern Faux Leather Open Back Swivel Kitchen Counter Stool, 37H, Grey"),
    ("B07B7DL32P", "desk", "Anne desk, walnut", 1.57, 0.72, 0.76, "14/1478021a.jpg", "Amazon Brand – Rivet Anne Mid-Century Modern Wood Office Computer Desk, 62W, Walnut"),
    ("B07B7JY9C5", "cabinet", "Anne filing cabinet, walnut", 0.40, 0.50, 0.46, "a0/a0382e73.jpg", "Amazon Brand – Rivet Anne Mid-Century Modern Wood Filing Cabinet with Wheels, 20W, Walnut"),
    ("B075Z8M22B", "chair", "Swivel desk chair, marble grey", 0.61, 0.63, 0.88, "62/62cb6175.jpg", "Amazon Brand – Rivet Mid-Century Open Back Swivel Office Desk Chair, 24W, Marble"),
    ("B075ZBW1SN", "desk", "Ventura writing desk, small", 1.28, 0.61, 0.76, "57/57937f83.jpg", "Amazon Brand – Rivet Ventura Mid-Century Small Reversible Writing Home Office Computer Desk with File Drawer, "),
    # Farmhouse, industrial, coastal, traditional, glam, Scandinavian, kids and outdoor lines (same dataset).
    ('B07B4MG9HX', 'sofa', 'Brandeberry roll-arm sofa, beige', 2.16, 1.09, 1.09, '7b/7b59b65c.jpg', 'Stone & Beam Brandeberry Farmhouse Charles of London Sofa, 82"W, Beige'),
    ('B07B4LZP9Q', 'armchair', 'Brandeberry roll-arm armchair, beige', 0.96, 1.07, 1.04, '01/013f3a77.jpg', 'Stone & Beam Brandeberry Farmhouse Charles of London Accent Chair, 38"W, Beige'),
    ('B07B4M68LW', 'ottoman', 'Brandeberry ottoman, beige', 0.77, 0.57, 0.50, 'c2/c2016d8a.jpg', 'Stone & Beam Brandeberry Farmhouse Charles of London Ottoman, 31"W, Beige'),
    ('B074KLN3HW', 'coffee_table', 'Parson coffee table, honey oak', 1.15, 0.64, 0.48, '2f/2ff8da53.jpg', 'Stone & Beam Parson Coffee Table, 45"L, Oak'),
    ('B074KMVDRS', 'tv_stand', 'Parson media console, honey oak', 1.82, 0.43, 0.64, '22/22359416.jpg', 'Stone & Beam Parson Entertainment Console with Drawers, 72"W, Oak'),
    ('B074KLFZRM', 'side_table', 'Parson side table, honey oak', 0.62, 0.61, 0.59, '26/26f180ec.jpg', 'Stone & Beam Parson Wood Side End Table, 26"L, Oak'),
    ('B074KLN3HX', 'bookshelf', 'Parson shelf tower, honey oak', 0.56, 0.43, 1.83, 'fd/fdd95378.jpg', 'Stone & Beam Parson Entertainment Tower with Wood Shelves and Drawer, 72"H, Oak'),
    ('B07B4WKN5K', 'rug', 'Kilim-pattern cotton rug, grey, large', 3.03, 2.42, 0.01, 'dc/dcdd097e.jpg', 'Stone & Beam Casual Geometric Cotton Area Rug, 8 x 10 Foot, Flatweave, Grey, Orange'),
    ('B07DT153M1', 'floor_lamp', 'Farmhouse floor lamp, wood base', 0.75, 0.47, 1.71, '61/6138372f.jpg', 'Stone & Beam Modern Farmhouse Wood Base Adjustable Living Room Floor Lamp With LED Light Bulb And White Shade-'),
    ('B075YMW6VR', 'dining_table', 'Parson trestle dining table, seats six', 1.98, 1.00, 0.76, '8b/8b714f51.jpg', 'Stone & Beam Parson Wood Trestle Dining Table, 78"-96"L, Oak Finish'),
    ('B075YPTDT5', 'chair', 'Parson dining chair, cream', 0.53, 0.65, 1.01, '4c/4cbc184e.jpg', 'Stone & Beam Parson Armless Dining Room Chairs, 40 Inch Height, Set of 2, Beige'),
    ('B075YQ8Q3T', 'cabinet', 'Parson sideboard, honey oak', 1.47, 0.48, 0.81, '33/3370b158.jpg', 'Stone & Beam Parson Rustic Buffet Sideboard Storage Cabinet 56"W, Oak Wood'),
    ('B075Z8PMML', 'bed', 'Bateman queen bed, reclaimed wood', 1.70, 2.28, 1.42, '8e/8e74f9e6.jpg', 'Stone & Beam Bateman Casual Rustic Wood Platform Bed Frame with Tall Headboard, Queen, 65"W, Brown'),
    ('B075Z8GZ9X', 'nightstand', 'Bateman nightstand, brass legs', 0.61, 0.45, 0.61, '06/06fe53a6.jpg', 'Stone & Beam Bateman Casual Rustic Wood Bedside Table Nightstand, Brass Hardware, 24 Inch Wide, Brown'),
    ('B075Z8THYY', 'dresser', 'Bateman dresser, reclaimed wood', 1.39, 0.44, 0.89, '01/01e340ab.jpg', 'Stone & Beam Bateman Casual Rustic Wood Bedroom Dresser, 55 Inch Wide, Brown'),
    ('B075Z93N8N', 'dresser', 'Bateman tall dresser, reclaimed wood', 1.07, 0.44, 1.36, '2b/2b9904d7.jpg', 'Stone & Beam Bateman Casual Rustic Wood Bedroom Dresser, Brass Hardware, 42 Inch Wide, Brown'),
    ('B075QDQXW1', 'bench', 'Nori tufted storage bench, linen', 1.48, 0.45, 0.47, '80/800283d9.jpg', 'Stone & Beam Nori Tufted Storage Bench, 58"L, Natural'),
    ('B07B4WR39P', 'rug', 'Floral wool rug, grey-blue, medium', 2.50, 1.54, 0.02, 'ce/cec84d53.jpg', 'Stone & Beam Floral Wool Area Rug, 5 x 8 Foot, Blue'),
    ('B07QBMQ4LN', 'sofa', 'Abigail leather sofa, dark brown', 2.18, 1.02, 0.87, '1e/1e477c97.jpg', 'Stone & Beam Abigail Leather Sofa, 86"W, Dark Brown'),
    ('B07QHYD2KF', 'armchair', 'Abigail leather armchair, dark brown', 1.01, 1.02, 0.87, '64/64ffb99a.jpg', 'Stone & Beam Abigail Leather Living Room Chair, 40"W, Dark Brown'),
    ('B07QGSZ79Z', 'ottoman', 'Abigail leather ottoman, dark brown', 0.75, 0.60, 0.48, '11/1115aa82.jpg', 'Stone & Beam Abigail Leather Ottoman, 29"W, Dark Brown'),
    ('B074KMJ96Q', 'coffee_table', 'Glenwood storage coffee table, oak and steel', 1.17, 0.72, 0.36, 'f6/f6b5c88a.jpg', 'Stone & Beam Glenwood Storage Coffee Table, 46"W, Oak'),
    ('B074KMW499', 'tv_stand', 'Glenwood media console, oak and steel', 1.54, 0.38, 0.58, 'ea/ea9169ee.jpg', 'Stone & Beam Glenwood Entertainment TV Console Media Stand, 68"W, Oak'),
    ('B074KMZGTC', 'side_table', 'Glenwood end table, oak and steel', 0.51, 0.57, 0.58, 'ad/ade0b62b.jpg', 'Stone & Beam Glenwood End Table, 23"H, Oak'),
    ('B074KKXLK1', 'bookshelf', 'Glenwood bookcase, oak and steel', 0.54, 0.40, 1.73, '37/377fe604.jpg', 'Stone & Beam Glenwood Bookcase, 72"H, Oak'),
    ('B0732HV63Q', 'rug', 'Distressed medallion rug, steel grey', 2.43, 1.54, 0.01, '73/73fa448c.jpg', 'Rivet Distressed Medallion Area Rug, 5 x 8 Foot, Steel'),
    ('B07HKGKSMM', 'floor_lamp', 'Pharmacy floor lamp, bronze', 0.24, 0.85, 1.34, '6a/6a23406e.jpg', 'Stone & Beam Industrial Adjustable Pharmacy Living Room Floor Lamp with LED Light Bulb - 10 x 31 x 51 Inches, '),
    ('B071FJR4FW', 'bed', 'Glenwood queen bed, oak and steel', 1.63, 2.11, 1.42, '82/829ec2ec.jpg', 'Stone & Beam Glenwood Industrial Metal Accent Bed, Queen, 84.5"L, Oak'),
    ('B071FJR46L', 'nightstand', 'Glenwood nightstand, oak and steel', 0.60, 0.42, 0.58, '5c/5c237c07.jpg', 'Stone & Beam Glenwood Industrial Metal Accent Nightstand End Table, 23"H, Oak'),
    ('B071FJR3S6', 'dresser', 'Glenwood dresser, oak and steel', 1.51, 0.57, 0.98, '05/05cb2c2e.jpg', 'Stone & Beam Glenwood Industrial Metal Dresser, 60"W, Oak'),
    ('B071FJR3SR', 'dresser', 'Glenwood tall chest, oak and steel', 0.97, 0.51, 1.32, 'c6/c6a3123c.jpg', 'Stone & Beam Glenwood Industrial Metal Chest of Drawers, 52"H, Oak'),
    ('B0735RJPDH', 'rug', 'Distressed wool rug, ivory', 2.46, 1.63, 0.01, '80/80ae16b6.jpg', 'Rivet Contemporary Linear Distressed Wool Area Rug, 5 x 8 Foot, Grey'),
    ('B075YMW6LH', 'dining_table', 'Glenwood dining table, oak and steel', 1.99, 1.08, 0.80, '38/389a50ec.jpg', 'Stone & Beam Glenwood Modern Industrial Metal Base Dining Room Table, 78"L, Oak Wood'),
    ('B07B7B244W', 'chair', 'Allie leather dining chair, tan', 0.57, 0.68, 0.83, '86/86bb70ad.jpg', 'Rivet Allie Leather Industrial Mid-Century Dining Room Kitchen Chair, 33"H, Brown and Black'),
    ('B075ZBW1RR', 'bookshelf', 'Larson low bookcase, walnut and steel', 0.82, 0.35, 0.95, 'd5/d581c685.jpg', 'Stone & Beam Larson Industrial Wood and Metal 2-Shelf Bookcase, 39"H, Walnut'),
    ('B072PXBV88', 'rug', 'Distressed medallion rug, charcoal', 2.27, 1.54, 0.01, 'cf/cf0543ae.jpg', 'Rivet Charcoal Distressed Medallion Area Rug, 5 x 7 Foot'),
    ('B07B7DMLPL', 'desk', 'Elias desk on castors, wood and steel', 1.59, 0.71, 0.75, '7a/7a1fa1c7.jpg', 'Stone & Beam Elias Industrial Metal Office Computer Desk, 60"W, Brown/Black'),
    ('B07BZQPQM8', 'chair', 'Slope leather desk chair, saddle', 0.65, 0.66, 0.92, 'd7/d7ad9cfa.jpg', 'Rivet Industrial Slope Top-Grain Leather Swivel Office Chair, 24.41" H, Saddle Brown'),
    ('B075Z6YS1Z', 'bookshelf', 'Larson tall bookcase, walnut and steel', 0.87, 0.36, 1.86, '19/191f5601.jpg', 'Stone & Beam Larson Industrial Wood and Metal 4-Shelf Bookcase, 73"H, Walnut'),
    ('B07HK8NDXS', 'floor_lamp', 'Shelf floor lamp, black metal', 0.43, 0.38, 1.47, '4e/4e0468f8.jpg', 'Stone & Beam Rustic Storage Space/Shelving Unit Pull Chain Switch Floor Lamp with Bulb, 62"H, Black'),
    ('B07B4MKFR5', 'sofa', 'Bartow slipcover sofa, natural', 2.29, 1.03, 1.03, '86/862c547a.jpg', 'Stone & Beam Bartow Living Room Loveseat Sofa with Slipcover, 86"W, Natural'),
    ('B07B4MN5P2', 'loveseat', 'Bartow slipcover chair-and-a-half, natural', 1.61, 1.03, 1.04, '2d/2d31850b.jpg', 'Stone & Beam Bartow Living Room Chair with Slipcover, 60"W, Natural'),
    ('B07B4D86WF', 'ottoman', 'Bartow slipcover ottoman, stone', 1.39, 0.66, 0.46, '03/0356d052.jpg', 'Stone & Beam Bartow Ottoman with Slipcover, 54"W, Grey'),
    ('B075Z8KXW7', 'coffee_table', 'Coastal Breeze coffee table, white and natural', 1.40, 0.80, 0.41, '9b/9b36b329.jpg', 'Stone & Beam Coastal Breeze Rustic Farmhouse Coffee Table, 55.1"W, Natural and White'),
    ('B075Z96L53', 'side_table', 'Coastal Breeze side table, white and natural', 0.70, 0.70, 0.53, 'e4/e408b9d3.jpg', 'Stone & Beam Coastal Breeze Accent Side End Table, 27.5"W, Natural and White'),
    ('B075Z876TX', 'console_table', 'Coastal Breeze console table, white and natural', 1.40, 0.45, 0.93, '78/78f8fda4.jpg', 'Stone & Beam Coastal Breeze Rustic Farmhouse Console Table, 55.1"W, Natural and White'),
    ('B07B7J2VCD', 'bookshelf', 'Rattan-front bookcase, natural', 0.86, 0.45, 1.93, '50/50f364cf.jpg', 'Stone & Beam Casual Wood Bookcase, 34"W, Natural Rattan'),
    ('B07B4SF23K', 'rug', 'Geometric wool rug, pale blue', 2.45, 1.54, 0.01, '6b/6b8442fb.jpg', 'Rivet Geometric Wool Area Rug, 5 x 8 Foot, Blue, Ivory'),
    ('B073P2WNLJ', 'floor_lamp', 'Column floor lamp, white shade', 0.42, 0.42, 1.48, 'ae/ae5244ce.jpg', 'Stone & Beam Glass Column Living Room Standing Floor Lamp With Light Bulb and Linen Shade, 59"H, Brushed Nicke'),
    ('B0842H2F7C', 'plant', 'Ceramic planter on a stand, white', 0.36, 0.36, 0.48, '2b/2ba26eb2.jpg', 'Rivet Mid-Century Ceramic Planter with Stand, 18.9"H, White'),
    ('B07B4ZFC57', 'bed', 'Chase queen bed, wood and rattan', 1.61, 2.18, 1.39, '63/6383bac3.jpg', 'Stone & Beam Chase Casual Wood and Rattan Queen Bed with Headboard, 63"W, Natural'),
    ('B07B4W5RF7', 'nightstand', 'Chase bedside table, rattan shelf', 0.91, 0.43, 0.70, '88/8894eb28.jpg', 'Stone & Beam Chase Casual Wood and Rattan Bedroom Nightstand, 36", Natural'),
    ('B07B4SCM7N', 'dresser', 'Chase dresser, natural wood', 1.58, 0.47, 0.89, '80/80d50405.jpg', 'Stone & Beam Chase Casual Wood Dresser, 62" W, Natural'),
    ('B07B4ZFC5H', 'dresser', 'Chase tall chest, natural wood', 0.97, 0.48, 1.32, 'b6/b62079e1.jpg', 'Stone & Beam Chase Casual Wood Bedroom Dresser, 38" W, Natural'),
    ('B071SHLFH6', 'rug', 'Wave-pattern rug, blue', 2.14, 1.54, 0.01, 'f4/f4a80d49.jpg', 'Rivet Modern Wave Cosmopolitan Area Rug, 5 x 7 Foot, Blue'),
    ('B07P6JTZ1K', 'sofa', 'Charles roll-arm sofa, stone grey', 2.35, 0.97, 0.95, 'ae/ae7d2217.jpg', 'Stone & Beam Charles Classic Oversized Upholstered Sofa, 92"W, Stone'),
    ('B07P5LM3KH', 'armchair', 'Charles roll-arm armchair, stone grey', 1.03, 0.97, 0.97, '65/65d549a6.jpg', 'Stone & Beam Charles Classic Oversized Upholstered Chair, 39" W, Stone'),
    ('B07P5LKL27', 'ottoman', 'Charles ottoman, stone grey', 0.72, 0.66, 0.51, '8f/8f4cc423.jpg', 'Stone & Beam Charles Classic Oversized Upholstered Ottoman, 31"W, Stone'),
    ('B07DBHC337', 'armchair', 'Rolled-arm club chair, brown leather look', 0.85, 0.89, 0.93, '97/970f0802.jpg', 'Ravenna Home Classic Rolled Arm Nailedhead Faux Leather Accent Chair, 35"W, Brown'),
    ('B07DBFQFGS', 'coffee_table', 'Dora coffee table, dark cherry', 0.94, 0.53, 0.46, 'cd/cdb90403.jpg', 'Ravenna Home Dora Classic Shelf Storage Wood Coffee Table, 37"W, Dark Cherry'),
    ('B07DBDX5ZB', 'side_table', 'Dora side table, dark cherry', 0.56, 0.50, 0.56, '8f/8f279ded.jpg', 'Ravenna Home Dora Class Shelf Storage Wood Side End Table Nightstand, 22"W, Dark Cherry'),
    ('B07DBFG1BH', 'console_table', 'Dora console table, dark cherry', 1.19, 0.39, 0.74, '7e/7ea0fa62.jpg', 'Ravenna Home Dora Classic Shelf Storage Wood Console EntryTable, 47.5"W, Dark Cherry'),
    ('B0824FRXW8', 'floor_lamp', 'Traditional floor lamp, dark bronze', 0.36, 0.36, 1.49, 'f2/f2fc04a4.jpg', 'Ravenna Home Traditional Metal Floor Lamp with Stacked Oval Accents, LED Bulb Included, 58.5"H, Dark Bronze'),
    ('B07HSF58JY', 'rug', 'Distressed Persian rug, blue', 2.45, 1.53, 0.01, '72/7298f855.jpg', 'Rivet Modern Distressed Persian Area Rug, 5 x 8 Foot, Blue Multicolor'),
    ('B075X29QYK', 'bed', 'Newport queen bed, arched headboard', 1.61, 2.38, 1.25, '0d/0dd1f33a.jpg', 'Stone & Beam Newport Arced Headboard Queen Bed, 63"W, Oak'),
    ('B075X38PZ7', 'nightstand', 'Newport nightstand, toffee oak', 0.59, 0.46, 0.60, 'd6/d6954826.jpg', 'Stone & Beam Newport Nightstand End Table, 22"W, Toffee Oak'),
    ('B071ZHY8X9', 'dresser', 'Newport three-drawer dresser, toffee oak', 0.86, 0.41, 0.84, '56/56a703fb.jpg', 'Stone & Beam Newport 3-Drawer Bedroom Dresser, 34"W, Toffee Oak'),
    ('B07DBHPK22', 'bench', 'Galer tufted storage bench, charcoal', 1.05, 0.42, 0.47, '97/97c3c2c9.jpg', 'Ravenna Home Galer Tufted Nailhead Lift Top Storage Bedroom and Entryway Bench, 15.75"W, Charcoal Grey'),
    ('B0828FBDV7', 'floor_lamp', 'Swing-arm floor lamp, bronze', 0.32, 0.42, 1.60, 'da/da1d6d60.jpg', 'Ravenna Home Single-Light Swing-Arm Floor Lamp with Beige Linen Shade, LED Bulb Included, 63"H, Dark Bronze'),
    ('B076NNYB3P', 'dining_table', 'Dunbar dining table, dark oak', 1.98, 1.02, 0.76, 'ff/ff519006.jpg', 'Stone & Beam Dunbar Wood Dining Room Kitchen Table, 78"L, Oak Finish'),
    ('B075YMW6W9', 'chair', 'Dunbar ladderback chair, dark oak', 0.59, 0.59, 0.98, '08/0850bf68.jpg', 'Stone & Beam Dunbar Modern Dining Room Set of 2 Ladderback Side Kitchen Chairs, 38 Inch Height, Oak Wood'),
    ('B075YQXQ5C', 'cabinet', 'Dunbar buffet, dark oak', 1.40, 0.48, 0.91, 'b3/b3ab5b5e.jpg', 'Stone & Beam Dunbar Modern Wood Buffet, 55"W, Oak'),
    ('B07DBGJ5PM', 'console_table', 'Angela console table, white, turned legs', 1.19, 0.39, 0.81, '12/127dae7d.jpg', 'Ravenna Home Angela Modern Turned Wood Leg Console Entryway Table, 47.2"W, White'),
    ('B07DBDZ4SG', 'bench', 'Entryway storage bench, white', 1.20, 0.36, 0.45, '76/76d4c0db.jpg', 'Ravenna Home Upholstered Entryway Cushioned Storage Bench - 47 1/4-Inch, White'),
    ('B0719SX5P1', 'rug', 'Interlocking-rings runner, beige', 2.28, 0.68, 0.01, 'a6/a627670c.jpg', 'Stone & Beam Contemporary Interlocking Rings Wool Runner Rug, 2\' 3" x 7\' 6", Beige'),
    ('B082VM4LMQ', 'sofa', 'Alonzo velvet sofa, sapphire', 2.04, 0.96, 0.90, '4d/4d32df08.jpg', 'Rivet Alonzo Contemporary Velvet Sofa Couch, 80"W, Blue'),
    ('B082VLYP6B', 'armchair', 'Alonzo velvet armchair, sapphire', 0.99, 0.96, 0.90, 'ff/ff78c30a.jpg', 'Rivet Alonzo Contemporary Velvet Livingroom Accent Chair, 39"W, Blue'),
    ('B075X4JB1T', 'armchair', 'Clover brass-arm chair, blush velvet', 0.70, 0.85, 0.82, 'c3/c3f52e7a.jpg', 'Rivet Clover Modern Glam Velvet Brass Arm Accent Chair, 27.25"W, Navy'),
    ('B07B4FZXL1', 'ottoman', 'Cliff tufted velvet pouf, navy', 1.00, 1.00, 0.40, 'ee/ee8c5332.jpg', 'Rivet Cliff Mid-Century Modern Round Tufted Velvet Pouf Ottoman, 39"W, Navy'),
    ('B074KLRCPW', 'coffee_table', 'Round glass coffee table, gold', 0.91, 0.91, 0.47, '3f/3f8bc565.jpg', 'Rivet Modern Round Glass and Gold Coffee Table, 36"W, Gold Finish'),
    ('B074KN35MV', 'side_table', 'Geometric side table, gold', 0.39, 0.39, 0.58, 'd7/d77a3a50.jpg', 'Rivet Geometric Modern Glass and Metal Side End Table Stand, 15.6" W, Gold Finish'),
    ('B07B7J5BC2', 'tv_stand', 'Alan media console, washed navy and gold', 1.57, 0.48, 0.72, 'bd/bd6f83c4.jpg', 'Stone & Beam Alan Casual Wood Media TV Console Table, 62"W, Washed Navy and Gold'),
    ('B07B7GZTNR', 'bookshelf', 'Alan bookcase, washed navy and gold', 0.58, 0.47, 1.73, 'f2/f2343a20.jpg', 'Stone & Beam Alan Casual Wood Bookcase, 23", Navy and Gold'),
    ('B07HKF28GD', 'floor_lamp', 'Slim floor lamp, satin gold', 0.34, 0.34, 1.54, '12/127a2525.jpg', 'Rivet Contemporary Standing Floor Lamp with LED Light Bulb, 58.5"H, Satin Gold Finish'),
    ('B0732HNJFL', 'rug', 'Abstract wool rug, navy and ivory', 2.45, 1.52, 0.00, '48/48df294b.jpg', 'Rivet Abstract Reflections Modern Wool Area Rug, 5 x 8 Foot, Navy Blue'),
    ('B07B4SCPH1', 'bed', 'Catelyn queen bed, navy velvet and brass', 1.72, 2.17, 1.23, '34/348c52d6.jpg', 'Rivet Catelyn Brushed Brass Queen Bed with Headboard, 67"W, Blue'),
    ('B07B4SBZJJ', 'nightstand', 'Gould nightstand, washed navy and gold', 0.66, 0.41, 0.66, 'c4/c43913df.jpg', 'Stone & Beam Gould Contemporary Wood Bedside Nightstand Table, 16", Washed Navy and Gold'),
    ('B07B4VXZZC', 'dresser', 'Gould dresser, washed navy and gold', 1.05, 0.46, 0.99, '69/69416ca0.jpg', 'Stone & Beam Gould Contemporary Wood Bedroom Dresser Chest, 18", Washed Navy and Gold'),
    ('B07B7N6JH3', 'side_table', 'Marble nesting tables, gold', 0.51, 0.40, 0.55, '9e/9e535d9b.jpg', 'Rivet Nesting Side Table, Set of 2, Circular Modern, Marble and Gold'),
    ('B07DTLF7DN', 'floor_lamp', 'Globe floor lamp, gold', 0.25, 0.48, 1.50, 'a6/a645c7c6.jpg', 'Rivet Modern Gold Globe Floor Lamp 59.25" H, Integrated LED light'),
    ('B072LXZ653', 'rug', 'Faded-lines rug, beige and silver', 2.45, 1.53, 0.01, 'fd/fdcc6da8.jpg', "Rivet Leather and Metallic Faded Lines Rug, 5' x 8', Beige & Silver"),
    ('B07VNLB6Z4', 'sofa', 'Ribe three-seat sofa, grey', 2.00, 0.92, 0.87, 'ec/ecacca91.jpg', 'An Amazon Brand - Movian Ribe - 3 Seater Sofa - 92 x 200 x 85 cm (L x W x H) - Grey'),
    ('B07VMLFMZB', 'armchair', 'Galga wingback armchair, dark grey', 0.98, 0.90, 1.05, '49/49e6d08f.jpg', "Marque Amazon - Movian Galga - Chaise d'appoint en tissu Malmo, 90 x 105 x 98 cm (longueur x largeur x hauteur"),
    ('B07VPNWY95', 'ottoman', 'Galga footstool, dark grey', 0.53, 0.53, 0.41, '38/38fc3c83.jpg', 'Marque Amazon - Movian Galga - Repose-pieds en tissu Malmo, 53 x 53 x 42 cm (longueur x largeur x hauteur), Gr'),
    ('B01NCOR0VZ', 'coffee_table', 'Aggol lift-top coffee table, white', 1.00, 0.50, 0.44, 'bc/bcfff597.jpg', 'Movian Aggol Lift-Top Coffee Table with 1-Shelf, 50 x 100 x 44cm, White'),
    ('B07GFFPKBG', 'tv_stand', 'Aggol TV stand, white and oak', 1.60, 0.42, 0.52, '73/73c99a34.jpg', 'Movian Aggol 2-Door 2-Drawer TV Stand, 160 x 40 x 52cm, White/Brown Oak-Effect'),
    ('B07QTB45S5', 'bookshelf', 'Martin bookcase, oak and black steel', 0.77, 0.35, 1.85, 'd0/d0f3d7bc.jpg', 'An Amazon Brand - Movian Martin - Oak Effect Bookcase - 35cm L x 77cm W x 185cm H'),
    ('B0825D873D', 'floor_lamp', 'Striped floor lamp, black and blond wood', 0.38, 0.38, 1.48, 'da/da6766fd.jpg', 'Rivet Scandinavian Striped Floor Lamp, LED Bulb Included, 58.25"H, Black and Blond Wood'),
    ('B071777YL9', 'rug', 'Diamond tassel wool rug, ivory', 2.75, 1.52, 0.02, '0b/0b802d13.jpg', "Rivet Diamond Trellis Tassel Wool Rug, 5' x 8', Ivory"),
    ('B07GFRX4WZ', 'bed', 'Havel double bed with drawers, oak', 1.62, 1.96, 0.80, '94/94fd04a6.jpg', 'Marque Amazon - Movian - Cadre de lit double avec tête de lit et 4 tiroirs Havel Modern, 196 x 162,2 x 80, Eff'),
    ('B07D42T6CX', 'nightstand', 'Havel bedside table, oak and white', 0.43, 0.35, 0.45, 'c9/c927f8ab.jpg', 'Movian Havel 2-Drawer Bedside Table, 35 x 43 x 45cm, Light Brown Oak-Effect/White'),
    ('B07GFFKRXS', 'dresser', 'Havel four-drawer chest, oak and white', 0.99, 0.40, 0.82, '9e/9edd0157.jpg', 'Movian Havel Chest of 4 Drawers, 40 x 100 x 82cm, Light Brown Oak-Effect/White'),
    ('B07GFRKNR1', 'wardrobe', 'Inari wardrobe, oak', 0.99, 0.58, 1.81, '30/30683c12.jpg', 'Movian Inari Modern 2-Door 2-Drawer Wardrobe, 100 x 57 x 180 cm, Light Brown Oak-Effect'),
    ('B07Q9CS3YC', 'rug', 'Trellis shag rug, beige', 2.35, 1.50, 0.02, '9c/9cfc0468.jpg', 'Amazon Basics Modern Plush Moroccan Trellis Shag Area Rug - 5x8 (4\'11" x 7\'8"), Beige'),
    ('B079HXWXXD', 'desk', 'Havel desk, oak and white', 0.98, 0.49, 0.72, 'cc/cc3664a2.jpg', 'Movian Havel 1-Drawer Desk with Storage Compartment, 50 x 100 x 75cm, Light Brown Oak-Effect/White'),
    ('B084T7LRRQ', 'chair', 'Arendsee desk chair, white', 0.55, 0.55, 0.93, '3f/3f789b57.jpg', 'Movian Arendsee, Desk Chair, 55 x 48.5 x 93 cm (L x W x H), White'),
    ('B07RGR6XYT', 'bed', 'Twin-over-twin bunk bed, grey', 2.01, 1.11, 1.21, 'c1/c185460a.jpg', 'Max & Finn Twin Floor Bunk Bed, Gray'),
    ('B07PJTHFFW', 'bookshelf', 'Toy storage bookcase, white', 0.79, 0.38, 0.62, '67/67383006.jpg', "AmazonBasics Children's Multi-Functional Bookcase and Toy Storage Bin - White"),
    ('B07YBHB9QS', 'other', "Kids' table with two chairs, white", 0.65, 0.67, 0.50, 'f3/f3d683b0.jpg', 'AmazonBasics Solid Wood Kiddie Table Set with Two Chairs, White'),
    ('B07YBHFPWR', 'desk', "Kids' desk and chair, white", 0.60, 0.46, 0.67, 'ba/ba9872cd.jpg', 'AmazonBasics Solid Wood Kid Desk and Chair, White'),
    ('B07HSFBRY5', 'rug', 'Colour-block rug, coral and grey', 2.45, 1.53, 0.02, '66/66811672.jpg', 'Rivet Modern Color-Block Area Rug, 5 x 8 Foot, Multicolor'),
    ('B07TPMXMBF', 'armchair', "Kids' recliner, cream", 0.68, 0.71, 0.70, 'd0/d0c67dd4.jpg', 'AmazonBasics LeatherSoft Kids/Youth Recliner with Armrest Storage, 5+ Age Group, Beige'),
    ('B082VGM164', 'sofa', 'Archer patio sofa, grey steel', 1.74, 0.79, 0.86, 'f2/f2ff12f5.jpg', 'Ravenna Home Archer Steel-Framed Outdoor Patio Plush 3-Seater Sofa with Removable, Water-Resistant Cushions, 6'),
    ('B082VFPXGJ', 'armchair', 'Archer patio lounge chair, grey steel', 0.65, 0.79, 0.86, '38/387625f9.jpg', 'Ravenna Home Archer Steel-Framed Outdoor Patio Deep-Seat Chairs, Set of 2, 31"W, Gray'),
    ('B082VSYX39', 'coffee_table', 'Archer patio coffee table, grey steel', 0.97, 0.56, 0.46, '6e/6eb7f7cc.jpg', 'Ravenna Home Archer Outdoor Patio Steel Coffee Table with Panel Top, 38"W, Gray'),
    ('B082VT4GGJ', 'side_table', 'Archer patio side table, grey steel', 0.51, 0.51, 0.51, '9b/9b83655f.jpg', 'Ravenna Home Archer Outdoor Patio Steel Side Table with Panel Top, 20"W, Gray'),
    ('B07MBFDHRY', 'stool', 'Ceramic garden stool, charcoal', 0.35, 0.35, 0.39, 'e1/e1d773f7.jpg', 'Ravenna Home Clover-Pattern Ceramic Garden Stool or Side Table, 16"H, Grey'),
    ('B07ZVM6QMT', 'plant', 'Wood garden planter, square', 0.45, 0.45, 0.39, '45/454d252b.jpg', 'AmazonBasics Recycled Wood Square Garden Planter - 17.75" x 17.75"'),
    ('B082VSXML3', 'dining_table', 'Archer patio dining table, grey steel', 1.68, 0.91, 0.73, '3d/3d7aaf17.jpg', 'Ravenna Home Archer Outdoor Patio Steel Dining Table with Panel Top, 66.14"W, Gray'),
    ('B082VFZD5K', 'chair', 'Archer patio dining chair, grey steel', 0.58, 0.62, 0.89, 'a3/a389748f.jpg', 'Ravenna Home Archer Steel-Framed Outdoor Patio Dining Chairs, Set of 2, 35"H, Gray'),
]
# (id, class, label, w, d, h): the shape the rest of the code reads.
PIECES: list[tuple[str, str, str, float, float, float]] = [row[:6] for row in _ROWS]
BY_ID = {row[0]: row for row in PIECES}
_FILES = {row[0]: row[6:] for row in _ROWS}
SOURCE = "Amazon Berkeley Objects (CC BY 4.0)"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
BUCKET = "https://amazon-berkeley-objects.s3.amazonaws.com"
# Models the dataset drew too large, by the same factor in every direction: they are scaled by
# this when cached, to the size their listing gives. The sizes in _ROWS are after scaling.
_SCALE: dict[str, float] = {
    "B071FJR46L": 0.78,  # Glenwood nightstand: 0.77 m wide as drawn, 24 in (0.61 m) as sold
    "B071FJR3S6": 0.92,  # Glenwood dresser: 1.65 m wide as drawn, 60 in (1.52 m) as sold
    "B071ZHY8X9": 0.69,  # Newport dresser: 1.25 m wide as drawn, 34 in (0.86 m) as sold
}

# The looks the catalog covers, in the order they are offered.
STYLES = ["Mid-century modern", "Farmhouse", "Industrial", "Coastal", "Traditional", "Glam", "Scandinavian", "Kids", "Outdoor"]

# Rooms' worth of furniture that was designed to go together. A room is furnished from ONE set.
# `pieces` are in the order they matter: the first few make the room, the rest are extras.
# `style` is one of STYLES: sets of one style furnish a whole house that hangs together.
SETS: list[dict] = [
    {"id": "sloane_living", "name": "Sloane living room", "style": "Mid-century modern", "rooms": "living room, family room",
     "look": "Pebble-grey tufted seating on tapered walnut legs, walnut tables, a jute rug and a brass lamp.",
     "pieces": ["B072FVHS9G", "B0714QGLSD", "B072ZN4NDS", "B07BVHKPFS", "B07B4WH5K3", "B07GDZ8HN6", "B07HK5HDPZ", "B071W5VJF4", "B07HSCJZQM", "B07QC6X16K"]},
    {"id": "aiden_lounge", "name": "Aiden leather lounge", "style": "Mid-century modern", "rooms": "living room, family room, den",
     "look": "Cognac leather sofa and chair with a round walnut coffee table, brass nesting tables and a jute rug.",
     "pieces": ["B075X2WN11", "B075X2WN27", "B07GDSF3MR", "B07BVHKPFS", "B07B4WH5K3", "B07B7ND7YH", "B07HK5HDPZ", "B07QC6X16K"]},
    {"id": "jonathan_bedroom", "name": "Jonathan walnut bedroom", "style": "Mid-century modern", "rooms": "bedroom (queen bed)",
     "look": "Walnut queen bed with matching nightstands and dresser, brass hardware, a small jute rug.",
     "pieces": ["B07B4SBZFN", "B07B4W2FFY", "B07B4SBZDV", "B07B4WKMB1", "B07B4YZWRK", "B07HK5HDPZ"]},
    {"id": "hillmoor_bedroom", "name": "Hillmoor light bedroom", "style": "Mid-century modern", "rooms": "bedroom (queen bed)",
     "look": "Light-grey upholstered queen bed, grey-and-walnut nightstand, walnut tall dresser, a jute rug.",
     "pieces": ["B07B4YXNR3", "B072ZMHBKQ", "B07B4YZWRK", "B07B4WKMB1", "B07QC6X16K"]},
    {"id": "west_kids_room", "name": "West kid's room", "style": "Mid-century modern", "rooms": "small bedroom, child's room (twin bed)",
     "look": "Low espresso pine twin bed with a dark-oak nightstand and chest, and a small writing desk.",
     "pieces": ["B0856FL9HR", "B07177355J", "B071SHBTM5", "B075ZBW1SN", "B075Z8M22B", "B07B4WKMB1"]},
    {"id": "federal_dining", "name": "Federal dining room", "style": "Mid-century modern", "rooms": "dining room, kitchen island",
     "look": "Walnut dining table and sideboard with grey upholstered chairs; grey counter stools for an island.",
     "pieces": ["B07B86VTX4", "B07B78LXYF", "B07B79WSKT", "B07TSD3DYP", "B07B4WH5K3"]},
    {"id": "anne_office", "name": "Anne walnut office", "style": "Mid-century modern", "rooms": "office, study",
     "look": "Walnut desk with its filing cabinet, a grey swivel chair, a ladder bookcase and a brass lamp.",
     "pieces": ["B07B7DL32P", "B075Z8M22B", "B07B7JY9C5", "B07HSCJZQM", "B07HK5HDPZ", "B07B4WKMB1"]},

    {"id": "parson_living", "name": "Parson farmhouse living room", "style": "Farmhouse", "rooms": "living room, family room",
     "look": "Beige roll-arm sofa and armchair on turned legs, honey-oak plank tables, a kilim-pattern rug and a wood-base lamp.",
     "pieces": ["B07B4MG9HX", "B07B4LZP9Q", "B074KLN3HW", "B074KMVDRS", "B07B4WKN5K", "B074KLFZRM", "B07DT153M1", "B07B4M68LW", "B074KLN3HX"]},
    {"id": "parson_dining", "name": "Parson farmhouse dining room", "style": "Farmhouse", "rooms": "dining room, eat-in kitchen",
     "look": "Honey-oak trestle table and sideboard with cream upholstered chairs, on a kilim-pattern rug.",
     "pieces": ["B075YMW6VR", "B075YPTDT5", "B075YQ8Q3T", "B07B4WKN5K"]},
    {"id": "bateman_bedroom", "name": "Bateman rustic bedroom", "style": "Farmhouse", "rooms": "bedroom (queen bed)",
     "look": "Reclaimed-wood queen bed with a tall plank headboard, matching dressers, brass-legged nightstands and a linen bench.",
     "pieces": ["B075Z8PMML", "B075Z8GZ9X", "B075Z8THYY", "B07B4WR39P", "B075Z93N8N", "B075QDQXW1", "B07DT153M1"]},

    {"id": "glenwood_living", "name": "Glenwood industrial living room", "style": "Industrial", "rooms": "living room, den, loft",
     "look": "Dark-brown leather seating with dark-oak tables on steel sled bases, a distressed grey rug and a bronze pharmacy lamp.",
     "pieces": ["B07QBMQ4LN", "B07QHYD2KF", "B074KMJ96Q", "B074KMW499", "B0732HV63Q", "B074KMZGTC", "B07HKGKSMM", "B07QGSZ79Z", "B074KKXLK1"]},
    {"id": "glenwood_bedroom", "name": "Glenwood industrial bedroom", "style": "Industrial", "rooms": "bedroom (queen bed)",
     "look": "Dark-oak queen bed, nightstands and dressers on steel bases, with an ivory distressed rug.",
     "pieces": ["B071FJR4FW", "B071FJR46L", "B071FJR3S6", "B0735RJPDH", "B071FJR3SR", "B07HKGKSMM"]},
    {"id": "glenwood_dining", "name": "Glenwood industrial dining room", "style": "Industrial", "rooms": "dining room, eat-in kitchen",
     "look": "Dark-oak table on a steel frame with tan leather chairs, and a low walnut-and-steel bookcase as a sideboard.",
     "pieces": ["B075YMW6LH", "B07B7B244W", "B075ZBW1RR", "B072PXBV88"]},
    {"id": "elias_office", "name": "Elias industrial office", "style": "Industrial", "rooms": "office, study",
     "look": "Wood-and-steel desk on castors, a saddle-leather chair, open steel bookcases and a black shelf lamp.",
     "pieces": ["B07B7DMLPL", "B07BZQPQM8", "B075Z6YS1Z", "B07HK8NDXS", "B075ZBW1RR", "B072PXBV88"]},

    {"id": "coastal_living", "name": "Coastal Breeze living room", "style": "Coastal", "rooms": "living room, family room, sunroom",
     "look": "White slipcovered seating with whitewashed X-frame tables, a pale blue rug and a rattan-front bookcase.",
     "pieces": ["B07B4MKFR5", "B07B4MN5P2", "B075Z8KXW7", "B075Z876TX", "B07B4SF23K", "B075Z96L53", "B073P2WNLJ", "B07B4D86WF", "B07B7J2VCD", "B0842H2F7C"]},
    {"id": "chase_bedroom", "name": "Chase rattan bedroom", "style": "Coastal", "rooms": "bedroom (queen bed)",
     "look": "Natural-wood queen bed with rattan panels, rattan-shelf bedside tables and matching chests, on a blue wave rug.",
     "pieces": ["B07B4ZFC57", "B07B4W5RF7", "B07B4SCM7N", "B071SHLFH6", "B07B4ZFC5H", "B073P2WNLJ"]},

    {"id": "charles_living", "name": "Charles traditional living room", "style": "Traditional", "rooms": "living room, family room, den",
     "look": "Stone-grey roll-arm sofa and armchair with dark-cherry tables, a brown club chair, a blue Persian rug and a bronze lamp.",
     "pieces": ["B07P6JTZ1K", "B07P5LM3KH", "B07DBFQFGS", "B07DBFG1BH", "B07HSF58JY", "B07DBDX5ZB", "B0824FRXW8", "B07DBHC337", "B07P5LKL27"]},
    {"id": "newport_bedroom", "name": "Newport traditional bedroom", "style": "Traditional", "rooms": "bedroom (queen bed)",
     "look": "Queen bed with an arched upholstered headboard, toffee-oak nightstands and dresser, a tufted bench and a swing-arm lamp.",
     "pieces": ["B075X29QYK", "B075X38PZ7", "B071ZHY8X9", "B07HSF58JY", "B07DBHPK22", "B0828FBDV7"]},
    {"id": "dunbar_dining", "name": "Dunbar traditional dining room", "style": "Traditional", "rooms": "dining room",
     "look": "Dark-oak dining table and buffet with cushioned ladderback chairs, on a blue Persian rug.",
     "pieces": ["B076NNYB3P", "B075YMW6W9", "B075YQXQ5C", "B07HSF58JY"]},
    {"id": "classic_entry", "name": "Classic white entryway", "style": "Traditional", "rooms": "entry, hallway, mudroom",
     "look": "A white console table on turned legs, a storage bench with cubbies and a beige runner.",
     "pieces": ["B07DBGJ5PM", "B07DBDZ4SG", "B0719SX5P1"]},

    {"id": "alonzo_living", "name": "Alonzo glam living room", "style": "Glam", "rooms": "living room, lounge",
     "look": "Sapphire velvet sofa and armchair, a gold-and-glass coffee table, a blush accent chair and washed-navy storage with gold pulls.",
     "pieces": ["B082VM4LMQ", "B082VLYP6B", "B074KLRCPW", "B07B7J5BC2", "B0732HNJFL", "B074KN35MV", "B07HKF28GD", "B075X4JB1T", "B07B4FZXL1", "B07B7GZTNR"]},
    {"id": "catelyn_bedroom", "name": "Catelyn glam bedroom", "style": "Glam", "rooms": "bedroom (queen bed)",
     "look": "Navy velvet queen bed on brass legs, washed-navy nightstands and dresser with gold ring pulls, marble nesting tables and a gold globe lamp.",
     "pieces": ["B07B4SCPH1", "B07B4SBZJJ", "B07B4VXZZC", "B072LXZ653", "B07DTLF7DN", "B07B7N6JH3"]},

    {"id": "ribe_living", "name": "Ribe Scandinavian living room", "style": "Scandinavian", "rooms": "living room, small living room",
     "look": "Grey sofa on pale wood legs, a wingback armchair and footstool on black wire legs, white-and-oak storage and an ivory diamond rug.",
     "pieces": ["B07VNLB6Z4", "B07VMLFMZB", "B01NCOR0VZ", "B07GFFPKBG", "B071777YL9", "B0825D873D", "B07VPNWY95", "B07QTB45S5", "B0842H2F7C"]},
    {"id": "havel_bedroom", "name": "Havel Scandinavian bedroom", "style": "Scandinavian", "rooms": "bedroom (double bed)",
     "look": "Pale-oak double bed with storage drawers, oak-and-white bedside tables and chest, an oak wardrobe and a beige trellis rug.",
     "pieces": ["B07GFRX4WZ", "B07D42T6CX", "B07GFFKRXS", "B07Q9CS3YC", "B07GFRKNR1", "B0825D873D"]},
    {"id": "havel_office", "name": "Havel Scandinavian office", "style": "Scandinavian", "rooms": "office, study, desk corner",
     "look": "Oak-and-white desk on tapered legs, a white shell chair, an open oak-and-steel bookcase and a striped wood lamp.",
     "pieces": ["B079HXWXXD", "B084T7LRRQ", "B07QTB45S5", "B0825D873D", "B07Q9CS3YC"]},

    {"id": "kids_playroom", "name": "Kids' bunk room", "style": "Kids", "rooms": "child's room, playroom",
     "look": "Grey twin-over-twin bunk bed with white play furniture: toy storage, a desk, a table with two chairs, and a colour-block rug.",
     "pieces": ["B07RGR6XYT", "B07PJTHFFW", "B07YBHFPWR", "B07HSFBRY5", "B07YBHB9QS", "B07TPMXMBF"]},

    {"id": "archer_patio", "name": "Archer patio", "style": "Outdoor", "rooms": "patio, deck, balcony, sunroom",
     "look": "Grey steel-framed sofa and lounge chairs with pale cushions, matching tables, a ceramic stool and a wood planter; a dining table and chairs where there is room.",
     "pieces": ["B082VGM164", "B082VFPXGJ", "B082VSYX39", "B082VT4GGJ", "B07MBFDHRY", "B07ZVM6QMT", "B082VSXML3", "B082VFZD5K"]},
]
SET_BY_ID = {s["id"]: s for s in SETS}
# How many of a piece a set wants (default one).
SET_COUNTS: dict[tuple[str, str], int] = {
    ("jonathan_bedroom", "B07B4W2FFY"): 2,
    ("federal_dining", "B07B78LXYF"): 6,
    ("federal_dining", "B07TSD3DYP"): 3,
    ("parson_dining", "B075YPTDT5"): 6,
    ("bateman_bedroom", "B075Z8GZ9X"): 2,
    ("glenwood_bedroom", "B071FJR46L"): 2,
    ("glenwood_dining", "B07B7B244W"): 6,
    ("chase_bedroom", "B07B4W5RF7"): 2,
    ("newport_bedroom", "B075X38PZ7"): 2,
    ("dunbar_dining", "B075YMW6W9"): 6,
    ("catelyn_bedroom", "B07B4SBZJJ"): 2,
    ("havel_bedroom", "B07D42T6CX"): 2,
    ("archer_patio", "B082VFPXGJ"): 2,
    ("archer_patio", "B082VFZD5K"): 4,
}


def sets_of(piece_id: str) -> list[str]:
    return [s["name"] for s in SETS if piece_id in s["pieces"]]


def style_of(piece_id: str) -> str:
    """The look a piece belongs to: that of the first set it is in. The few pieces in no set are
    spare sizes and seats of the mid-century line the catalog began with."""
    return next((s["style"] for s in SETS if piece_id in s["pieces"]), STYLES[0])


# furniture class -> the default model for that class (the first one listed). For photographed
# pieces. "other" has none: a thing of no known kind is better a plain box than a wrong piece.
MODELS: dict[str, str] = {}
for _id, _cls, *_ in PIECES:
    if _cls != "other":
        MODELS.setdefault(_cls, _id)

HEADERS = {"User-Agent": "furniture-catalog-fetch/0.1"}
TEXTURE_PX = 1024


def thumbnail_url(piece_id: str) -> str:
    """A picture of the model itself, drawn from the same three-quarter view for every piece
    (fixtures/blobs/catalog/, served by the web app). The dataset's own product photos are often
    close-ups or room scenes, which crop badly in a small card; photo_url has those."""
    return f"/fixtures/blobs/catalog/{piece_id}.jpg"


def photo_url(piece_id: str) -> str:
    """The dataset's own main product photo (256 px)."""
    return f"{BUCKET}/images/small/{_FILES[piece_id][0]}"


def product_name(piece_id: str) -> str:
    return _FILES[piece_id][1]


def catalog_dir() -> Path:
    path = Path(env("CATALOG_DIR") or REPO_ROOT / "data" / "catalog") / "abo"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _unpack(glb: bytes) -> tuple[dict, bytes]:
    """A binary glTF's JSON document and its one binary chunk."""
    json_len = struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20:20 + json_len])
    start = 20 + json_len + 8
    return doc, glb[start:start + struct.unpack_from("<I", glb, 20 + json_len)[0]]


def _pack(doc: dict, blob: bytes) -> bytes:
    blob += b"\x00" * (-len(blob) % 4)
    doc["buffers"] = [{"byteLength": len(blob)}]
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(blob), 0x004E4942) + blob
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def shrink_textures(glb: bytes, size: int = TEXTURE_PX) -> bytes:
    """The same GLB with every embedded image at most `size` px a side (JPEG, or PNG if it has
    transparency). Geometry is untouched. A file that is not a plain binary glTF comes back as it is."""
    from PIL import Image

    if glb[:4] != b"glTF":
        return glb
    doc, blob = _unpack(glb)
    views = doc.get("bufferViews", [])
    replaced: dict[int, bytes] = {}
    for image in doc.get("images", []):
        index = image.get("bufferView")
        if index is None:
            continue
        view = views[index]
        raw = blob[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
        picture = Image.open(io.BytesIO(raw))
        if max(picture.size) <= size and image.get("mimeType") == "image/jpeg":
            continue
        picture.thumbnail((size, size))
        out = io.BytesIO()
        if picture.mode in ("RGBA", "LA", "P") and "A" in picture.convert("RGBA").getbands() and picture.convert("RGBA").getextrema()[3][0] < 255:
            picture.convert("RGBA").save(out, "PNG", optimize=True)
            image["mimeType"] = "image/png"
        else:
            picture.convert("RGB").save(out, "JPEG", quality=86)
            image["mimeType"] = "image/jpeg"
        replaced[index] = out.getvalue()
    if not replaced:
        return glb
    packed = bytearray()
    for index, view in enumerate(views):
        data = replaced.get(index) or blob[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
        packed += b"\x00" * (-len(packed) % 4)
        view["byteOffset"], view["byteLength"] = len(packed), len(data)
        packed += data
    return _pack(doc, bytes(packed))


def rescale(glb: bytes, factor: float) -> bytes:
    """The same GLB, `factor` times the size: its scene hangs under one new node that scales it."""
    if glb[:4] != b"glTF":
        return glb
    doc, blob = _unpack(glb)
    scene = doc["scenes"][doc.get("scene", 0)]
    doc.setdefault("nodes", []).append({"name": "to_real_size", "children": scene.get("nodes", []), "scale": [factor] * 3})
    scene["nodes"] = [len(doc["nodes"]) - 1]
    return _pack(doc, blob)


def model_url(piece_id: str) -> str:
    """Where ABO keeps a model: in a folder named by the last character of its id."""
    return f"{BUCKET}/3dmodels/original/{piece_id[-1]}/{piece_id}.glb"


def fetch(piece_id: str) -> bytes:
    """One model as a GLB with browser-sized textures, at real size (_SCALE). Cached on disk.
    Works for any ABO model id, in the catalog or not (catalog_build.py measures candidates)."""
    small = catalog_dir() / f"{piece_id}.1k.glb"
    if small.exists():
        return small.read_bytes()
    original = catalog_dir() / f"{piece_id}.glb"  # kept by earlier versions; no longer written
    if original.exists():
        raw = original.read_bytes()
    else:
        with httpx.Client(timeout=300, headers=HEADERS, follow_redirects=True) as http:
            raw = http.get(model_url(piece_id)).raise_for_status().content
    glb = shrink_textures(raw)
    if piece_id in _SCALE:
        glb = rescale(glb, _SCALE[piece_id])
    small.write_bytes(glb)
    return glb


def fetch_all(workers: int = 8) -> list[str]:
    """Download every catalog model that is not cached yet. Returns what could not be fetched."""
    from concurrent.futures import ThreadPoolExecutor

    def one(piece_id: str) -> str | None:
        try:
            fetch(piece_id)
            return None
        except Exception as e:
            return f"{piece_id}: {e}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [failure for failure in pool.map(one, [row[0] for row in PIECES]) if failure]


class CatalogTo3D:
    def generate(self, image: bytes, segment: Segment, piece: Piece) -> Model3D:
        piece_id = MODELS.get("sofa" if piece.item_class == "sectional" else piece.item_class)
        if piece_id:
            try:
                return Model3D(fetch(piece_id))
            except Exception as e:  # offline, or the file moved: a box is better than nothing
                log.warning("catalog model %s unavailable (%s); using a box", piece_id, e)
        return BoxTo3D().generate(image, segment, piece)


if __name__ == "__main__":  # python -m services.api.objects.providers.catalog : download everything now
    failures = fetch_all()
    for piece_id, _cls, label, *_ in PIECES:
        small = catalog_dir() / f"{piece_id}.1k.glb"
        print(f"{label:52} {small.stat().st_size / 1e6:5.1f} MB" if small.exists() else f"{label:52} MISSING")
    for failure in failures:
        print("FAILED", failure)
