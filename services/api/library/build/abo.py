"""The Amazon Berkeley Objects part of the furniture library: artifacts/items.abo.jsonl.gz.

    python -m services.api.library.build.abo
        Write the file, and print what is in it and what was left out, by reason.
    python -m services.api.library.build.abo explain <id> [<id> ...]
        What the build decides for these products (their index lines), and how their size was settled.
    python -m services.api.library.build.abo facing <id> [<id> ...]
        Fetch these models and measure which way each faces: lines for `_TURN`.

ABO publishes 7,953 products with a 3D model (CC BY 4.0, credit Amazon.com). Nine in ten can
stand, lie or hang in a home. This reads the index the catalog tools keep (data/catalog/abo_meta/,
see objects/providers/catalog_build.py) and writes one line per usable piece, in the shape
library/__init__.py describes. No model is downloaded: a piece's size comes from the dataset's
own measurement of its model, checked against what the listing and the title say.

What is decided here, in the order it is done:

    class    from the product's title first (the dataset's product types are often wrong: a bed
             filed as STORAGE_BOX, a TV stand as CLOTHES_RACK, a sofa as FURNITURE_COVER), then
             its shop category, then its type and size. A product that fits no class is left
             out, never called "other".
    size     the model's extent (x, y, z = w, h, d). Some 130 models are drawn at the wrong scale
             (in inches for centimetres, or as another size of the same design); where the
             listing's three sizes or the title's differ from the model by one factor in every
             direction, the size here is the real one. The model is scaled to it when the piece
             is added (objects/normalize.py).
    sanity   a size that no piece of that class has, a model too heavy for a browser, a model
             with no thickness: left out.
    facing   ABO models face +Z, except some sectionals (`_TURN`). A piece with a `turn` states
             the size of the piece as placed: library/sources.py turns the model first and sizes
             it after, so its w is along the turned model's X.
    words    a short name for the card, the design line, a family key shared by one design's
             colours and sizes, and tags.

The 160 pieces of the hand-checked catalog (objects/providers/catalog.py) keep the class, the
label and the measured size they have there.
"""

from __future__ import annotations

import csv
import gzip
import html
import itertools
import json
import math
import re
import sys
from collections import Counter, defaultdict

from services.api.library import ARTIFACTS, CATEGORIES
from services.api.objects.classes import FURNITURE_CLASSES
from services.api.objects.providers import catalog, catalog_build

OUT = ARTIFACTS / "items.abo.jsonl.gz"
INCH, FOOT = 0.0254, 0.3048
MAX_FACES = catalog_build.MAX_FACES
IMAGES = f"{catalog.BUCKET}/images/small/"


# --- the listings ---------------------------------------------------------------------------------


def _listings(ids: set[str]) -> dict[str, list[dict]]:
    """Every raw listing of the products that have a model, by model id (a product sold in three
    countries has three). The index keeps a few fields of one of them; tags and a good English
    title need the rest."""
    files = [catalog_build.META / f"listings_{h}.json.gz" for h in "0123456789abcdef"]
    if not all(f.exists() for f in files):
        catalog_build.build_index()
    found: dict[str, list[dict]] = defaultdict(list)
    for path in files:
        with gzip.open(path, "rt") as f:
            for line in f:
                if '"3dmodel_id"' not in line:
                    continue
                d = json.loads(line)
                if d.get("3dmodel_id") in ids:
                    found[d["3dmodel_id"]].append(d)
    return found


def _values(listing: dict, field: str, english: bool = True) -> list[str]:
    """A multilingual field's values: the English ones, or all of them."""
    out = []
    for v in listing.get(field) or []:
        value = v.get("value")
        if isinstance(value, str) and value.strip() and (not english or str(v.get("language_tag", "")).startswith("en")):
            out.append(value.strip())
    return out


def _main(listings: list[dict]) -> dict:
    """The listing the index was made from: the US one when there is one."""
    return next((d for d in listings if d.get("domain_name") == "amazon.com"), listings[0])


def _title(row: dict, listings: list[dict]) -> tuple[str, bool]:
    """(the product's title, whether it is English). The US listing of a few products has no
    English title; another country's listing may."""
    main = _main(listings) if listings else {}
    for d in [main] + [d for d in listings if d is not main]:
        names = _values(d, "item_name")
        us = [v.get("value") for v in d.get("item_name") or [] if v.get("language_tag") == "en_US"]
        if us or names:
            title = _clean((us or names)[0])
            return title, _is_english(title)
    title = _clean(row.get("name") or "")
    return title, _is_english(title)


# Words that give a title's language away. The listings' own language tags do not: a Mexican
# listing is tagged Spanish and written in English, a US one tagged English and written in German.
_FOREIGN = re.compile(
    r"\b(?:con|per|di|da|del|della|colore|legno|bianco|nero|grigio|rovere|quercia|tessuto|posti|marrone|avec|pour|des|du|et|à|bois|chêne|blanc|noir|gris|tissu|"
    r"places|para|madera|blanco|negro|roble|plazas|marrón|mit|für|und|aus|im|holz|weiß|schwarz|grau|eiche|med|och)\b|\b[dl]'\w|"
    r"\bde (?:la|l|salle|lit|table|bureau|pie|mesa|noche|comedor|centro|oficina|bar|computadora|estudio)\b", re.I)
_ENGLISH = re.compile(r"\b(?:with|for|and|of|the|inch|inches|by)\b", re.I)


def _is_english(title: str) -> bool:
    return bool(_ENGLISH.search(title)) or not _FOREIGN.search(title)


def _clean(text: str) -> str:
    text = html.unescape(text).replace("\xa0", " ").replace("”", '"').replace("“", '"').replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


# --- brand, design line ---------------------------------------------------------------------------

# "Amazon Brand – ", "Marque Amazon - ", "An Amazon Brand - ": said before the brand, in five languages.
_PREFIX = re.compile(r"^(?:an?\s+)?(?:amazon brand|brand of amazon|marque amazon|marchio amazon|marca amazon|amazon[- ]marke|amazon-märke|amazon(?=\s+(?:movian|alkove)))\s*[–—:,-]*\s*", re.I)
# The one brand under its several spellings.
_BRANDS = {
    "AmazonBasics": "Amazon Basics", "Amazonベーシック(AmazonBasics)": "Amazon Basics", "Amazon Brand - Solimo": "Solimo",
    "UMI": "Umi", "Umi.": "Umi", "UMI. by Amazon": "Umi", "ROSSINI": "Rossini", "KINGSWELL": "Kingswell", "CIRCUIT FITNESS": "Circuit Fitness",
    "POCATOY": "Pocatoy", "sreoci": "Sreoci", "kovalenthor": "Kovalenthor",
}
# How a brand is written at the start of a title, when not as in the brand field.
_BRAND_IN_TITLE = ["AmazonBasics", "Amazon Basics", "AmazonCommercial", "Solimo", "UMI. by Amazon", "UMI.by Amazon", "Umi. Essentials", "UMI. Essentials", "UMI.", "Eono by Amazon", "Iris Ohyama", "Movian", "Alkove", "Rivet", "Stone & Beam", "Ravenna Home", "Ravenna"]


def _brand(row: dict) -> str | None:
    brand = (row.get("brand") or "").strip()
    return _BRANDS.get(brand, brand) or None


def _without_brand(title: str, brand: str | None, raw_brand: str | None) -> str:
    """The title after "Amazon Brand – Rivet ": what the maker calls the piece."""
    text = _PREFIX.sub("", title)
    for name in sorted({n for n in [raw_brand, brand, *_BRAND_IN_TITLE] if n}, key=len, reverse=True):
        if text.lower().startswith(name.lower()) and not text[len(name):len(name) + 1].isalnum():
            text = text[len(name):]
            break
    return text.lstrip(" -–—,.:")


# --- reading a foreign title ----------------------------------------------------------------------

# Some 400 products are listed only in Italian, French, Spanish, German or Swedish. Their kind is
# read from these words: (a pattern, the English for it). Of two that start at the same place in
# a title the one listed first counts, so "divano letto" is a sofa bed and not a sofa.
_GLOSS: list[tuple[str, str]] = [
    (r"materass[oi]|matelas|colch[óo]n(es)?|matratzen?|madrass", "mattress"),
    (r"cadres? de lit|struttur[ae] (del )?letto|giroletto|estructuras? de cama|bettgestell|bettrahmen|sängram", "bed frame"),
    (r"[ée]tag[èe]res? (suspendues?|murales?)|mensol[ae]|estantes? (de pared|flotantes?)|wandregal", "floating shelf"),
    (r"chaises? d'appoint|sedi[ae] d'appoggio|sillas? auxiliar(es)?", "accent chair"),
    (r"testier[ae]|t[êe]te de lit|cabecero|kopfteil|sänggavel", "headboard"),
    (r"divano[- ]letto|divani letto|canap[ée][- ]lit|canap[ée] convertible|sof[áa][- ]cama|schlafsofa|schlafcouch|bäddsoffa", "sofa bed"),
    (r"poltrona letto|schlafsessel|fauteuil convertible|sill[óo]n cama", "sleeper armchair"),
    (r"modulo|module\b|m[óo]dulo|modul\b", "sectional component"),
    (r"divano angolare|angolare|canap[ée] d'angle|sof[áa] (rinconera|esquinero)|rinconera|chaise ?longue|ecksofa|penisola|hörnsoffa", "sectional sofa"),
    (r"2 posti|due posti|2 places|deux places|2 plazas|dos plazas|2[- ]sitzer|zweisitzer|2[- ]sits", "loveseat"),
    (r"divan[oi]|canap[ée]s?|sof[áa]s?\b|couch\b|soffa", "sofa"),
    (r"sedi[ae] (da |per )?(ufficio|scrivania|gaming)|chaises? (de |pour )?(bureau|gaming)|sillas? (de |para )?(oficina|escritorio|gaming|gamer)|silla gaming|b[üu]rostuhl|gaming[- ]stuhl|schreibtischstuhl|kontorsstol", "office chair"),
    (r"poltron[ae]|fauteuils?|sill[óo]n(es)?|butacas?|ohrensessel|sessel|fåtölj", "armchair"),
    (r"sgabell[oi] (da )?bar|tabourets? de bar|taburetes? (de |alto)?\s*bar|barhocker|barstol", "bar stool"),
    (r"poggiapiedi|pouf+s?\b|puf+\b|repose[- ]pieds|reposapi[ée]s|fu(ß|ss)hocker|sitzhocker|fotpall", "ottoman"),
    (r"sgabell[oi]|tabourets?|taburetes?|hocker\b|pall\b", "stool"),
    (r"sedi[ae] (da |per )?(sala da )?pranzo|chaises? de salle [àa] manger|sillas? de comedor|esszimmerst[uü]hle?", "dining chair"),
    (r"sedi[ae]|chaises?|sillas?\b|st[uü]hle?\b|stol(ar)?\b", "chair"),
    (r"tavolin[oi] da (caff[èe]|salotto)|table basse|mesas? (de )?centro|mesas? de caf[ée]|couchtisch|soffbord", "coffee table"),
    (r"comodin[oi]|tables? de (chevet|nuit)|chevets?\b|mesit[ao]s? de noche|mesillas?( de noche)?|nachttisch|nachtkommode|nachtschrank|sängbord", "nightstand"),
    (r"tavolin[oi]|tables? d'appoint|bouts? de canap[ée]|mesas? auxiliar(es)?|beistelltisch|sidobord", "side table"),
    (r"tavol[oi] da pranzo|tavolo allungabile|tables? ([àa]|de) manger|tables? de salle [àa] manger|mesas? de comedor|esstisch|matbord", "dining table"),
    (r"scrivani[ae]|bureaux?\b|escritorios?|schreibtisch|skrivbord|mesas? (de |para )?(ordenador|oficina|estudio|pc|computadora)|computertisch", "desk"),
    (r"scarpier[ae]|meubles? [àa] chaussures|(armarios? )?zapateros?|schuhschrank|schuhregal|skoskåp", "shoe cabinet"),
    (r"cassettier[ae]|commodes?\b|c[óo]modas?|kommode|byrå", "chest of drawers"),
    (r"armadi[oi]?|guardaroba|armoires?|armarios?|kleiderschrank|schrank\b|garderob", "wardrobe"),
    (r"librer[ií]a|scaffal[ei]|biblioth[èe]ques?|[ée]tag[èe]res?|estanter[íi]as?|b[üu]cherregal|standregal|regal\b|bokhylla", "bookcase"),
    (r"vetrin[ae]|vitrin[ae]s?", "display cabinet"),
    (r"credenz[ae]|buffets?\b|aparador(es)?|sideboard|anrichte|madia|skänk", "sideboard"),
    (r"mobile (porta )?tv|porta tv|meubles? tv|muebles? (de |para )?(tv|televisi[óo]n)|mesas? (de |para )?(tv|televisi[óo]n)|tv[- ]schrank|tv[- ]bank|lowboard|tv[- ]bänk", "tv stand"),
    (r"letto a castello|lits? superpos[ée]s|literas?|etagenbett", "bunk bed"),
    (r"lett[oi]\b|lits?\b|camas?\b|bett\b|bettgestell|bettrahmen|säng\b", "bed"),
    (r"panc[ah]e?\b|panchin[ae]|bancs?\b|bancos?\b|sitzbank|bank\b|bänk\b", "bench"),
    (r"lampad[ae] da terra|piantan[ae]|lampadaires?|l[áa]mparas? de pie|stehlampe|stehleuchte|golvlampa", "floor lamp"),
    (r"lampad[ae] da (tavolo|comodino|scrivania)|lampes? (de|à) (table|chevet|bureau|poser)|l[áa]mparas? de (mesa|escritorio|mesita|noche|sobremesa)|tischlampe|tischleuchte|nachttischlampe|bordslampa", "table lamp"),
    (r"lampadari[oi]?|sospension[ei]|suspensions?|lustres?\b|l[áa]mparas? (de techo|colgantes?)|pendelleuchte|h[äa]ngeleuchte|h[äa]ngelampe|deckenleuchte|taklampa|ciondol[oi]|pendente", "pendant light"),
    (r"l[áa]mparas?|lampad[ae]|lampes?\b|leuchte\b", "lamp"),
    (r"tappet[oi]|tapis\b|alfombras?|teppich|matta\b", "rug"),
    (r"specchi[oi]?|miroirs?|espejos?|spiegel|spegel", "mirror"),
    (r"cornic[ei]( portafoto)?|portafoto|cadres?( photos?)?\b|marcos? (de |para )?fotos?|portafotos?|bilderrahmen|fotorahmen", "picture frame"),
    (r"consolle?\b|consolas?\b|konsolentisch|konsole\b", "console table"),
    (r"appendiabiti|stender|percheros?|portants?\b|kleiderst[äa]nder|garderobenst[äa]nder", "garment rack"),
    (r"cuscin[oi]|coussins?|coj[íi]n(es)?|kissen", "pillow"),
    (r"vas[oi]\b|vases?\b|jarr[óo]n(es)?", "vase"),
    (r"fiorier[ae]|jardini[èe]res?|maceter[oa]s?|macetas?|pflanzk[üu]bel|blumentopf", "planter"),
    (r"scatol[ae]|cest[oi]\b|cestin[oi]|paniers?|bo[îi]tes?\b|cestas?\b|cajas?\b|korb\b|aufbewahrungsbox", "storage basket"),
    (r"tavol[oi]\b|mesas?\b|tisch\b|bord\b|tables?\b", "table"),
]
_GLOSS_RE = [(re.compile(rf"\b(?:{pattern})", re.I), english) for pattern, english in _GLOSS]
_WITH_FOREIGN = re.compile(r"\s(?:with|con|avec|mit|med|met|para|pour|per|für)\s", re.I)


def _gloss(title: str) -> str:
    """The English for what a foreign title names. In Italian, French and Spanish the noun comes
    first ("mesa auxiliar", "lampada da terra"), so the leftmost word found is the product; among
    words that start at the same place, the one listed first."""
    head = _WITH_FOREIGN.split(title, maxsplit=1)[0]
    for text in (head, title):
        found = [(m.start(), order, english) for order, (pattern, english) in enumerate(_GLOSS_RE) if (m := pattern.search(text))]
        if found:
            words = [english for _, _, english in sorted(found)]
            # A modular sofa is sold by the piece: say so, and say when the piece is the footstool.
            if "sectional component" in words:
                return "ottoman sectional component" if "ottoman" in words else "sectional component"
            return words[0]
    return ""


# --- class ----------------------------------------------------------------------------------------

# Product types that are not home furnishing. They decide for a product whose title names nothing
# this file knows; a title that does is believed over its type ("Wall Art" filed as CURTAIN).
_NOT_HOME = {
    "SOUND_AND_RECORDING_EQUIPMENT", "ELECTRIC_FAN", "SPORTING_GOODS", "FREESTANDING_SHELTER", "FLAT_SCREEN_DISPLAY_MOUNT", "CURTAIN", "MULTIPORT_HUB",
    "AUTO_ACCESSORY", "AIR_CONDITIONER", "LADDER", "BUILDING_MATERIAL", "ELECTRONIC_CABLE", "PORTABLE_ELECTRONIC_DEVICE_STAND", "EXERCISE_MAT",
    "COMPUTER_ADD_ON", "TOOLS", "PLACEMAT", "JANITORIAL_SUPPLY", "MOUSE_PAD", "PAPER_PRODUCT", "SAUTE_FRY_PAN", "STORAGE_HOOK", "CE_ACCESSORY",
    "DEHUMIDIFIER", "INSTRUMENT_PARTS_AND_ACCESSORIES", "PORTABLE_ELECTRONIC_DEVICE_COVER", "HANDBAG", "DRINK_COASTER", "OFFICE_PRODUCTS",
    "PORTABLE_ELECTRONIC_DEVICE_MOUNT", "ANIMAL_COLLAR", "ACCESSORY_OR_PART_OR_SUPPLY", "UTILITY_CART_WAGON", "COMPUTER", "ROWING_MACHINE",
    "CONSUMER_ELECTRONICS", "BATTERY", "CELLULAR_PHONE_CASE", "ELECTRONIC_ADAPTER", "DRINKING_CUP", "BLANKET", "FITNESS_BENCH", "WIRELESS_ACCESSORY",
    "PUMP_DISPENSER", "CARRYING_CASE_OR_BAG", "FILE_FOLDER", "WRITING_BOARD", "PINBOARD", "BISS",
}

# What a title can name: (kind, weight, pattern). English puts the noun last ("dining room table
# chairs" are chairs, a "storage ottoman bench" is a bench), so the noun that ENDS last in the
# title's head is the product; of two that end together, the one listed first here. A kind that
# starts with "-" is a reason to leave the product out. Weight 2 says what the product is;
# weight 1 is a general word that a more exact one earlier in the title overrides ("recliner
# chair"); weight 0 names a product only when nothing else does ("rack", "box").
_NOUNS: list[tuple[str, int, str]] = [
    # exceptions to the rules below them
    ("decor", 2, r"(pillow|cushion) covers?"),
    ("armchair", 2, r"bean ?bags?( chairs?| loungers?)?"),
    ("plant", 2, r"(plant|flower) ?(pots?|stands?|holders?)|planter (pots?|box(es)?|stands?)"),
    ("bed", 2, r"(day)?bed ?frames?( platforms?)?|platform frames?"),
    ("cabinet", 2, r"desk (high |low |mobile |drawer )*pedestals?"),
    ("-a wall-mounted or hanging holder", 2, r"(hanging|wall[- ]?mount(ed)?|floating|vertical|ceiling) [\w\s-]{0,40}?(planters?|vases?|pots?|cabinets?|shel(f|ves)|racks?|organizers?|units?|baskets?)|(planters?|vases?|pots?) [\w\s-]{0,12}?(wall mount|hanger)|wall (planters?|vases?)"),
    ("-a seat cushion", 2, r"((seat|chair|bench|lounger|chaise|patio|back|outdoor) )+cushions?( sets?)?|cushion sets?"),
    # not home furnishing
    ("-a cover or a spare part", 2, r"covers?|replacement [\w-]+|refills?|pillow inserts?|caster wheels|casters"),
    ("-a mattress alone", 2, r"mattress(es)?( toppers?| pads?| protectors?| foundations?)?|box springs?|bed bases?|bunkie boards?|toppers?"),
    ("-a headboard alone", 2, r"headboards?|footboards?"),
    ("-bedding or a curtain", 2, r"curtains?( panels?)?|drapes?|blinds?|valances?|window (panels?|shades?)|shower liners?|sheets?( sets?)?|duvets?|comforters?|quilts?|bedding|bed ?spreads?|pillow ?cases?|shams?|blankets?"),
    ("-a wall or outdoor light fitting", 2, r"sconces?( lamps?| lights?| fixtures?)?|vanity (lights?|fixtures?)|bath(room)? (bar )?lights?|wall (mount(ed)? )?(lights?|lamps?|lanterns?|fixtures?)|picture lights?|security lights?|flood ?lights?|path ?lights?|string lights?|night ?lights?|flash ?lights?|light strips?|track lights?|porch lights?|post lights?|spot ?lights?|work lights?"),
    ("-an appliance", 2, r"(ceiling |tower |pedestal |box |desk |table |floor |window )?fans?|heaters?|air conditioners?|dehumidifiers?|humidifiers?|purifiers?|vacuums?|microwaves?|refrigerators?|televisions?|speakers?|projectors?|monitors?|soundbars?"),
    ("-an electronics accessory", 2, r"(tv |wall |monitor |ceiling |tilt(ing)? |full[- ]motion |articulating |projector |arm |desk )+mounts?|mounting brackets?|cables?|chargers?|hubs?|adapters?|keyboards?|mouse ?pads?|(laptop|monitor|tablet|phone|speaker|headphone|microphone|tripod|piano|keyboard) (stands?|risers?|arms?|holders?)|power strips?|surge protectors?|extension cords?|remotes?"),
    ("-sports equipment", 2, r"dumbbells?( sets?)?|kettlebells?|barbells?|weights?|(yoga|exercise|workout|fitness|weight|flat|utility) (mats?|balls?|bench(es)?|bikes?|blocks?|racks?)|treadmills?|rowing machines?|trampolines?|bikes?|bicycles?|scooters?|tents?|sleeping bags?|coolers?"),
    ("-a shelter or an umbrella", 2, r"canop(y|ies)|gazebos?|pergolas?|umbrellas?|awnings?|greenhouses?|carports?|sheds?|hammocks?"),
    ("-a tool or hardware", 2, r"(step )?ladders?|step ?stools?|wagons?|hand trucks?|doll(y|ies)|creepers?|tool (sets?|kits?|box(es)?|bags?)|door ?stops?|drawer pulls?|workbench(es)?|sawhorses?|wheelbarrows?|tarps?|(kitchen |prep |commercial )?work tables?|utility carts?|supply carts?|shop stools?"),
    ("-for a pet or a bird", 2, r"(pet|dog|cat|puppy|kitten) [\w-]+|kennels?|crates?|bird ?houses?|bird ?feeders?|bat shelters?|aquariums?|litter box(es)?"),
    ("-kitchenware", 2, r"(sauce ?|fry(ing)? ?|saute |stock ?)?pans?|stock ?pots?|dutch ovens?|skillets?|cookware( sets?)?|kni(fe|ves)|cutting boards?|dish racks?|utensils?|mugs?|cups?|dinnerware|flatware|glassware|coasters?( sets?)?|placemats?|napkins?|trivets?|kettles?|toasters?|blenders?|bakeware|water bottles?|tumblers?|wine glass(es)?|spice racks?|paper towel holders?|aprons?|oven mitts?|table runners?"),
    ("-for the bathroom or the laundry", 2, r"shower [\w-]+|towel (bars?|racks?|rings?|holders?)|towels?|toilet [\w-]+|bath ?(mats?|rugs?)|soap [\w-]+|toothbrush [\w-]+|ironing boards?|drying racks?|plungers?|brooms?|mops?|trash cans?|waste ?baskets?|garbage cans?|recycling bins?"),
    ("-office supplies", 2, r"whiteboards?|(dry[- ]erase|message|memo|bulletin|notice|cork) ?boards?|file folders?|binders?|shredders?|laminators?|notebooks?|planners?|journals?|staplers?|paper trays?|desk (pads?|mats?|organizers?)|monitor risers?|foot ?rests?|(chair|floor|anti-fatigue|standing|comfort) mats?|lap desks?|laptop (tables?|trays?|desks?)|bed trays?"),
    ("-a bag or a case", 2, r"bags?|backpacks?|luggage|suitcases?|(display |carrying |flag display )?cases?|wallets?|totes?( sets?)?|shadow ?box(es)?"),
    ("-a wall shelf or an organizer", 2, r"(corner )?wall shel(f|ves)|corner shel(f|ves)|over[- ]?(the[- ])?door [\w-]+|closet (organizers?|systems?|rods?)|drawer organizers?|shelf liners?|under[- ]?bed [\w-]+|shoe organizers?|ledges?|mail (holders?|organizers?)|key (holders?|racks?)|wall (grids?|organizers?)|hooks?|hangers?"),
    ("-garden equipment", 2, r"fire ?pits?( tables?)?|grills?|compost [\w-]+|raised (garden )?beds?|garden beds?|fountains?|sprinklers?|mowers?|trellis(es)?|fenc(es?|ing)|garden (stakes?|edging|tools?)|rain barrels?|deck box(es)?|hose reels?"),
    ("-a baby or play item", 2, r"strollers?|car seats?|high ?chairs?|play ?(pens?|yards?|mats?|houses?|sets?)|(baby|safety) gates?|changing (pads?|tables?)|bouncers?|walkers?|puzzles?|dolls?|ride[- ]ons?"),
    ("-several different pieces sold as one", 2, r"(dining|bistro|conversation|dinette|pub|patio|bedroom|living room|furniture|table|seating|lounge|chat) sets?|tables? (and|&) (\w+ )?chairs?( sets?)?|\d[- ]?(pc|pcs|pieces?) [\w\s-]{0,40}?(dining|patio|bistro|conversation|furniture|seating|lounge|table|vanity|bedroom)[\w\s-]{0,20}?sets?"),
    # what hangs on a wall or lies on the floor
    ("wall_art", 2, r"wall (art|d[ée]cor|hangings?|sculptures?|plaques?|accents?|signs?|tapestr(y|ies))( d[ée]cor)?|(art )?prints?( i+)?|canvas(es)?|posters?|paintings?|murals?|tapestr(y|ies)|artworks?|photo(graph)?s?|chalkboards?|neon (light )?signs?|macram[ée]|maps?|reprints?|sketch(es)?|lithographs?|patents?|decals?"),
    ("rug", 2, r"rugs?|runners?|door ?mats?|carpets?|kilims?|dhurries?"),
    ("mirror", 2, r"mirrors?"),
    ("wall_clock", 2, r"clocks?"),
    ("frame", 1, r"frames?"),
    # light
    ("floor_lamp", 2, r"floor (lamps?|lights?)|standing lamps?|torchi[eè]res?|tree lamps?|arc lamps?"),
    ("table_lamp", 2, r"(table|desk|task|bedside|accent|buffet|nightstand|touch|bankers?|piano|reading|mini|console)( top)? lamps?|lamp sets?"),
    ("ceiling_light", 2, r"pendants?( lights?| lamps?| fixtures?)?|chandeliers?( lights?| fixtures?)?|ceiling (lights?|lamps?|fixtures?)|(semi[- ]?)?flush[- ]?mounts?( lights?| fixtures?)?|island lights?|hanging (lights?|lamps?|fixtures?)|(pendant|chandelier|ceiling|cage|light) fixtures?"),
    ("lamp", 1, r"lamps?|fixtures?"),
    # seats
    ("stool", 2, r"bar ?stools?|(counter|pub|kitchen|saddle|vanity|drafting|garden|accent|spa|bar|swivel|backless|adjustable)[- ]?(height )?(bar )?stools?|stools?|(bar|counter)[- ]?height (dining |kitchen )?(chairs?|seats?)|bar chairs?"),
    ("chair", 2, r"(desk|office|task|dining|kitchen|side|folding|stack(ing|able)?|guest|executive|gaming|computer|conference|reception|drafting|school|classroom|banquet|parsons?|windsor|bistro|ladder ?back|spindle|cross[- ]?back|x[- ]?back|wishbone|shell|managerial|ergonomic|mesh|racing|kids?|kid's|children'?s?|toddler|resin|plastic)\b[\w\s'/-]{0,45}?\bchairs?"),
    ("armchair", 2, r"arm ?chairs?|wing ?backs?|recliners?|loungers?|gliders?|rockers?|chair[- ]and[- ]a[- ]half|papasans?|berg[eè]res?|adirondacks?|chaises?( lounges?| longues?)?|sleeper (arm)?chairs?|chair[- ]beds?|bed chairs?|cuddlers?|1[- ]seaters?|(accent|arm|club|lounge|living ?room|swivel|rocking|reclining|wing ?back|wing|barrel|slipper|tub|egg|sling|occasional|oversized|armless|floor|deep[- ]seat|papasan|adirondack)\b[\w\s'/-]{0,30}?\bchairs?"),
    ("seat", 1, r"chairs?"),
    ("component", 2, r"sectional components?|modular [\w\s-]{0,30}?(components?|pieces?|units?|modules?)|modules?"),
    ("sofa", 2, r"sofa[- ]?(cum )?beds?|sofa[- ]chaises?|sofas?|couch(es)?|love ?seats?|settees?|\d(\.5)?[- ]?seaters?|(two|three|four|five|six)[- ]seaters?|futons?|sleepers?|daybeds?|sectionals?"),
    ("ottoman", 2, r"ottomans?|poufs?|pouffes?|foot ?stools?|hassocks?"),
    ("bench", 2, r"bench(es)?|banquettes?"),
    # beds
    ("crib", 2, r"cribs?|bassinets?|cradles?|toddler beds?"),
    ("nightstand", 2, r"night ?stands?|bed ?side( tables?| cabinets?| chests?)?|beside tables?|night tables?"),
    ("bed", 2, r"bed ?frames?|bunk ?beds?|beds?|bunks?|trundles?|platform frames?|cots?"),
    # tables
    ("tv_stand", 2, r"(tv|television|media|entertainment)( [\w-]+){0,4}? (stands?|consoles?|centers?|centres?|cabinets?|units?|bench(es)?|chests?|towers?)( tables?| stands?)*|(tv|television|media) (tables?|storage|boards?)|lowboards?"),
    ("coffee_table", 2, r"(coffee|cocktail) tables?"),
    ("console_table", 2, r"(console|sofa|entry|entryway|hall|hallway|foyer) tables?"),
    ("console", 1, r"consoles?"),
    ("side_table", 2, r"(side|end|accent|nesting|drink|martini|tray|snack|lamp|plant|pedestal|bunching|occasional|chair ?side|telephone|stacking|c)[- ]?(end |side )?tables?|(side|end)[/ ](end|side)( tables?)?|nest of tables|tv (dinner )?(folding )?trays?"),
    ("dining_table", 2, r"(dining|kitchen|dinner|pub|bar|bistro|trestle|extension|extendable|counter[- ]?height|gathering|breakfast|drop[- ]?leaf)\b[\w\s'/-]{0,30}?\btables?"),
    ("desk", 2, r"desks?|writing tables?|computer (tables?|workstations?)|workstations?|secretar(y|ies)|vanit(y|ies)( tables?| sets?| desks?)?|dressing tables?|study tables?|drafting tables?"),
    ("table", 1, r"tables?"),
    # storage
    ("dresser", 2, r"dressers?|chests? of (\d+ )?drawers|drawer chests?|\d[- ]drawer( [\w-]+){0,2} chests?|tallboys?|lingerie chests?|highboys?"),
    ("wardrobe", 2, r"wardrobes?|armoires?|closets?|(garment|clothes|clothing|coat) racks?|hall trees?"),
    ("bookshelf", 2, r"book ?cases?|book ?shel(f|ves)|(shelving|shelf) units?|[ée]tag[èe]res?|(ladder|display|storage) shel(f|ves)|cube (organizers?|storage|shelves|bookcases?)|cubb(y|ies)|bakers? racks?|shelving|shel(f|ves)|(shoe|wine|storage) racks?"),
    ("cabinet", 2, r"cabinets?|sideboards?|buffets?|credenzas?|(bar|kitchen|serving|rolling) carts?|kitchen islands?|hutch(es)?|curios?|pantr(y|ies)|cupboards?|lockers?|trunks?|(storage|blanket|toy) chests?|toy (box(es)?|storage|organizers?)( bins?)?|shoe (storage|cabinets?)|storage (units?|towers?)|vitrines?"),
    ("chest", 1, r"chests?"),
    # plants and decorations
    ("plant", 2, r"planters?|window box(es)?|(artificial|faux) [\w\s-]{0,30}?(plants?|trees?|ferns?|succulents?|palms?|flowers?|arrangements?)|succulents?|bonsai|topiar(y|ies)|jardini[èe]res?|cachepots?|terrariums?|garden urns?|pots?"),
    ("decor", 2, r"vases?|pillows?|baskets?|candle ?(holders?|sticks?|stands?)|candlesticks?|candelabras?|hurricanes?|votives?|lanterns?|sculptures?|figurines?|statues?|book ?ends?|canisters?( sets?)?|jewelry box(es)?|urns?|storage (cubes?|bins?|baskets?|box(es)?)|fabric (bins?|cubes?)|hampers?|magazine (racks?|holders?)|(table|mantel|desk|alarm) clocks?|wreaths?|garlands?|centerpieces?|decorative (bowls?|box(es)?|objects?|orbs?|balls?|accents?|jars?|bottles?|plates?|urns?|trays?)|organizer box(es)?"),
    ("decor", 0, r"bowls?|jars?|trays?|box(es)?|bins?|cubes?|holders?"),
    ("cabinet", 0, r"carts?|islands?|organizers?"),
    ("bookshelf", 0, r"racks?"),
    ("-a baby or play item", 0, r"toys?"),
    ("-kitchenware", 0, r"plates?"),
    ("wall_art", 0, r"signs?"),
    ("-a wall shelf or an organizer", 0, r"boards?|easels?"),
]
_NOUNS_RE = [(kind, weight, re.compile(rf"\b(?:{pattern})\b", re.I)) for kind, weight, pattern in _NOUNS]
# A general word, and the exact kinds that may stand before it in the same title.
_GENERAL = {
    "seat": {"stool", "chair", "armchair"},
    "table": {"coffee_table", "side_table", "dining_table", "console_table", "nightstand", "desk", "tv_stand"},
    "lamp": {"floor_lamp", "table_lamp", "ceiling_light", "-a wall or outdoor light fitting"},
    "frame": {"mirror", "wall_art", "wall_clock"},
    "chest": {"dresser", "cabinet", "nightstand"},
    "console": {"tv_stand", "desk", "console_table"},
}

# Where the stretch of a title that names the product ends: before "with", before a second piece
# ("chair and ottoman", "garden stool or side table") and, tried first, before a size ("Desk 42"W").
_AFTER = r"\s(?:with|w/|without|featuring|includes?|including|for)\s|\(|\sor\s+(?:(?:side|end|accent)\s+)?(?:tables?|stools?|ottomans?|bench)\b|\s(?:and|&|\+)\s+(?:(?:a|an|\d|two|four|six|matching|storage)\s+)*(?:ottomans?|foot ?stools?|stools?|chairs?|bench(?:es)?|side tables?|end tables?|night ?stands?|mirrors?|hutch(?:es)?)\b"
_SIZE = r"""\d+(?:[.,]\d+)?\s*(?:"|''|'|-?\s?inch(?:es)?\b|-?\s?in\b|cm\b|mm\b|ft\b|foot\b|feet\b|[whld]\b|x\s*\d)"""
_HEAD = re.compile(rf"{_AFTER}|{_SIZE}", re.I)
_HEAD_LOOSE = re.compile(_AFTER, re.I)
_SPLIT = re.compile(r"\s*,\s*|\s+[-–—|]\s+|\s*[-–—]\s+(?=[A-Z0-9])")  # a title's parts: at commas and dashes

# Shop category -> kind, for a title that does not say (a product is filed under several).
_BY_NODE: list[tuple[str, str]] = [
    ("barstools", "stool"), ("/kitchen & dining room furniture/chairs", "chair"), ("dining chairs", "chair"), ("home office desk chairs", "chair"),
    ("home office chairs", "chair"), ("/living room furniture/chairs", "armchair"), ("sofas & couches", "sofa"), ("ottomans", "ottoman"),
    ("end tables", "side_table"), ("coffee tables", "coffee_table"), ("sofa & console tables", "console_table"), ("nesting tables", "side_table"),
    ("nightstands", "nightstand"), ("bedside tables", "nightstand"), ("dressers", "dresser"), ("chest of drawers", "dresser"), ("bookcases", "bookshelf"),
    ("television stands", "tv_stand"), ("tv stands", "tv_stand"), ("media storage", "tv_stand"), ("buffets & sideboards", "cabinet"), ("sideboards", "cabinet"),
    ("bedroom wardrobes", "wardrobe"), ("home office desks", "desk"), ("/desks", "desk"), ("dining tables", "dining_table"),
    ("/kitchen & dining room furniture/tables", "dining_table"), ("floor lamps", "floor_lamp"), ("table lamps", "table_lamp"), ("desk lamps", "table_lamp"),
    ("pendant lights", "ceiling_light"), ("chandeliers", "ceiling_light"), ("close to ceiling lights", "ceiling_light"), ("storage benches", "bench"),
    ("/beds, frames & bases/beds", "bed"), ("bed frames", "bed"), ("planters", "plant"), ("vases", "decor"), ("wall clocks", "wall_clock"),
]
# Product type -> kind, the last resort (the types are coarse, and wrong for one product in ten).
_BY_TYPE = {
    "SOFA": "sofa", "CHAIR": "seat", "STOOL_SEATING": "stool", "OTTOMAN": "ottoman", "RUG": "rug", "WALL_ART": "wall_art", "HOME_MIRROR": "mirror",
    "PLANTER": "plant", "VASE": "decor", "PILLOW": "decor", "DESK": "desk", "BED": "bed", "BED_FRAME": "bed", "DRESSER": "dresser", "BENCH": "bench",
    "SHELF": "bookshelf", "CABINET": "cabinet", "LAMP": "lamp", "TABLE": "table", "BEAN_BAG_CHAIR": "armchair", "CLOCK": "wall_clock",
    "PICTURE_FRAME": "frame", "CANDLE_HOLDER": "decor", "BASKET": "decor", "ARTIFICIAL_PLANT": "plant", "SCULPTURE": "decor", "FIGURINE": "decor",
    "JAR": "decor", "DISHWARE_BOWL": "decor", "LAUNDRY_HAMPER": "decor", "BODY_POSITIONER": "decor",
}
# Types that are right about what they say, where titles mislead: a print of chairs is not a chair.
_TRUSTED_TYPE = {"WALL_ART": {"wall_art", "mirror", "wall_clock", "frame"}, "RUG": {"rug"}}

_SECTIONAL = re.compile(r"sectional(?! component)|\b[lu][- ]shaped?\b|corner (sofa|couch)|chaise|modular (sofa|couch)", re.I)
_LOVESEAT = re.compile(r"love ?seat|settee|\b2[- ]seater|two[- ]seater", re.I)
_OUTDOOR = re.compile(r"outdoor|patio|garden|adirondack|\bdeck\b|porch|balcony|poolside|all[- ]weather", re.I)
_INDOOR_TOO = re.compile(r"indoor[/ -]*(and |or |& )?outdoor|outdoor[/ -]*(and |or |& )?indoor", re.I)
_KIDS = re.compile(r"\bkids?\b|kid's|kiddie|children|child'?s?\b|toddler|nursery|youth|\bbaby\b|junior|\bboys?\b|\bgirls?\b|playroom|school classroom", re.I)

CAT_BY_CLASS = {
    "sofa": "sofas", "loveseat": "sofas", "sectional": "sofas", "armchair": "armchairs", "ottoman": "armchairs", "chair": "chairs", "stool": "chairs",
    "bench": "chairs", "coffee_table": "tables", "side_table": "tables", "dining_table": "tables", "console_table": "tables", "desk": "desks",
    "bed": "beds", "crib": "beds", "nightstand": "beds", "dresser": "storage", "wardrobe": "storage", "bookshelf": "storage", "cabinet": "storage",
    "tv_stand": "storage", "floor_lamp": "lighting", "table_lamp": "lighting", "ceiling_light": "lighting", "rug": "rugs", "wall_art": "wall",
    "mirror": "wall", "plant": "plants", "decor": "decor", "other": "decor",
}


def _noun(text: str, least: int = 1) -> str | None:
    """The kind a stretch of title names: its last noun, or the exact one a general last noun
    refers back to."""
    found = []  # (where it ends, minus its place in the list, kind, weight): the largest is the last noun
    for order, (kind, weight, pattern) in enumerate(_NOUNS_RE):
        for m in pattern.finditer(text):
            found.append((m.end(), -order, kind, weight))
    found = [f for f in found if f[3] >= least] if least else [f for f in found if f[3] == 0]
    if not found:
        return None
    end, _, kind, _ = max(found)
    if kind in _GENERAL:
        exact = [f for f in found if f[2] in _GENERAL[kind] and f[0] <= end]
        if exact:
            return max(exact)[2]
    return kind


def _settle(kind: str, title: str, w: float, d: float, h: float) -> tuple[str | None, bool]:
    """A noun and the model's size -> (class, whether the title alone said so). Some nouns cover
    two classes (a "lamp", a "sofa" 1.5 m wide, a "table") and the size says which."""
    long, short = max(w, d), min(w, d)
    thin = min(w, d, h)
    if kind == "lamp":
        return ("floor_lamp" if h >= 1.05 else "table_lamp" if long <= 0.7 else None), False
    if kind == "wall_clock":
        return ("wall_art" if thin <= 0.12 and max(w, h) >= 0.2 else "decor"), True
    if kind == "frame":
        return ("wall_art" if max(w, h) >= 0.3 and thin <= 0.12 else "decor" if long <= 0.5 else None), True
    if kind == "sofa":
        if h < 0.55:  # no back: an ottoman that folds out, or one piece of a sectional
            return ("ottoman" if long <= 1.6 else None), False
        if _SECTIONAL.search(title) and (short >= 1.2 or long >= 2.6):
            return "sectional", True
        if long < 1.15:
            return "armchair", False
        if long >= 1.6 and re.search(r"\b3[- ]seater|three[- ]seat", title, re.I):
            return "sofa", True
        if long < 1.75 or (_LOVESEAT.search(title) and long < 1.95):
            return "loveseat", True
        if short >= 1.35:
            return "sectional", False  # a "3-seater" with a chaise
        return "sofa", True
    if kind == "component":
        if h < 0.6 or re.search(r"ottoman|foot ?stool", title, re.I):
            return "ottoman", False
        return ("armchair" if long < 1.3 else "loveseat" if long < 1.95 else "sofa"), False
    if kind == "armchair" and long < 0.62 and h > 1.3 * long:
        return "chair", True  # "solid wood armchairs" for a dining table
    if kind == "floor_lamp" and h < 1.0:
        return "table_lamp", False
    if kind == "table_lamp" and h > 1.05:
        return "floor_lamp", False
    if kind == "coffee_table" and long < 0.6:
        return "side_table", False
    if kind == "coffee_table" and h > 0.62 and short <= 0.5:
        return "console_table", False
    if kind == "armchair":
        if long > 1.45 and short >= 1.3 and re.search(r"chaise", title, re.I):
            return "sectional", False  # a "reversible chaise": a sofa with one
        return "armchair", long <= 1.45  # longer: a chaise, a lounger, or a chair shown with its ottoman
    if kind == "chest":
        return "cabinet", False
    if kind == "console":
        return ("console_table" if h >= 0.62 else "tv_stand"), False
    if kind == "bed":
        return "bed", not re.search(r"daybed|rollaway|folding|foldable|trundle", title, re.I)
    if kind == "seat":
        return ("armchair" if w >= 0.68 and h <= 1.15 else "chair"), False
    if kind == "bench" and long < 0.8 and re.search(r"ottoman", title, re.I):
        return "ottoman", True
    if kind == "stool" and h < 0.3:
        return "ottoman", False
    if kind == "table":
        if h <= 0.55 and long >= 0.75:
            return "coffee_table", False
        if long <= 0.8 and 0.3 <= h <= 0.85:
            return "side_table", False
        if 0.66 <= h <= 0.82 and long >= 0.7 and short >= 0.6 or 0.85 <= h <= 1.15 and long >= 0.6 and short >= 0.5:
            return "dining_table", False  # the second: a bar table
        if 0.66 <= h <= 1.0 and long >= 0.8 and short <= 0.5:
            return "console_table", False
        return None, False
    if kind == "tv_stand" and h >= 1.2 and long < h:
        return "bookshelf", True  # an "entertainment tower" stands beside the TV
    if kind == "console_table" and h < 0.62:
        return "tv_stand", False  # a low "console" is a media console
    if kind == "bookshelf" and h < 0.45:
        return None, False  # one board: a shelf for a wall
    if kind == "cabinet" and h >= 1.7 and long >= 0.9 and re.search(r"wardrobe|armoire|closet|cupboard|sliding door", title, re.I):
        return "wardrobe", True
    if kind == "wardrobe" and h < 1.3:
        return "cabinet", False  # a low "armario" is a cupboard
    if kind == "side_table" and long > 0.95 and short <= 0.5 and h >= 0.6:
        return "console_table", False  # a "console end table"
    return kind, True


def _classify(title: str, english: bool, color: str | None, product_type: str, nodes: list[str], w: float, d: float, h: float) -> tuple[str | None, str, bool]:
    """(class, why it is left out when there is none, whether the class is sure)."""
    text = title if english else (_gloss(title) or title)
    if color and len(color) >= 3:  # "Sofa, Light Grey" must not read as a light
        text = re.sub(re.escape(color), " ", text, flags=re.I)
    # The first part of the title that names something: "West, Mid-Century, Oak Nightstand Bedside, 1-Drawer Table".
    parts = [cut.split(part, maxsplit=1)[0] for part in _SPLIT.split(text) for cut in (_HEAD, _HEAD_LOOSE)]
    kind = next((k for k in map(_noun, parts) if k), None)
    sure = kind is not None
    if kind is None and product_type in _NOT_HOME:  # a cable, a dumbbell: nothing in the title says furniture
        return None, "not home furnishing (by product type)", False
    if kind is None:
        kind = next((k for k in (_noun(part, 0) for part in parts) if k), None)
    if kind is None and english:  # an "English" title in German, or one that is only a name
        foreign = _gloss(title)
        kind = _noun(foreign) if foreign else None
        sure = kind is not None
    trusted = _TRUSTED_TYPE.get(product_type)
    if trusted and kind not in trusted and min(w, d, h) <= 0.12:  # a print of a bicycle is filed as wall art, and is
        kind, sure = _BY_TYPE[product_type], True
    if kind is None:
        path = " ".join(nodes).lower()
        kind = next((cls for part, cls in _BY_NODE if part in path), None) or _BY_TYPE.get(product_type)
    if kind is None:
        return None, "no class fits", False
    if kind.startswith("-"):
        return None, kind[1:], False
    if kind == "seat":  # a "chair": the shop shelf it is on says which kind
        path = " ".join(nodes).lower()
        hint = next((cls for part, cls in _BY_NODE if part in path and cls in ("chair", "armchair", "stool")), None)
        if hint == "armchair" and w >= 0.6 or hint in ("chair", "stool") and w < 0.72:
            return hint, "", True
    cls, by_title = _settle(kind, text, w, d, h)
    if cls is None:
        return None, "no class fits", False
    return cls, "", sure and by_title and not re.search(r"\bcomponents?\b|\bmodul", text, re.I)


def _category(cls: str, title: str) -> str:
    if _KIDS.search(title):
        return "kids"
    if _OUTDOOR.search(title) and not _INDOOR_TOO.search(title) and cls != "rug":
        return "outdoor"
    return CAT_BY_CLASS[cls]


# --- size -----------------------------------------------------------------------------------------

# What a class's pieces measure: (longer side of the footprint, shorter side, height), each as
# (least, most) in metres. Wide on purpose: this is for telling a chair from a model of a chair
# drawn 2.5 times too large, or a nightstand's listing that carries the model of a bed.
_BOUNDS: dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = {
    "sofa": ((1.5, 3.3), (0.6, 1.45), (0.55, 1.3)),
    "loveseat": ((1.0, 2.0), (0.55, 1.35), (0.55, 1.3)),
    "sectional": ((1.7, 4.8), (0.8, 3.8), (0.55, 1.3)),
    "armchair": ((0.5, 2.4), (0.45, 1.6), (0.45, 1.45)),
    "chair": ((0.38, 1.0), (0.35, 0.95), (0.6, 1.5)),
    "stool": ((0.25, 0.8), (0.25, 0.75), (0.3, 1.3)),
    "ottoman": ((0.3, 1.9), (0.3, 1.3), (0.2, 0.65)),
    "bench": ((0.6, 2.4), (0.25, 1.6), (0.35, 1.2)),
    "coffee_table": ((0.5, 1.9), (0.35, 1.3), (0.2, 0.62)),
    "side_table": ((0.25, 1.0), (0.2, 0.85), (0.3, 0.9)),
    "dining_table": ((0.6, 3.3), (0.5, 1.5), (0.62, 1.2)),
    "desk": ((0.6, 2.6), (0.25, 1.9), (0.6, 2.0)),
    "console_table": ((0.6, 2.3), (0.2, 0.65), (0.55, 1.15)),
    "tv_stand": ((0.7, 2.8), (0.25, 0.75), (0.28, 1.1)),
    "bookshelf": ((0.25, 2.4), (0.2, 0.75), (0.45, 2.5)),
    "cabinet": ((0.28, 2.6), (0.18, 0.95), (0.35, 2.4)),
    "dresser": ((0.5, 2.4), (0.3, 0.75), (0.55, 1.7)),
    "wardrobe": ((0.4, 3.3), (0.3, 0.95), (1.3, 2.6)),
    "nightstand": ((0.28, 1.0), (0.25, 0.7), (0.3, 0.95)),
    "bed": ((1.85, 2.7), (0.75, 2.4), (0.15, 2.4)),
    "crib": ((0.8, 1.7), (0.45, 1.0), (0.5, 1.4)),
    "floor_lamp": ((0.15, 1.7), (0.12, 1.0), (1.0, 2.5)),
    "table_lamp": ((0.08, 0.75), (0.06, 0.7), (0.15, 1.0)),
    "ceiling_light": ((0.08, 1.8), (0.06, 1.8), (0.06, 2.6)),
    "rug": ((0.55, 4.6), (0.35, 3.8), (0.0005, 0.12)),
    "plant": ((0.06, 1.4), (0.05, 1.4), (0.05, 2.4)),
    "decor": ((0.04, 1.3), (0.01, 0.9), (0.02, 1.3)),
}
_KIDS_SMALLER = 0.55  # a child's chair or bed may be this much smaller than the least above
_FLAT = {"rug", "wall_art", "mirror"}  # one side is a thickness, not a size anyone states with care


def _believable(cls: str, w: float, d: float, h: float, kids: bool = False) -> bool:
    """Whether a piece of this class can have this size."""
    if cls in ("wall_art", "mirror"):  # it hangs: wide and high, and thin from front to back
        face = (max(w, h), min(w, h))
        return 0.12 <= face[0] <= 2.6 and face[1] >= 0.05 and d <= (0.45 if cls == "mirror" else 0.2) and d < face[1]
    if cls not in _BOUNDS or (cls == "armchair" and w > 1.5):  # an armchair may be long (a chaise), not wide: that is a sofa's model
        return cls not in _BOUNDS
    least = _KIDS_SMALLER if kids else 1.0
    sides = (max(w, d), min(w, d), h)
    return all(lo * (least if n < 2 or cls in ("chair", "armchair", "stool", "desk", "dining_table", "bench") else 1.0) <= v <= hi for n, (v, (lo, hi)) in enumerate(zip(sides, _BOUNDS[cls])))


_N = r"(\d+(?:[.,]\d+)?)"
_INCHES = r"""(?:"|''|-?\s?inch(?:es)?\b|in\b\.?)"""
_FEET = r"(?:'|ft\b\.?|foot\b|feet\b)"
_AXIS = {"w": "w", "wide": "w", "width": "w", "diameter": "w", "h": "h", "high": "h", "height": "h", "tall": "h", "l": "l", "long": "l", "length": "l", "d": "d", "deep": "d", "depth": "d"}
_AXIS_WORD = r"(w|h|l|d|wide|width|high|height|tall|long|length|deep|depth|diameter)\b(?![-'])"
_CORNER = re.compile(r"\bcorner\b|angolare|d'angle|\bangle\b|rinc[óo]n|esquiner|eck(sofa|bank|schreibtisch)", re.I)  # made for a corner: not for the designer to place
_WITH_ANOTHER = re.compile(r"\b(and|with|&|\+) (\d |two |a |an )?(matching )?(chairs?|stools?|ottomans?|bench|foot ?stool|side table|hutch)\b", re.I)  # the model shows two pieces
_SEVERAL = re.compile(r"\bset of (\d|two|three|four)|\b(\d|two|three|four|five|six|nine)[- ]?(pack|pc|pcs|pieces?)\b|\bpack of \d|\bpair\b|\b(and|with|&|\+) (\d |two |a |an )?(matching )?(chairs?|stools?|ottomans?|bench|foot ?stool|side table|hutch|mirror)\b|nesting|nest of|juego|lot de|set d[ae]", re.I)


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def _stated(title: str, cls: str) -> tuple[list[tuple[str, float]], list[list[float]]]:
    """The sizes a title states, in metres: the named ones ("79.9W", "54 Inch Height") as
    (axis, metres), and the bare ones ("5 x 8 Foot", "30" x 30"", "92 x 200 x 85 cm") in groups."""
    text = title
    named: list[tuple[str, float]] = []
    groups: list[list[float]] = []

    def take(pattern: str, use=None) -> None:
        """Read every match, then blank it out so that no later pattern reads it again."""
        nonlocal text
        if use:
            for m in re.finditer(pattern, text, re.I):
                use(m)
        text = re.sub(pattern, " ~ ", text, flags=re.I)

    def feet(whole: str, inches: str | None) -> float:
        return _number(whole) * FOOT + (float(inches) * INCH if inches else 0.0)

    # Numbers that are not the piece's size: an adjustable range ("60-82L", "12 - 58 Inch Cord"),
    # a cord, what a TV stand holds, a weight, a wattage.
    take(rf"{_N}\s*(?:\"|'')?\s*[-–]\s*{_N}\s*{_INCHES}?\s*-?\s*(?:w|h|l|d|wide|height|cord|chain)\b")
    take(rf"{_N}\s*{_INCHES}\s*[-–]\s*{_N}\s*{_INCHES}")
    take(rf"{_N}\s*{_INCHES}\s*(?:cord|chain|shade|pile|thick|foam|memory)\b")
    take(rf"(?:up to|holds|fits|for tvs?)\s*(?:up to\s*)?{_N}\s*{_INCHES}?")
    take(rf"{_N}\s*-?\s*(?:pounds?|lbs?|kg|watts?|lumens?|gallons?|btu|pages)\b")
    # centimetres
    take(rf"{_N}\s*cm\s*(l|w|h|d)\b", lambda m: named.append((m.group(2).lower(), _number(m.group(1)) / 100)))
    take(rf"{_N}\s*(?:cm)?\s*[x×]\s*{_N}\s*(?:cm)?(?:\s*[x×]\s*{_N})?\s*cm\b", lambda m: groups.append([_number(g) / 100 for g in m.groups() if g]))
    take(rf"{_N}\s*cm\b", lambda m: groups.append([_number(m.group(1)) / 100]))
    # feet: "5' 9 x 3' 9", "10' 6 x 8'", "5'W x 7'6"L", "8 x 10 Foot", "5'3" Round", "6ft"
    take(rf"{_N}\s*{_FEET}\s*(?:(\d+)\s*(?:\"|in\b\.?)?)?\s*[wl]?\s*[x×]\s*{_N}\s*{_FEET}\s*(?:(\d+)\s*(?:\"|in\b\.?)?)?", lambda m: groups.append([feet(m.group(1), m.group(2)), feet(m.group(3), m.group(4))]))
    take(rf"{_N}\s*[x×]\s*{_N}\s*{_FEET}", lambda m: groups.append([_number(m.group(1)) * FOOT, _number(m.group(2)) * FOOT]))
    take(r"(\d+)\s*'\s*(\d+)?\s*\"?\s*(?:round|square|diameter)", lambda m: groups.append([feet(m.group(1), m.group(2))] * 2))
    take(rf"{_N}\s*-?\s*(?:ft\b\.?|foot\b|feet\b|'(?![\w']))", lambda m: groups.append([_number(m.group(1)) * FOOT]))
    if cls == "rug":  # "5x8": a rug's size in feet
        take(r"\b(\d{1,2})\s*[x×]\s*(\d{1,2})\b", lambda m: groups.append([float(m.group(1)) * FOOT, float(m.group(2)) * FOOT]))
    # inches: named, then in groups, then alone
    take(rf"{_N}\s*{_INCHES}?\s*-?\s*{_AXIS_WORD}", lambda m: named.append((_AXIS[m.group(2).lower()], _number(m.group(1)) * INCH)))
    take(rf"{_N}\s*{_INCHES}?\s*[x×]\s*{_N}\s*{_INCHES}?(?:\s*[x×]\s*{_N})?\s*{_INCHES}", lambda m: groups.append([_number(g) * INCH for g in m.groups() if g]))
    if cls in ("wall_art", "mirror", "decor"):  # "24 x 36": a print's or a pillow's size in inches
        take(rf"\b{_N}\s*[x×]\s*{_N}\b", lambda m: groups.append([_number(m.group(1)) * INCH, _number(m.group(2)) * INCH]))
    take(rf"{_N}\s*{_INCHES}", lambda m: groups.append([_number(m.group(1)) * INCH]))
    # A wattage reads like a width ("40W"), and a pendant's height is its cord's.
    lighting = cls in ("floor_lamp", "table_lamp", "ceiling_light")
    named = [(axis, m) for axis, m in named if 0.03 <= m <= 5.0 and not (lighting and axis == "w") and not (cls == "ceiling_light" and axis == "h")]
    groups = [g for g in groups if all(0.03 <= m <= 6.0 for m in g)]
    if cls == "ceiling_light":
        groups = []
    return named, groups


def _times_too_large(model: tuple[float, float, float], named: list[tuple[str, float]], groups: list[list[float]]) -> list[float]:
    """How many times too large the model (w, d, h) is, by each size its title states. A named
    height is the model's height. A width, length or depth is the side of the footprint it
    matches, whichever that is (titles call a bench's depth its width); when it matches neither,
    the side its name says. A bare group is matched to the sides it fits best."""
    w, d, h = model
    out = []
    for axis, metres in named:
        if axis == "h":
            out.append(h / metres)
            continue
        either = [r for r in (w / metres, d / metres) if not _off(r)]
        out.append(min(either, key=lambda r: abs(math.log(r))) if either else {"w": w, "l": max(w, d), "d": min(w, d)}[axis] / metres)
    for group in groups:
        best = min(itertools.permutations(model, len(group)), key=lambda sides: sum(abs(math.log(a / b)) for a, b in zip(sides, group)))
        out += [a / b for a, b in zip(best, group)]
    return out


def _each_side(model: tuple[float, float, float], title: str, cls: str) -> tuple[float, float, float] | None:
    """A rug's or a picture's real (w, d, h) when its title gives its two sides and the model is
    another size of the same design: the 5 x 8 foot model under the 8 x 10 foot listing. Its two
    sides are stated each for itself (they differ by a few per cent from one factor), its
    thickness goes with them."""
    w, d, h = model
    _, groups = _stated(title, cls)
    pair = next((sorted(g) for g in groups if len(g) == 2), None)
    if pair is None or _SEVERAL.search(title):
        return None
    face = sorted((w, d) if cls == "rug" else (w, h))
    by = [pair[0] / face[0], pair[1] / face[1]]
    if not all(1 / 3 <= r <= 3 for r in by) or max(by) / min(by) > 1.35:
        return None
    short, long, thick = pair[0], pair[1], (h if cls == "rug" else d) * _mean(by)
    if cls == "rug":
        return (long, short, thick) if w >= d else (short, long, thick)
    return (long, thick, short) if w >= h else (short, thick, long)


TOLERANCE = 1.12  # a model within 12 % of a stated size is that size
UNIFORM = 1.10  # the stated sizes agree on one factor when they are within 10 % of each other


def _mean(ratios: list[float]) -> float:
    return math.exp(sum(math.log(r) for r in ratios) / len(ratios))


def _off(factor: float) -> bool:
    return abs(math.log(factor)) > math.log(TOLERANCE)


def _real_size(model: tuple[float, float, float], inches: dict, title: str, cls: str, kids: bool) -> tuple[float, bool, str]:
    """(the factor the model is too large by, whether its size is confirmed, how).

    The listing's three sizes are compared with the model's largest to smallest (listings mix up
    width, length and height); when all three differ by one factor, that is a model drawn at the
    wrong scale and not a listing that gives the shipping box. The title's own sizes confirm or
    overrule the listing: they are written by a person, about the product."""
    w, d, h = model
    named, groups = _stated(title, cls)
    by_title = _times_too_large(model, named, groups)
    listed = sorted(v * INCH for v in (inches or {}).values() if v and v > 0)
    by_listing: list[float] = []
    if len(listed) == 3:
        own = sorted(model)
        thin = cls in _FLAT or listed[0] < 0.035 * listed[2] or own[0] < 0.035 * own[2]
        by_listing = [a / b for a, b in zip(own, listed)][1 if thin else 0:]
    listing_uniform = bool(by_listing) and max(by_listing) / min(by_listing) <= UNIFORM
    title_uniform = bool(by_title) and max(by_title) / min(by_title) <= TOLERANCE
    if listing_uniform and not _off(_mean(by_listing)):
        return 1.0, True, "listing"
    if by_title and all(not _off(r) for r in by_title):
        return 1.0, True, "title"
    if len(listed) == 3 and not (title_uniform and _off(_mean(by_title))):
        # The listing gives one side differently (a headboard, a cord at another length) and the other two as drawn.
        near = sorted(abs(math.log(a / b)) for a, b in zip(sorted(model), listed))
        if near[1] <= math.log(1.06) and near[2] <= math.log(2.0):
            return 1.0, True, "listing"
    if cls == "ceiling_light":  # its height is its cord's, which a listing gives at any length
        return 1.0, False, ""
    several = bool(_SEVERAL.search(title))

    def fits(factor: float) -> bool:
        # A set drawn side by side is two or three times one piece's size: that is not a wrong scale.
        side_by_side = several and any(abs(factor / n - 1) < 0.08 for n in (2, 3))
        return not side_by_side and _believable(cls, w / factor, d / factor, h / factor, kids)

    by = _mean(by_title) if title_uniform and _off(_mean(by_title)) else None
    if listing_uniform:
        factor = _mean(by_listing)
        if by and max(by / factor, factor / by) <= UNIFORM and fits(factor):
            return factor, True, "listing and title"
        if by and len(by_title) >= 2 and fits(by):  # two sizes in the title against the listing's three: the title is about this product
            return by, True, "title"
        if fits(factor) and not (several and not by_title):
            return factor, not by_title, "listing"
        return 1.0, False, ""
    if by and len(by_title) >= 2 and fits(by):
        return by, True, "title"
    # One number in a title is thin evidence: it moves a model only when the model is impossible as it is.
    if by and 0.6 <= by <= 1.7 and not several and fits(by) and not _believable(cls, w, d, h, kids):
        return by, False, "title"
    return 1.0, False, ""


# --- facing ---------------------------------------------------------------------------------------

# ABO draws a piece with its front along +Z: checked by geometry on the catalog's 160 models and
# on 160 more of other brands and kinds (`python -m services.api.library.build.abo facing <id> ..`:
# every seat's back and every bed's headboard is at -Z, and so is the flat back panel of every
# chest, cabinet and wardrobe that has one).
# The exception is L- and U-shaped sectionals, a fifth of which are drawn with their long back
# along a side. Neither their titles ("left-facing") nor their sizes say which side, so every
# sofa that is deeper than wide was measured: here are the ones whose long back is at +X (turned
# by 90 degrees about +Y to bring it to -Z) and at -X (turned by 270). The corner sectionals
# that are as deep as wide have a back at -Z already and stay as drawn. A sofa that is deeper
# than wide and not listed here is kept, but is not for the designer to pick by itself.
_BACK_AT_PLUS_X = (
    "B07B4MSYPS B07BWL4C2Y B07B4MH6QH B07B4MKBVS B07B4D8982 B07BWMSP1R B07BWMQQCH B07BWJCSQL B07BWK59H7 B07B4MLQ2X B07BW8QTX1 B07BWK7JV5 "
    "B0831X8LWR B07BWL245W B07CB2G4TK B07BWMSLWY B07B4L563M B07BWK7FXW B0831Y1G6B B07BWK7JRT B07BW8VJ5G B07BW8P2DS B07JC4M6VR B07BW8MJQT "
    "B07B4D46YR B07B4M2B98 B07CB5GWDQ B07JD47LW3 B07BWJCPSN B07B4GXNLL B07B4D48XJ B07BWK59J3 B07CB5GN2X B07B4L562Y B07B4GVRXK B07BWLC7ZR "
).split()
_BACK_AT_MINUS_X = (
    "B07QFP5W3W B07BW8Q18T B07BWMSQ38 B07QDL46GB B07BWK7FYC B07BWK42J3 B07BWK59KD B07QJYGDZG B07QJYGDZ9 B07BW8PZ28 B07BWMQLWM B07BW8PZ2K "
    "B07CB54MFS B07B4FZXK2 "
).split()
_TURN: dict[str, int] = {**dict.fromkeys(_BACK_AT_PLUS_X, 90), **dict.fromkeys(_BACK_AT_MINUS_X, 270)}


def _facing(glb: bytes) -> dict:
    """Where a model's tall part is (its back, its headboard), from -1 to 1 along Z and X, and how
    much of each end of it is one flat sheet (a chest's back panel)."""
    import io

    import numpy as np
    import trimesh

    scene = trimesh.load(io.BytesIO(glb), file_type="glb", force="scene")
    mesh = scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)
    lo, hi = mesh.bounds
    size, centre = hi - lo, (lo + hi) / 2
    area, where = mesh.area_faces, mesh.triangles_center
    top = where[:, 1] > lo[1] + 0.7 * size[1]
    weight = area[top].sum() or 1.0

    def lean(axis: int) -> float:
        return float(((where[top, axis] - centre[axis]) * area[top]).sum() / weight / (size[axis] / 2))

    def sheet(axis: int, end: int) -> float:
        near = np.abs(where[:, axis] - (hi if end > 0 else lo)[axis]) < 0.012 * size[axis] + 0.002
        outward = mesh.face_normals[near][:, axis] * end > 0.9
        across = [k for k in range(3) if k != axis]
        return float(area[near][outward].sum() / (size[across[0]] * size[across[1]]))

    return {"w": float(size[0]), "d": float(size[2]), "h": float(size[1]), "back_z": lean(2), "back_x": lean(0),
            "sheet": {"-z": sheet(2, -1), "+z": sheet(2, 1), "-x": sheet(0, -1), "+x": sheet(0, 1)}}


def _turn_for(facing: dict) -> int:
    """Degrees about +Y that bring a model's back to -Z: 0 when it is there already. A piece that
    is deeper than wide with its tall part to one side has its long back there; a corner piece
    as deep as wide has two backs, and the one at -Z will do."""
    z, x = facing["back_z"], facing["back_x"]
    if facing["d"] > 1.05 * facing["w"] and abs(x) >= 0.15:
        return 90 if x > 0 else 270
    return 180 if z >= 0.15 and z > abs(x) else 0


def facing(ids: list[str]) -> None:
    """Fetch models and print which way each faces, as lines for `_TURN`."""
    catalog_build.prefetch(ids)
    rows = catalog_build.index()
    for piece_id in ids:
        try:
            f = _facing(catalog.fetch(piece_id))
        except Exception as e:
            print(f"    # {piece_id} could not be measured: {e}")
            continue
        name = re.sub(r"\s+", " ", (rows.get(piece_id, {}).get("name") or ""))[:60]
        print(f'    "{piece_id}": {_turn_for(f)},  # {f["w"]:.2f} x {f["d"]:.2f}, back at z {f["back_z"]:+.2f} x {f["back_x"]:+.2f} · {name}')


# --- words ----------------------------------------------------------------------------------------

# First words that are not the name of a design line, though no other title uses them elsewhere.
_NOT_A_LINE = {
    "all", "amazon", "beveled", "breakfast", "cadre", "campaign", "cargo", "chefsessel", "chic", "clothes", "cottage", "curvy", "eastern", "elegant",
    "estantería", "estantes", "express", "familia", "fully", "fun", "grande", "hall", "heights", "homepop", "iluminación", "industry", "inn", "just",
    "knit", "leaning", "level", "lux", "mid", "mobiletto", "moderner", "modular", "movie", "multifunction", "perchero", "physio", "premaire", "remache",
    "revolving", "rivetto", "scandinavian", "schlafsessel", "schoolhouse", "smooth", "spiegel", "sporting", "successfulhome", "triangular", "trípode",
    "urban", "ventilated", "éclairage", "heavy", "foldable", "ergonomic", "retro", "elevated", "hairpin", "tiered", "pneumatic", "torchiere",
    "functional", "premium", "dual", "easy", "high", "step", "memory", "rubber", "usb", "deco", "swing", "global", "rounded", "big", "neoprene",
    "old", "new", "extra", "three", "six", "children's", "handcrafted", "recycled", "terrazzo", "los", "lámpara", "mesa", "silla", "sedia", "divano",
}
_LINES_OF_TWO = {("Anne", "Marie"), ("St.", "Cloud"), ("North", "End"), ("Beaver", "Creek"), ("Coastal", "Breeze"), ("City", "Club")}
_NO_LINE = {"wall_art", "rug", "decor", "plant"}  # a print's first word is its subject, not a line
_WORD = re.compile(r"[A-Za-zÀ-ÿ][\w'.&+-]*")


def _line_names(titles: list[str]) -> set[str]:
    """The words that only ever start a title: "Sloane", "Andover", "Hayes" (a design line), and
    not "Modern" or "Wood", which also stand in the middle of others."""
    first: Counter[str] = Counter()
    elsewhere: Counter[str] = Counter()
    for title in titles:
        words = _WORD.findall(title)
        if words:
            first[words[0].rstrip(".")] += 1
            elsewhere.update({w.rstrip(".") for w in words[1:]})
    return {
        w for w, n in first.items()
        if w[0].isupper() and len(w) >= 3 and "-" not in w and not any(c.isdigit() for c in w) and elsewhere[w] <= 0.15 * n and w.lower() not in _NOT_A_LINE
    }


def _line(bare: str, cls: str, lines: set[str]) -> str | None:
    """The design line a title starts with, if it does."""
    words = _WORD.findall(bare)
    if not words or cls in _NO_LINE:
        return None
    if len(words) > 1 and (words[0], words[1]) in _LINES_OF_TWO:
        return f"{words[0]} {words[1]}"
    word = words[0].rstrip(".")
    if word not in lines:
        return None
    return word.capitalize() if word.isupper() else word


_SIZE_WORDS = re.compile(
    rf"""\(?\s*{_N}\s*(?:"|''|'|-?\s?inch(?:es)?\b|in\b\.?|cm\b|mm\b|ft\b\.?|foot\b|feet\b)?\s*-?\s*(?:w|h|l|d|wide|width|high|height|tall|long|length|deep|depth|diameter)\b(?![-'])\s*\)?"""
    rf"""|{_N}\s*(?:"|''|'|-?\s?inch(?:es)?\b|in\b\.?|cm\b|mm\b|ft\b\.?|foot\b|feet\b)?(?:\s*\d+\s*"?)?\s*[wl]?\s*[x×]\s*{_N}(?:\s*[x×]\s*{_N})?(?:\s*\d+\s*"?)?\s*(?:"|''|'|-?\s?inch(?:es)?\b|in\b\.?|cm\b|mm\b|ft\b\.?|foot\b|feet\b)?\s*[wlh]?"""
    rf"""|{_N}\s*(?:"|''|-?\s?inch(?:es)?\b|in\b\.?|cm\b|mm\b|ft\b\.?|foot\b|feet\b|'(?![a-z]))(?:\s*\d+\s*")?""", re.I)
_COUNT_WORDS = re.compile(r"\b(?:set|pack|lot|juego|confezione) (?:of|de|da|di) (?:\d+|two|three|four|six)\b|\b\d+[- ]?(?:pack|pk|pcs?)\b|\((?:set|pack) of \d+\)", re.I)
_BED_SIZES = re.compile(r"\b(?:cal(?:ifornia)?[- ]king|king|queen|twin(?:[- ]xl)?|full|double|single|small|medium|large|x-large|xl|oversized)\b(?:[- ]size[d]?)?", re.I)
_CODE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9]+(?:-[A-Z0-9]+)+\b|\b[A-Z]{2,}\d{3,}\w*\b")


def _segments(bare: str, color: str | None) -> list[str]:
    """The parts of a title (without its brand, cut at commas and dashes) that describe the
    design: not the ones that are a size, a count or the colour."""
    colour_words = {w for w in re.findall(r"[a-zà-ÿ]+", (color or "").lower().replace("gray", "grey"))}
    keep: list[str] = []
    after_size = False
    for part in _SPLIT.split(re.sub(r"\([^)]*\)", " ", bare)):
        part = part.strip(" .;:/")
        if not part:
            continue
        plain = re.sub(r"(?<![\w-])[x×](?![\w-])", " ", _COUNT_WORDS.sub(" ", _SIZE_WORDS.sub(" ", part)))  # and the "x" two sizes leave behind
        words = re.findall(r"[a-zà-ÿ]+", plain.lower().replace("gray", "grey"))
        sized = plain != part
        named = _noun(plain) is not None  # "Transitional Desk 42"W" is the design, with a size in it
        is_colour = bool(words) and (set(words) <= colour_words | {"and", "with", "finish", "colour", "color", "effect"} or (after_size and len(words) <= 3 and keep and not named))
        if not words or is_colour or (sized and len(words) <= 2 and not named):
            after_size = after_size or sized
        else:
            keep.append(re.sub(r"\s+", " ", plain).strip(" .;:/-"))
    return keep


def _family(brand: str | None, keep: list[str]) -> str:
    """One key per design: its brand and what its title says, without size or colour."""
    text = _BED_SIZES.sub(" ", " ".join(keep))
    slug = re.sub(r"[^a-z0-9]+", "-", f"{brand or ''} {text}".lower()).strip("-")
    return slug[:64].rstrip("-")


# Phrases shortened on a card, and words left out of a name that is too long: first the rooms a
# title lists to be found by, then the style words.
_SHORTER = [
    (r"\bmid[- ]century modern\b", "Mid-Century"), (r"\b(sofa|loveseat) couch\b", r"\1"), (r"\blove ?seat sofa\b", "Loveseat"), (r"\blove seat\b", "Loveseat"),
    (r"\b(side[/ ]end|end[/ ]side|side end|end side)( table)?\b", "Side Table"), (r"\barea rug\b", "Rug"),
    (r"\b(canvas )?print wall art( d[ée]cor)?\b", r"\1Print"), (r"\bwall art print\b", "Print"), (r"\bwall art d[ée]cor\b", "Wall Art"), (r"\bon canvas wall art\b", "on Canvas"),
    (r"\bthrow pillow\b", "Pillow"), (r"\bkitchen counter[- ]height\b", "Counter"), (r"\bcounter[- ]height bar ?stool\b", "Counter Stool"),
    (r"\bstanding floor lamp\b", "Floor Lamp"), (r"\btable desk lamp\b", "Table Lamp"), (r"\b(ceiling )?pendant chandelier( fixture)?\b", "Pendant"),
    (r"\bchest bedroom dresser\b", "Dresser"), (r"\bbedside nightstand\b", "Nightstand"), (r"\bled light bulbs?\b", "Bulb"), (r"\blight bulbs?\b", "Bulb"),
]
_FILLER = [
    r"\b(living ?room|bed ?room|dining ?room|kitchen|home office|office|entryway|bathroom|indoor|outdoor patio|home d[ée]cor|d[ée]cor|decorative|furniture|computer|upholstered|storage space)\b",
    r"\b(modern|contemporary|classic|casual|traditional|transitional|mid-century|rustic|industrial|farmhouse|vintage|glam|elegant|stylish)\b",
]
_KEEP_CASE = {
    "tv": "TV", "led": "LED", "usb": "USB", "l-shaped": "L-shaped", "u-shaped": "U-shaped", "l-shape": "L-shape", "u-shape": "U-shape", "c-shaped": "C-shaped",
    "x-base": "X-base", "l-sectional": "L-sectional", "u-sectional": "U-sectional", "a-frame": "A-frame", "x": "x", "ii": "II", "iii": "III", "mdf": "MDF", "pc": "PC", "dvd": "DVD", "cd": "CD",
    "london": "London", "paris": "Paris", "french": "French", "moroccan": "Moroccan", "persian": "Persian", "scandinavian": "Scandinavian",
    "hawaiian": "Hawaiian", "american": "American", "european": "European", "asian": "Asian", "eiffel": "Eiffel", "york": "York", "california": "California",
    "mojave": "Mojave", "edison": "Edison", "charles": "Charles", "adirondack": "Adirondack", "parsons": "Parsons", "windsor": "Windsor", "chesterfield": "Chesterfield",
}
_PLACES = ["New York City", "New York", "New England", "San Francisco", "Los Angeles", "Empire State", "Eiffel Tower", "Charles of London", "Los Altos"]
NAME_MAX = 48
LOW_PENDANT = 1.2  # a pendant drawn with this much cord hangs to head height in a room: not for the designer to hang
_NAME_TAIL = re.compile(r"\s(?:with|w/|featuring|for|in)\s", re.I)  # what a name can lose first: "with Tufted Back"
_FRAME_TAIL = re.compile(r",?\s(?:in|with)\s(?=(?:an? )?[\w\s-]*frame)", re.I)  # of a picture's name, only "in a White Wood Frame": the rest is its subject


def _sentence(text: str, line: str | None) -> str:
    """Sentence case: "Sloane mid-century sofa". The line's name and a few proper nouns keep their capitals."""
    words = text.split()
    lined = len(line.split()) if line and text.lower().startswith(line.lower()) else 0
    out = []
    for n, word in enumerate(words):
        if n < lined:
            out.append(line.split()[n])
            continue
        low = word.lower()
        core = low.strip(",.;:()'\"")
        out.append(low.replace(core, _KEEP_CASE[core]) if core in _KEEP_CASE else low)
    text = " ".join(out)
    for place in _PLACES:
        text = re.sub(rf"\b{place}\b", place, text, flags=re.I)
    return text[:1].upper() + text[1:]


def _card_name(keep: list[str], line: str | None, colour: str | None, cls: str) -> str:
    """What a card shows: the design's own words, shortened to fit, then its colour."""
    base, used = (keep[0], 1) if keep else ("", 0)
    while used < len(keep) and _noun(base) is None and len(base.split()) <= 8:  # "Ribe - 3 Seater Sofa", "Mesh, Mid-Back, Swivel Office Desk Chair"
        base, used = f"{base} {keep[used]}", used + 1
    if used < len(keep) and re.search(r"component$", base, re.I):  # "Bagley Sectional Component, Left-Facing Loveseat"
        base = f"{base} {keep[used]}"
    if _noun(base) is None and len(base.split()) <= 3:  # a title that is only the line's name: "Moselle" is a cabinet
        base = f"{base} {_LABEL.get(cls, '')}".strip()
    base = _CODE.sub(" ", _BED_SIZES.sub(lambda m: m.group(0) if re.search(r"king|queen|twin|full|double|single", m.group(0), re.I) else " ", base))
    for pattern, shorter in _SHORTER:
        base = re.sub(pattern, shorter, base, flags=re.I)
    base = re.sub(r"\s+", " ", base).strip(" ,.;:/-")
    tail = ""
    if colour:
        colour = re.sub(r"\s+", " ", colour.lower().replace("/", " and ").replace("&", "and")).strip(" .,;")
        said = set(re.findall(r"[a-zà-ÿ]+", base.lower()))
        if 2 < len(colour) <= 24 and not re.search(r"\d|multi|assorted|see description|^n/?a$", colour) and not set(re.findall(r"[a-zà-ÿ]+", colour)) <= said | {"and"}:
            tail = f", {colour}"
    # Shorter and shorter: without "with tufted back", without the rooms it is for, without its style words.
    cut = (_FRAME_TAIL if cls == "wall_art" else _NAME_TAIL).split(base, maxsplit=1)[0]
    tries = [base, cut if _noun(cut) or not _noun(base) else base]  # "Parquetry Top with Metal Base Side Table" keeps its table
    for pattern in _FILLER:
        tries.append(re.sub(r"\s+", " ", re.sub(pattern, " ", tries[-1], flags=re.I)).strip(" ,.;:/-"))
    for end in (tail, ""):
        for name in tries:
            if name and len(name) + len(end) <= NAME_MAX:
                return _sentence(name, line) + end
    words = (tries[-1] or base).split()  # still too long: the noun is at the end, so the words after the first go
    while len(words) > 2 and len(" ".join(words)) > NAME_MAX:
        del words[1]
    return _sentence(" ".join(words), line)[:NAME_MAX]


# The noun a card shows for a class when the title gives none in English ("Moselle", "Scrivania").
_LABEL = {
    "sofa": "sofa", "loveseat": "two-seat sofa", "sectional": "corner sofa", "armchair": "armchair", "chair": "chair", "stool": "stool", "ottoman": "footstool",
    "bench": "bench", "coffee_table": "coffee table", "side_table": "side table", "dining_table": "dining table", "desk": "desk", "console_table": "console table",
    "tv_stand": "TV stand", "bookshelf": "bookcase", "cabinet": "cabinet", "dresser": "chest of drawers", "wardrobe": "wardrobe", "nightstand": "bedside table",
    "bed": "bed", "crib": "cot", "floor_lamp": "floor lamp", "table_lamp": "table lamp", "ceiling_light": "pendant light", "rug": "rug", "wall_art": "picture",
    "mirror": "mirror", "plant": "planter", "decor": "decoration",
}
_GLOSS_AS_LABEL = {"sofa bed", "office chair", "dining chair", "bar stool", "shoe cabinet", "display cabinet", "sideboard", "bunk bed", "picture frame", "storage basket", "pillow", "vase", "garment rack", "accent chair"}
# The colours of the foreign listings, word by word.
_COLOURS = {
    "bianco": "white", "bianca": "white", "blanc": "white", "blanche": "white", "blanco": "white", "blanca": "white", "weiß": "white", "weiss": "white", "vit": "white",
    "nero": "black", "nera": "black", "noir": "black", "noire": "black", "negro": "black", "negra": "black", "schwarz": "black", "svart": "black",
    "grigio": "grey", "grigia": "grey", "gris": "grey", "grise": "grey", "grau": "grey", "grå": "grey", "blu": "blue", "bleu": "blue", "azul": "blue", "blau": "blue",
    "rosso": "red", "rouge": "red", "rojo": "red", "rot": "red", "verde": "green", "vert": "green", "grün": "green", "giallo": "yellow", "jaune": "yellow",
    "amarillo": "yellow", "gelb": "yellow", "marrone": "brown", "marron": "brown", "marrón": "brown", "braun": "brown", "rosa": "pink", "arancione": "orange",
    "naranja": "orange", "viola": "purple", "violet": "purple", "morado": "purple", "lila": "purple", "turchese": "turquoise", "turquesa": "turquoise", "türkis": "turquoise",
    "antracite": "anthracite", "antracita": "anthracite", "anthrazit": "anthracite", "quercia": "oak", "rovere": "oak", "chêne": "oak", "roble": "oak", "eiche": "oak", "ek": "oak",
    "noce": "walnut", "noyer": "walnut", "nogal": "walnut", "nussbaum": "walnut", "faggio": "beech", "hêtre": "beech", "haya": "beech", "buche": "beech",
    "naturale": "natural", "naturel": "natural", "natur": "natural", "chiaro": "light", "clair": "light", "claro": "light", "hell": "light", "scuro": "dark",
    "foncé": "dark", "oscuro": "dark", "dunkel": "dark", "crema": "cream", "crème": "cream", "creme": "cream", "argento": "silver", "argent": "silver", "plata": "silver",
    "silber": "silver", "oro": "gold", "doré": "gold", "dorado": "gold", "effetto": "effect", "effet": "effect", "efecto": "effect", "e": "and", "et": "and", "y": "and",
    "und": "and", "talpa": "taupe", "caffè": "coffee", "café": "coffee", "sabbia": "sand", "sable": "sand", "arena": "sand", "pietra": "stone", "pierre": "stone",
    "piedra": "stone", "selvatico": "wild", "sauvage": "wild", "salvaje": "wild", "rustico": "rustic", "rustique": "rustic", "rústico": "rustic", "invecchiata": "aged",
    "lucido": "gloss", "brillant": "gloss", "opaco": "matte", "mate": "matte", "petrolio": "petrol", "pétrole": "petrol", "lunare": "moon", "lune": "moon", "luna": "moon",
    "brun": "brown", "zwart": "black", "wit": "white", "grijs": "grey", "bruin": "brown", "eiken": "oak", "rood": "red", "blauw": "blue", "groen": "green", "colore": "", "couleur": "", "farbe": "", "tessuto": "", "tissu": "", "tela": "", "in": "", "de": "", "di": "", "en": "", "con": "with", "avec": "with",
}


def _colour_in_english(colour: str | None, foreign: bool = True) -> str | None:
    """A colour named in Italian, French, Spanish, German or Dutch, in English. An English
    listing's colour is looked at too ("grau", "wit"), unless its first word could be English."""
    if not colour:
        return None
    first = (re.findall(r"[\wà-ÿ]+", colour.lower()) or [""])[0]
    if not foreign and (first not in _COLOURS or first in {"e", "y", "in", "de", "di", "en", "con", "arena", "viola", "mate", "luna", "rosa", "crema", "hell", "rot", "lila", "natur", "sable", "et"}):
        return colour
    text = re.sub(r"bleu marine|blu navy|blu marino|azul marino|marineblau", "navy", colour.lower())
    words = [_COLOURS.get(w, w) for w in re.findall(r"[\wà-ÿ]+", text)]
    words = [w for w in words if w]
    if len(words) == 2 and words[1] in ("light", "dark", "wild", "aged", "rustic", "matte", "gloss", "natural", "petrol", "moon"):  # "grigio chiaro" -> light grey
        words.reverse()
    return " ".join(words) or None


# Words worth searching by: (tag, pattern), by what they say. Within a kind, in the order kept.
_TAGS: dict[str, list[tuple[str, str]]] = {
    "material": [
        ("solid wood", r"solid (wood|pine|oak|hardwood|acacia|mango|sheesham|beech|rubber ?wood)|massello|massif|maciza|massivholz"), ("oak", r"\boak|quercia|rovere|chêne|roble|eiche"),
        ("walnut", r"walnut|\bnoce\b|noyer|nogal"), ("pine", r"\bpine\b|\bpino\b|kiefer"), ("acacia", r"acacia"), ("mango wood", r"mango"), ("beech", r"beech|faggio|hêtre|\bhaya\b|buche"),
        ("birch", r"birch"), ("teak", r"\bteak"), ("bamboo", r"bamboo"), ("rattan", r"rattan"), ("wicker", r"wicker"), ("cane", r"\bcane\b"), ("reclaimed wood", r"reclaimed"),
        ("wood", r"wood(en)?\b|legno|\bbois\b|madera|\bholz"), ("faux leather", r"faux leather|vegan leather|leatherette|leathersoft|bonded leather|pu leather|synthetic leather|similpelle|polipiel|kunstleder"),
        ("leather", r"(?<!faux )(?<!vegan )(?<!bonded )(?<!synthetic )(?<!pu )\bleather\b|top[- ]grain|\bpelle\b|\bcuir\b|\bpiel\b|\bleder\b"),
        ("velvet", r"velvet|velluto|velours|terciopelo|\bsamt"), ("linen", r"\blinen|\blino\b|leinen"), ("cotton", r"cotton|cotone|\bcoton\b|algodón|baumwolle"), ("wool", r"\bwool|\blana\b|\blaine\b|wolle"),
        ("jute", r"\bjute"), ("sisal", r"sisal"), ("boucle", r"boucl[ée]"), ("performance fabric", r"performance fabric|stain[- ]resistant"), ("microfiber", r"microfib(er|re)"),
        ("fabric", r"\bfabric|upholster(ed|y)|tessuto|\btissu\b|\btela\b|\bstoff"), ("marble", r"marble|marmo|marbre|mármol"), ("glass", r"\bglass\b|vetro|\bverre\b|vidrio|\bglas\b"),
        ("ceramic", r"ceramic|ceramica|céramique|keramik"), ("stoneware", r"stoneware"), ("concrete", r"concrete|cement"), ("stone", r"\bstone\b|terrazzo|granite"),
        ("brass", r"\bbrass"), ("gold", r"\bgold|doré|dorado"), ("bronze", r"bronze"), ("copper", r"copper"), ("chrome", r"chrome"), ("nickel", r"nickel"), ("iron", r"\biron\b|wrought"),
        ("steel", r"\bsteel"), ("metal", r"\bmetal|metallo|métal|metall"), ("mirrored", r"mirrored"), ("polypropylene", r"polypropylene"), ("plastic", r"plastic|\bresin\b|acrylic"),
    ],
    "finish": [
        ("lacquer", r"lacquer|high[- ]gloss|laccato|laqué"), ("matte", r"\bmatte?\b"), ("brushed", r"brushed"), ("distressed", r"distressed|weathered|antiqued?\b|\baged\b"),
        ("whitewashed", r"white[- ]?wash"), ("natural", r"\bnatural\b"),
    ],
    "shape": [
        ("round", r"\bround\b|circular|rotondo|\brond\b|redond[ao]"), ("square", r"\bsquare\b|quadrat"), ("oval", r"\boval"), ("rectangular", r"rectang"), ("hexagonal", r"hexagon"),
        ("l-shaped", r"\bl[- ]shaped?\b|\bl[- ]sectional|corner (sofa|desk)|angolare|d'angle"), ("u-shaped", r"\bu[- ]shaped?\b|\bu[- ]sectional"), ("runner", r"\brunner\b"), ("drum shade", r"\bdrum\b"),
    ],
    "feature": [
        ("tufted", r"\btufted|capitonn"), ("nailhead", r"nailhead|studded"), ("slipcover", r"slipcover"), ("down-filled", r"down[- ]filled"),
        ("storage", r"\bstorage|contenitore|rangement|almacenaje"), ("drawers", r"drawers?\b|cassett[oi]\b|tiroirs?\b|cajon(es)?\b|schublade"), ("shelves", r"shel(f|ves)\b|ripiani|estantes?\b"),
        ("doors", r"\b\d[- ]doors?\b|sliding doors?|\bante\b|\bportes\b|puertas"), ("lift-top", r"lift[- ]top"), ("extendable", r"extend(able|ing)|extension|drop[- ]leaf|allungabile|extensible"),
        ("folding", r"fold(ing|able)|pliant|plegable"), ("stackable", r"stack(able|ing)"), ("nesting", r"nesting|nest of"), ("swivel", r"swivel"), ("reclining", r"reclin(er|ing)"),
        ("rocking", r"\brock(er|ing)|glider"), ("adjustable", r"adjustable"), ("wheels", r"wheels|casters?\b|rolling"), ("armless", r"armless"), ("wingback", r"wing ?back"),
        ("high back", r"high[- ]back"), ("backless", r"backless|no[- ]back"), ("tapered legs", r"tapered"), ("hairpin legs", r"hairpin"),
        ("sleeper", r"sleeper|sofa[- ]?(cum )?bed|divano letto|convertible|schlaf"), ("chaise", r"chaise"), ("modular", r"modular|component|\bmodul"), ("headboard", r"headboard"),
        ("platform", r"platform"), ("canopy", r"canopy"), ("bunk", r"\bbunk|loft bed"), ("tripod", r"tripod"), ("arc", r"\barc\b"), ("dimmable", r"dimm"), ("usb port", r"\busb"),
        ("led", r"\bled\b"), ("edison bulb", r"edison"), ("cage", r"\bcage\b"), ("globe", r"\bglobe\b"), ("framed", r"\bframed\b|\bframe\b"), ("canvas", r"canvas"),
        ("print", r"\bprints?\b"), ("photo", r"\bphoto"), ("map", r"\bmap\b"), ("outdoor", r"outdoor|\bpatio|all[- ]weather"), ("kids", r"\bkids?\b|children|toddler|nursery|youth"),
    ],
    "pattern": [
        ("abstract", r"abstract"), ("botanical", r"botanical|floral|flower|\bleaf\b|leaves|succulent|\bpalm"), ("geometric", r"geometric|\bgeo\b|chevron|trellis|diamond"),
        ("striped", r"strip(e|ed|es)\b"), ("plaid", r"plaid|check(ed|ered)?\b|gingham"), ("medallion", r"medallion"), ("shag", r"\bshag"), ("flatweave", r"flat[- ]?weave|kilim|dhurrie"),
        ("hand-tufted", r"hand[- ]?tufted"), ("woven", r"\bwoven|braided"), ("tassels", r"tass(el|le)"),
    ],
    "style": [
        ("mid-century", r"mid[- ]century"), ("modern", r"\bmodern"), ("contemporary", r"contemporary"), ("farmhouse", r"farm[- ]?house|country"), ("rustic", r"rustic"),
        ("industrial", r"industrial"), ("traditional", r"traditional|\bclassic"), ("transitional", r"transitional"), ("coastal", r"coastal|nautical|\bbeach"), ("glam", r"\bglam"),
        ("scandinavian", r"scandinavian|nordic"), ("bohemian", r"bohemian|\bboho"), ("vintage", r"vintage|\bretro"), ("casual", r"\bcasual"), ("minimalist", r"minimal"),
        ("art deco", r"art deco\b"), ("moroccan", r"morocc?an"), ("southwestern", r"southwest|mojave|aztec"),
    ],
}
_TAGS_RE = {kind: [(tag, re.compile(pattern, re.I)) for tag, pattern in tags] for kind, tags in _TAGS.items()}
# Which kinds of word each of a listing's own fields can give. Its search keywords give none: they
# name whatever a buyer might type ("recliner" for every chair, "farmhouse" for every lamp).
_TAG_FIELDS = {"material": ["material", "fabric_type"], "finish": ["finish_type"], "shape": ["item_shape"], "pattern": ["pattern"], "style": ["style"]}
TAGS_MAX = 12


def _tags(title: str, listings: list[dict], brand: str | None) -> list[str]:
    """Up to twelve words that describe the piece: what its title says, then what the listing's
    fields for material, finish, shape, pattern and style say, then the materials in its
    description."""
    main = _main(listings) if listings else {}

    def said(name: str) -> str:
        values = _values(main, name) or _values(main, name, english=False)
        if brand == "Stone & Beam":  # its listings give "stone" as the material of a sofa
            values = [v for v in values if v.lower() != "stone"]
        return re.sub(r"stone (&|and) beam", " ", " ; ".join(values[:8]), flags=re.I)

    tags: list[str] = []

    def read(text: str, kinds) -> None:
        for kind in kinds:
            for tag, pattern in _TAGS_RE[kind]:
                if tag not in tags and pattern.search(text):
                    tags.append(tag)

    read(re.sub(r"stone (&|and) beam", " ", title, flags=re.I), _TAGS_RE)
    for kind, fields in _TAG_FIELDS.items():
        read(" ; ".join(said(name) for name in fields), [kind])
    read(said("bullet_point"), ["material"])
    return tags[:TAGS_MAX]


# --- the build ------------------------------------------------------------------------------------


def _round(v: float) -> float:
    """Millimetres; a tenth of one for a side under a centimetre (a rug's pile): the model is
    scaled to exactly this, and a thickness stated twice too large is a rug stretched upward."""
    return round(v, 4 if v < 0.01 else 3)


def _url(listing: dict) -> str | None:
    domain = (listing.get("domain_name") or "").removeprefix("fresh.").removeprefix("primenow.")
    asin = listing.get("item_id") or ""
    return f"https://www.{domain}/dp/{asin}" if domain.startswith("amazon.") and re.fullmatch(r"[A-Z0-9]{10}", asin) else None


def _other_pictures(rows: dict[str, dict], listings: dict[str, list[dict]]) -> dict[str, str]:
    """A picture for the few products whose listing names no main one: another country's
    listing's, else the first of its other pictures."""
    wanted = {
        piece_id: [i for d in listings.get(piece_id, []) for i in [d.get("main_image_id"), *(d.get("other_image_id") or [])] if i]
        for piece_id, row in rows.items() if not row.get("photo")
    }
    needed = {i for ids in wanted.values() for i in ids}
    if not needed:
        return {}
    with gzip.open(catalog_build.META / "images.csv.gz", "rt", newline="") as f:
        paths = {r["image_id"]: r["path"] for r in csv.DictReader(f) if r["image_id"] in needed}
    return {piece_id: next((paths[i] for i in ids if i in paths), "") for piece_id, ids in wanted.items()}


def _products() -> tuple[dict[str, tuple], set[str]]:
    """Every product with a model as (index row, its listings, title, whether the title is English,
    brand, the title without the brand, its picture), and the names of the design lines."""
    rows = catalog_build.index()
    listings = _listings(set(rows))
    pictures = _other_pictures(rows, listings)
    products = {}
    for piece_id, row in rows.items():
        own = listings.get(piece_id, [])
        title, english = _title(row, own)
        brand = _brand(row)
        products[piece_id] = (row, own, title, english, brand, _without_brand(title, brand, row.get("brand")), row.get("photo") or pictures.get(piece_id, ""))
    return products, _line_names([p[5] for p in products.values()])


def _piece(piece_id: str, product: tuple, lines: set[str]) -> tuple[dict | None, str]:
    """One product as an index line, or None and why it is left out. With a line comes how its
    size was settled ("model", "listing agrees", "1.41 times too large by listing and title")."""
    row, own, title, english, brand, bare, photo = product
    main = _main(own) if own else {}
    colour = (_values(main, "color") or _values(main, "color", english=False) or [row.get("color")])[0]
    x, y, z = row["extent"]
    known = catalog.BY_ID.get(piece_id)
    if not photo:
        return None, "no picture"
    if row["faces"] > MAX_FACES:
        return None, "too heavy for a browser (over 400,000 faces)"
    if not all(math.isfinite(v) and v >= 0.0005 for v in (x, y, z)):
        return None, "a model with no thickness"
    nodes = [n.get("node_name") or "" for d in own for n in d.get("node") or []]
    cls, why, sure = _classify(bare, english, colour, row["type"], nodes, x, z, y)
    if known:
        cls, why, sure = known[1], "", True
    if cls is None:
        return None, why
    assert cls in FURNITURE_CLASSES, cls
    cat = _category(cls, title)
    kids = cat == "kids"
    factor, confirmed, how = _real_size((x, z, y), row.get("inches") or {}, title, cls, kids)
    w, d, h = x / factor, z / factor, y / factor
    if factor != 1.0 and not known:  # a "sofa" 2.6 m wide that is really 2.1 m wide: its class is read again at its real size
        cls, why, sure = _classify(bare, english, colour, row["type"], nodes, w, d, h)
        if cls is None:
            return None, why
    if not confirmed and cls in ("rug", "wall_art") and (sides := _each_side((w, d, h), title, cls)):
        (w, d, h), confirmed, how = sides, True, "title, each side"
    turn = _TURN.get(piece_id, 0)
    if turn in (90, 270):  # the size is the placed piece's: it is turned first, then sized (library/sources.py)
        w, d = d, w
    if known:
        w, d, h = known[3:6]
        if h < 0.03:  # the catalog gives a rug's thickness to the centimetre ("0.01", "0.00"): the model's own, to the millimetre
            h = y * w / x
        confirmed, turn, how, factor = True, 0, "catalog", 1.0
    elif not _believable(cls, w, d, h, kids):
        return None, f"a size no {cls.replace('_', ' ')} has"
    # A sofa deeper than wide has its long back along a side, and which side is not known; a bed
    # wider than long (a bunk, a loft bed) is drawn along X and has no headboard to say which end is which.
    sideways = cls in ("sofa", "loveseat", "sectional") and d > 1.05 * w or cls == "bed" and w > 1.15 * d
    keep = _segments(bare, colour)
    line = _line(bare, cls, lines)
    if known:
        name = known[2]
    elif english:
        name = _card_name(keep, line, _colour_in_english(colour, foreign=False), cls)
    else:
        said = _gloss(title)
        label = said if said in _GLOSS_AS_LABEL else _LABEL[cls]
        name = _card_name([f"{line} {label}" if line else label], line, _colour_in_english(colour), cls)
    if not name:
        return None, "no name"
    item = {
        "id": f"abo:{piece_id}", "src": "abo", "name": name, "full": title, "brand": brand, "line": line, "cls": cls, "cat": cat,
        "w": _round(w), "d": _round(d), "h": _round(h),
    }
    if turn:
        item["turn"] = turn
    item |= {
        "img": IMAGES + photo, "faces": row["faces"], "fam": _family(brand, keep if english else [line or "", _gloss(title) or cls]),
        "color": colour, "tags": _tags(title, own, brand), "license": "CC BY 4.0", "credit": "Amazon.com", "url": _url(main) if main else None,
        "auto": bool(known and cls != "other") or (
            not known and sure and confirmed and not sideways and not _WITH_ANOTHER.search(title) and not _CORNER.search(title)
            and not (cls == "ceiling_light" and h > LOW_PENDANT)
        ),
    }
    note = f"{factor:.2f} times too large by {how}" if factor != 1.0 else f"sized by {how}" if "each" in how else f"{how} agrees" if confirmed else "model"
    return item, note


def build() -> tuple[list[dict], Counter, Counter]:
    """Every usable piece as an index line, why the others were left out, and how sizes were settled."""
    products, lines = _products()
    items: list[dict] = []
    left_out: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    for piece_id, product in products.items():
        item, note = _piece(piece_id, product, lines)
        if item is None:
            left_out[note] += 1
        else:
            items.append(item)
            sizes["rescaled" if "too large" in note or "sized by" in note else "confirmed" if "agrees" in note else "not confirmed"] += 1
    items.sort(key=lambda item: item["id"])
    return items, left_out, sizes


def explain(ids: list[str]) -> None:
    """Print what the build decides for these products, and how."""
    products, lines = _products()
    for piece_id in ids:
        item, note = _piece(piece_id, products[piece_id], lines)
        print(piece_id, "left out:" if item is None else json.dumps(item, ensure_ascii=False), "·", note)


def write(items: list[dict]) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in items)
    with open(OUT, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as f:  # no timestamp: the same input gives the same file
        f.write(text.encode("utf-8"))


def main(argv: list[str]) -> None:
    if argv[:1] == ["facing"]:
        return facing(argv[1:])
    if argv[:1] == ["explain"]:
        return explain(argv[1:])
    items, left_out, sizes = build()
    write(items)
    by_cat, by_cls = Counter(i["cat"] for i in items), Counter(i["cls"] for i in items)
    print(f"{len(items)} pieces -> {OUT} ({OUT.stat().st_size / 1e6:.2f} MB), {sum(i['auto'] for i in items)} of them for the designer to pick by itself")
    print("by section: " + ", ".join(f"{key} {by_cat[key]}" for key, _ in CATEGORIES))
    print("by class:   " + ", ".join(f"{cls} {n}" for cls, n in by_cls.most_common()))
    print("sizes:      " + ", ".join(f"{n} {what}" for what, n in sizes.most_common()) + f"; {sum('turn' in i for i in items)} turned")
    print(f"left out {sum(left_out.values())}:")
    for why, n in left_out.most_common():
        print(f"  {n:5d}  {why}")


if __name__ == "__main__":
    main(sys.argv[1:])
