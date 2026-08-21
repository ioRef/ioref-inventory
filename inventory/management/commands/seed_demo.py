"""Load a small slice of real data for development.

Real rows and real counting history, so the admin shows realistic shapes,
including the awkward ones: a part below its minimum, a part never counted, a
discontinued part at zero, and empty bins.

Development only. Production is edited through the admin.
"""

import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from inventory.models import (
    Group,
    Location,
    Part,
    PriceObservation,
    StockEvent,
    Tag,
)

LOCATIONS = [
    ("soldering bench", "Soldering bench"),
    ("Connector: Board Mount", ""),
    ("Connector: Wire to Wire", ""),
    ("Connector: Misc.", ""),
    ("discontinued", "Discontinued stock"),
    # Empty bins are real rows: a shelf does not stop existing when the last
    # part leaves it.
    ("B9-R1-C1", ""),
    ("B9-R2-C7", ""),
    ("B9-R2-C8", ""),
    ("B9-R3-C2", ""),
    ("B9-R3-C3", ""),
    ("B11-R2-C3", ""),
    ("B11-R2-C6", ""),
    ("Input: Potentiometer", ""),
    ("Input: Potentiometers", ""),
    ("Input: Touch", ""),
]

# part_num, name, description, location, min, max, unit, status,
#   [(count, when)], [(count, when)]. `when` is either an int meaning "this
#   many days ago" for the hand-written rows below, or an ISO date string where
#   the real observation date is known from the CSV,
#   (price, supplier, link) or None
PARTS = [
    (
        "0002",
        "soldering heat sink",
        "heat sink, for soldering, clip type",
        "soldering bench",
        2,
        None,
        "each",
        Part.Status.ACTIVE,
        [(5, 1400), (4, 1100), (4, 950), (5, 500), (5, 300), (5, 60)],
        [(0, 60)],
        ("2.20", "Amazon", "https://www.amazon.com/gp/product/B0032UYTV6"),
    ),
    (
        "2409",
        "brass wool",
        "brass wool, for cleaning soldering tip",
        "soldering bench",
        3,
        None,
        "each",
        Part.Status.ACTIVE,
        [(1, 900), (1, 30)],
        [(10, 30)],
        ("0.90", "Amazon", "https://www.amazon.com/dp/B00NS49LPU"),
    ),
    (
        "0006",
        "soldering sponge",
        "sponge, for soldering station",
        "soldering bench",
        5,
        20,
        "each",
        Part.Status.ACTIVE,
        [(4, 1200), (3, 900), (7, 200)],
        [(3, 45)],
        None,
    ),
    (
        # Below minimum: 1 + 2 = 3 against a minimum of 5. Shows red in the list.
        "0010",
        "soldering flux pen",
        "soldering flux, pen, refillable",
        "soldering bench",
        5,
        None,
        "each",
        Part.Status.ACTIVE,
        [(1, 20)],
        [(2, 20)],
        ("7.99", "Amazon", "https://www.amazon.com/dp/B00E1L4TK6"),
    ),
    (
        "0020",
        "flexible protoboard",
        "breadboard, flex perma-proto, half-sized, for solder assembly",
        "Connector: Board Mount",
        5,
        20,
        "each",
        Part.Status.ACTIVE,
        [(10, 90)],
        [(0, 60)],
        None,
    ),
    (
        "0097",
        "small protoboard",
        "protoboard, for solder assembly, 17 rows tall, without power rails",
        "Connector: Board Mount",
        10,
        None,
        "each",
        Part.Status.ACTIVE,
        [(50, 800), (4, 600), (80, 60)],
        [(60, 60)],
        (
            "1.99",
            "Chip Quik",
            "https://www.chipquik.com/store/product_info.php?products_id=200062",
        ),
    ),
    (
        "0026",
        "2 x 8cm protoboard",
        "protoboard, 2 x 8cm, for solder assembly",
        "Connector: Board Mount",
        25,
        150,
        "each",
        Part.Status.TO_DISCONTINUE,
        [(60, 60)],
        [(50, 60)],
        ("0.08", "AliExpress", ""),
    ),
    (
        # Discontinued and at zero, a real state the UI has to render sanely.
        "0028",
        "rigid base for Arduino Uno",
        "rigid mounting base, for Arduino Uno footprint",
        "discontinued",
        5,
        None,
        "each",
        Part.Status.DISCONTINUED,
        [(10, 1600), (5, 1200), (0, 400)],
        [(0, 400)],
        None,
    ),
    (
        "0107",
        "2-circuit lever connector",
        "lever connector, Wago, 2-circuit, 28-12AWG, 32A@400V",
        "Connector: Wire to Wire",
        10,
        50,
        "each",
        Part.Status.ACTIVE,
        [(50, 500), (30, 300), (20, 45)],
        [(45, 45)],
        ("0.50", "Amazon", "https://www.amazon.com/gp/product/B07Y66R1ZQ"),
    ),
    (
        "0044",
        "butt connector",
        "connector, 3M Scotchlok IDC Butt Connector, UY2",
        "Connector: Wire to Wire",
        20,
        100,
        "each",
        Part.Status.ACTIVE,
        [(70, 60)],
        [(0, 60)],
        ("0.15", "Amazon", "https://www.amazon.com/dp/B0076AY6J8"),
    ),
    (
        # Never counted: must read as "never counted", not as zero.
        "0054",
        "10pF ceramic capacitor",
        "capacitor, ceramic, 10pF, 50V, 20%",
        "Connector: Misc.",
        25,
        None,
        "each",
        Part.Status.ACTIVE,
        [],
        [],
        None,
    ),
    # Siblings of 0054. All 33 ceramic capacitors share one component page in
    # ioref-web, so these exist to exercise that grouping.
    (
        "0056",
        "22pF ceramic capacitor",
        "capacitor, ceramic, 22pF, 50V, 20%",
        "Connector: Misc.",
        25,
        None,
        "each",
        Part.Status.ACTIVE,
        [(140, 120)],
        [(0, 120)],
        None,
    ),
    (
        "0058",
        "47pF ceramic capacitor",
        "capacitor, ceramic, 47pF, 50V, 20%",
        "Connector: Misc.",
        25,
        None,
        "each",
        Part.Status.ACTIVE,
        [(8, 90)],
        [(0, 90)],
        None,
    ),
    (
        "0060",
        "100pF ceramic capacitor",
        "capacitor, ceramic, 100pF, 50V, 20%",
        "Connector: Misc.",
        25,
        None,
        "each",
        Part.Status.ACTIVE,
        [(200, 90)],
        [(50, 90)],
        None,
    ),
]

# Potentiometers, with their real counting history parsed out of the CSV's
# inventory1..N / back_stock1..N columns. Part 0390 alone carries 17 counts
# back to 2017. Dates here are the real observation dates, not offsets.
POTENTIOMETERS = [
    (
        "0308",
        "potentiometer",
        'potentiometer, trimmer, 500Ω, 0.5W, 3/8", square, cermet',
        "Input: Potentiometers",
        10,
        1,
        "each",
        Part.Status.ACTIVE,
        [
            (20, "2017-06-21"),
            (35, "2017-12-11"),
            (35, "2018-05-24"),
            (25, "2019-01-08"),
            (30, "2019-05-20"),
            (29, "2021-06-18"),
            (30, "2021-06-30"),
            (25, "2021-12-20"),
            (25, "2022-05-25"),
            (20, "2023-05-15"),
        ],
        [(0, "2022-05-25"), (0, "2023-05-15")],
        (
            "1.35",
            "Jameco",
            "http://www.jameco.com/webapp/wcs/stores/servlet/Product_10001_10001_2161385_-1",
        ),
    ),
    (
        "0310",
        "potentiometer",
        'potentiometer, trimmer, 1kΩ, 0.25W, 19/32", vertical mount, cermet',
        "Input: Potentiometers",
        10,
        25,
        "each",
        Part.Status.ACTIVE,
        [
            (20, "2017-06-21"),
            (20, "2017-12-11"),
            (13, "2018-05-24"),
            (12, "2019-01-08"),
            (9, "2019-05-20"),
            (35, "2019-07-29"),
            (45, "2021-06-18"),
            (45, "2021-06-30"),
            (40, "2021-12-20"),
            (40, "2022-05-25"),
            (20, "2023-05-15"),
            (30, "2023-05-15"),
        ],
        [(0, "2022-05-25"), (0, "2023-05-15"), (0, "2023-05-15")],
        (
            "0.65",
            "Jameco",
            "http://www.jameco.com/webapp/wcs/stores/servlet/Product_10001_10001_94693_-1",
        ),
    ),
    (
        "0312",
        "potentiometer",
        "potentiometer, trimmer, 1kΩ, 0.5W, breadboard compatible, cermet",
        "Input: Potentiometers",
        20,
        75,
        "each",
        Part.Status.ACTIVE,
        [
            (30, "2017-06-21"),
            (30, "2017-12-11"),
            (30, "2018-05-24"),
            (20, "2019-01-08"),
            (25, "2019-05-20"),
            (50, "2019-07-29"),
            (28, "2021-06-18"),
            (28, "2021-06-30"),
            (21, "2021-12-20"),
            (24, "2022-05-25"),
        ],
        [(0, "2022-05-25")],
        (
            "1.35",
            "Jameco",
            "http://www.jameco.com/z/18STS102-Potentiometer-3-8-Sq-Cermet-1-2W-1K-Ohm-Single-Turn-with-Shaft-0-1-Lead-Spc_2118803.html",
        ),
    ),
    (
        "0314",
        "potentiometer",
        "potentiometer, trimmer, 10kΩ, 0.5W, breadboard compatible, cermet",
        "Input: Potentiometers",
        10,
        25,
        "each",
        Part.Status.ACTIVE,
        [
            (1, "2017-06-21"),
            (15, "2017-08-31"),
            (1, "2017-12-11"),
            (5, "2018-05-24"),
            (15, "2018-08-13"),
            (30, "2019-01-08"),
            (30, "2019-05-20"),
            (29, "2021-06-18"),
            (27, "2021-06-30"),
            (28, "2021-12-20"),
            (25, "2022-05-25"),
            (20, "2023-06-06"),
        ],
        [(0, "2022-05-25"), (0, "2023-05-15")],
        ("1.25", "Adafruit", "https://www.adafruit.com/products/356"),
    ),
    (
        "0316",
        "potentiometer",
        'potentiometer, trimmer, 10kΩ, 0.5W, 3/8", single turn, square, cermet',
        "Input: Potentiometers",
        10,
        25,
        "each",
        Part.Status.ACTIVE,
        [
            (50, "2017-06-21"),
            (13, "2017-12-11"),
            (20, "2018-02-26"),
            (25, "2018-05-24"),
            (12, "2019-01-08"),
            (14, "2019-05-20"),
            (40, "2019-07-29"),
            (30, "2021-06-18"),
            (29, "2021-06-30"),
            (28, "2021-12-20"),
            (28, "2022-05-25"),
            (20, "2023-05-15"),
        ],
        [(0, "2022-05-25"), (0, "2023-05-15")],
        (
            "1.25",
            "Jameco",
            "http://www.jameco.com/webapp/wcs/stores/servlet/Product_10001_10001_2118791_-1",
        ),
    ),
    (
        "0318",
        "potentiometer",
        'potentiometer, trimmer, 10kΩ, 0.25W, 19/32", vertical mount',
        "Input: Potentiometers",
        10,
        25,
        "each",
        Part.Status.ACTIVE,
        [
            (15, "2017-06-21"),
            (15, "2017-12-11"),
            (15, "2018-05-24"),
            (15, "2019-01-08"),
            (14, "2019-05-20"),
            (40, "2019-07-29"),
            (35, "2021-06-18"),
            (45, "2021-06-30"),
            (45, "2021-12-20"),
            (45, "2022-05-25"),
            (30, "2023-05-15"),
        ],
        [(0, "2022-05-25"), (0, "2023-05-15")],
        (
            "0.65",
            "Jameco",
            "http://www.jameco.com/webapp/wcs/stores/servlet/Product_10001_10001_94714_-1",
        ),
    ),
    (
        "0320",
        "potentiometer",
        'potentiometer, trimmer, 100kΩ, 0.5W, 3/8" square, single turn, through hole, cermet',
        "Input: Potentiometers",
        10,
        25,
        "each",
        Part.Status.ACTIVE,
        [
            (15, "2017-06-21"),
            (15, "2017-12-11"),
            (15, "2018-05-24"),
            (8, "2019-01-08"),
            (8, "2019-05-20"),
            (35, "2019-07-29"),
            (35, "2021-06-18"),
            (35, "2021-06-30"),
            (30, "2021-12-20"),
            (25, "2022-05-25"),
            (20, "2023-05-15"),
        ],
        [(0, "2022-05-25"), (0, "2023-05-15")],
        (
            "1.35",
            "Jameco",
            "http://www.jameco.com/webapp/wcs/stores/servlet/ProductDisplay?search_type=jamecoall&catalogId=10001&freeText=2161406&langId=-1&productId=2161406&storeId=10001&ddkey=http:StoreCatalogDrillDownView",
        ),
    ),
    (
        "0322",
        "potentiometer",
        'potentiometer, trimmer, 100kΩ, 0.25W, 19/32", vertical mount',
        "Input: Potentiometers",
        10,
        25,
        "each",
        Part.Status.ACTIVE,
        [
            (20, "2017-06-21"),
            (20, "2017-12-11"),
            (20, "2018-05-24"),
            (20, "2019-01-08"),
            (45, "2019-07-29"),
            (40, "2021-06-18"),
            (45, "2021-06-30"),
            (45, "2021-12-20"),
            (45, "2022-05-25"),
            (30, "2023-05-15"),
        ],
        [(0, "2022-05-25"), (0, "2023-05-15")],
        (
            "0.65",
            "Jameco",
            "http://www.jameco.com/webapp/wcs/stores/servlet/ProductDisplay?search_type=jamecoall&catalogId=10001&freeText=158-100K-R&langId=-1&productId=94731&storeId=10001&ddkey=http:StoreCatalogDrillDownView",
        ),
    ),
    (
        "0324",
        "potentiometer",
        "potentiometer, trimmer, 1MΩ, 0.5W, breadboard compatible",
        "Input: Potentiometers",
        10,
        25,
        "each",
        Part.Status.ACTIVE,
        [
            (15, "2017-06-21"),
            (20, "2017-12-11"),
            (15, "2018-05-24"),
            (9, "2019-01-08"),
            (5, "2020-01-09"),
            (4, "2021-06-18"),
            (4, "2021-06-30"),
            (4, "2021-12-20"),
            (4, "2022-05-25"),
            (3, "2023-02-03"),
            (20, "2023-05-15"),
        ],
        [(0, "2022-05-25"), (0, "2023-05-15")],
        (
            "1.67",
            "Unknown",
            "http://nte01.nteinc.com/nte%5CNTEMasterxRef.nsf/$all/E19B65CE55F11A3285257F5700717385?OpenDocument",
        ),
    ),
    (
        "0326",
        "potentiometer",
        'potentiometer, trimmer, 1MΩ, 0.5W, 3/8" square, single turn with shaft, cermet, breadboard compatible',
        "Input: Potentiometers",
        10,
        25,
        "each",
        Part.Status.ACTIVE,
        [
            (15, "2017-06-21"),
            (13, "2017-12-11"),
            (11, "2018-05-24"),
            (10, "2019-01-08"),
            (9, "2019-05-20"),
            (35, "2019-07-29"),
            (33, "2021-06-18"),
            (30, "2021-06-30"),
            (30, "2021-12-20"),
            (30, "2022-05-25"),
        ],
        [(0, "2022-05-25")],
        (
            "0.89",
            "Jameco",
            "http://www.jameco.com/webapp/wcs/stores/servlet/ProductDisplay?langId=-1&storeId=10001&productId=2161422&catalogId=10001&CID=MERCH",
        ),
    ),
    (
        "0328",
        "potentiometer",
        "potentiometer, 5kΩ 20%, 0.75W, single turn, 6.35mm pin, through hole",
        "B11-R2-C3",
        10,
        5,
        "each",
        Part.Status.DISCONTINUED,
        [
            (3, "2017-06-21"),
            (1, "2017-12-11"),
            (15, "2018-05-24"),
            (0, "2019-01-08"),
            (2, "2019-05-20"),
            (0, "2020-01-09"),
            (0, "2021-06-18"),
            (0, "2021-12-20"),
            (0, "2022-05-25"),
        ],
        [(30, "2019-08-13"), (0, "2021-06-30"), (0, "2022-05-25")],
        (
            "4.05",
            "Jameco",
            "http://www.jameco.com/webapp/wcs/stores/servlet/Product_10001_10001_1998141_-1",
        ),
    ),
    (
        "0332",
        "potentiometer",
        "potentiometer, trimmer, 10kΩ 20%, 0.5W, single turn, through hole",
        "Input: Potentiometers",
        10,
        25,
        "each",
        Part.Status.ACTIVE,
        [
            (10, "2017-06-21"),
            (71, "2017-11-01"),
            (80, "2017-12-11"),
            (35, "2018-05-24"),
            (30, "2019-01-08"),
            (45, "2019-05-20"),
            (50, "2021-06-18"),
            (65, "2021-12-20"),
            (65, "2022-05-25"),
        ],
        [(0, "2022-05-25")],
        (
            "1.25",
            "Jameco",
            "http://www.jameco.com/webapp/wcs/stores/servlet/ProductDisplay?langId=-1&productId=1967441&refine=1&history=mt9imul6%7CfreeText~potentiometers%5Esearch_type~jamecoall%5EprodPage~15%5Epage~SEARCH%252BNAV&catalogId=10001&freeText=3352&storeId=10001&ddkey=http:StoreCatalogDrillDownView",
        ),
    ),
    (
        "0333",
        "multiturn potentiometer",
        "potentiometer, multi-turn, 10-turn with built-in dial, 10kΩ resistance, J bend solder terminals, use 10.5mm mounting hole for panel mount",
        "Input: Potentiometer",
        5,
        None,
        "each",
        Part.Status.ACTIVE,
        [
            (0, "2021-10-21"),
            (10, "2021-11-02"),
            (8, "2021-12-20"),
            (4, "2022-05-24"),
            (7, "2023-05-15"),
        ],
        [(5, "2022-02-08"), (5, "2022-05-24"), (0, "2023-05-15")],
        (
            "6.95",
            "MPJA",
            "https://www.mpja.com/10K-Ohm-10-Turn-Variable-Resistor-with-Dial/productinfo/32676+VR/",
        ),
    ),
    (
        "0334",
        "potentiometer",
        "potentiometer, slide, 5kΩ, small, 90º mount",
        "B11-R2-C6",
        15,
        25,
        "each",
        Part.Status.DISCONTINUED,
        [
            (10, "2017-06-21"),
            (8, "2017-12-11"),
            (7, "2018-05-24"),
            (8, "2019-01-08"),
            (8, "2019-05-20"),
            (3, "2021-06-14"),
            (3, "2021-12-20"),
            (3, "2022-05-25"),
        ],
        [(0, "2022-05-25")],
        (
            "1",
            "Electronic Goldmine",
            "http://www.goldmine-elec-products.com/prodinfo.asp?number=G20137",
        ),
    ),
    (
        "0351",
        "potentiometer",
        "potentiometer, 250kΩ, 0.2W, panel mount, breadboard compatible",
        "Input: Potentiometer",
        5,
        50,
        "each",
        Part.Status.ACTIVE,
        [(25, "2022-08-24")],
        [(0, "2022-08-24")],
        (
            "1.1728",
            "Digi-Key",
            "https://www.digikey.com/en/products/detail/tt-electronics-bi/P160KN-0QC15B250K/5957457",
        ),
    ),
    (
        "0376",
        "miscellaneous potentiometers",
        "potentiometers, miscellaneous",
        "Input: Potentiometer",
        0,
        0,
        "each",
        Part.Status.ACTIVE,
        [
            (100, "2017-06-21"),
            (100, "2017-12-11"),
            (100, "2018-05-24"),
            (100, "2019-01-08"),
            (100, "2019-05-20"),
            (260, "2021-06-18"),
            (250, "2021-12-20"),
            (200, "2022-05-25"),
        ],
        [],
        None,
    ),
    (
        "0380",
        "slide potentiometer",
        "potentiometer, slide, 5kΩ, 100mW, 20mm travel, small",
        "Input: Potentiometer",
        15,
        15,
        "each",
        Part.Status.ACTIVE,
        [
            (8, "2017-06-21"),
            (20, "2017-10-11"),
            (14, "2017-12-11"),
            (15, "2018-05-24"),
            (15, "2019-01-08"),
            (7, "2019-05-20"),
            (60, "2019-07-29"),
            (35, "2021-06-18"),
            (30, "2021-12-20"),
            (25, "2022-05-25"),
            (20, "2023-05-15"),
        ],
        [(0, "2022-05-25"), (0, "2023-05-15")],
        (
            "1.69",
            "Jameco",
            "https://www.jameco.com/z/RA2043F-20-10EB1-B5K-Taiwan-Alpha-Electronic-5k-8486-PC-Mount-Linear-Taper-Slide-Potentiometer_2237731.html",
        ),
    ),
    (
        "0382",
        "slide potentiometer",
        "potentiometer, slide, 10kΩ, 200mW, 37mm travel, medium",
        "Input: Potentiometer",
        15,
        15,
        "each",
        Part.Status.ACTIVE,
        [
            (10, "2017-06-21"),
            (23, "2017-10-11"),
            (15, "2017-12-11"),
            (10, "2018-05-24"),
            (18, "2018-06-15"),
            (9, "2019-01-08"),
            (4, "2019-05-20"),
            (25, "2019-07-29"),
            (5, "2021-06-18"),
            (15, "2021-07-12"),
            (12, "2021-12-20"),
            (12, "2022-05-25"),
            (13, "2023-05-15"),
        ],
        [(20, "2021-07-12"), (20, "2022-05-25"), (10, "2022-12-09"), (0, "2023-05-15")],
        (
            "1.89",
            "Jameco",
            "https://www.jameco.com/z/RA3043F-2010EB1-B14-Taiwan-Alpha-Electronic-10k-8486-1-5W-PC-Mount-Linear-Taper-Slide-Potentiometer_2237757.html",
        ),
    ),
    (
        "0384",
        "slide potentiometer",
        "potentiometer, slide, 10kΩ, 200mW, 67mm travel, large",
        "Input: Potentiometer",
        15,
        15,
        "each",
        Part.Status.ACTIVE,
        [
            (3, "2017-06-21"),
            (10, "2017-10-11"),
            (7, "2017-12-11"),
            (16, "2017-12-21"),
            (10, "2018-05-24"),
            (12, "2018-08-17"),
            (11, "2019-01-08"),
            (3, "2019-05-20"),
            (25, "2019-07-29"),
            (3, "2021-06-18"),
            (13, "2021-07-12"),
            (11, "2021-12-20"),
            (17, "2022-06-02"),
            (20, "2022-11-30"),
            (7, "2023-05-15"),
        ],
        [
            (10, "2019-01-10"),
            (30, "2019-08-13"),
            (30, "2021-07-12"),
            (30, "2022-05-25"),
            (13, "2022-11-30"),
            (0, "2023-05-15"),
        ],
        (
            "1.95",
            "Jameco",
            "https://www.jameco.com/z/RA6043F-20-10EB1-B14-Taiwan-Alpha-Electronic-10k-8486-PC-Mount-Slide-Potentiometer_2203961.html",
        ),
    ),
    (
        "0386",
        "linear soft potentiometer",
        "potentiometer, soft, linear ribbon sensor, 10kΩ, 100mm length",
        "Input: Touch",
        5,
        8,
        "each",
        Part.Status.ACTIVE,
        [
            (1, "2017-06-21"),
            (1, "2017-12-11"),
            (0, "2018-03-06"),
            (7, "2018-03-14"),
            (7, "2018-05-24"),
            (4, "2019-01-08"),
            (0, "2019-02-13"),
            (8, "2019-02-13"),
            (4, "2019-05-20"),
            (4, "2019-08-12"),
            (2, "2020-01-09"),
            (0, "2021-06-18"),
            (10, "2021-07-12"),
            (8, "2021-10-28"),
            (2, "2021-12-20"),
            (12, "2022-01-25"),
            (10, "2022-05-25"),
            (10, "2023-05-16"),
        ],
        [
            (8, "2018-03-14"),
            (8, "2019-01-17"),
            (0, "2019-02-13"),
            (11, "2019-08-13"),
            (0, "2021-07-12"),
            (10, "2021-10-28"),
            (0, "2022-01-25"),
            (10, "2022-06-09"),
            (0, "2023-05-16"),
        ],
        (
            "10.908",
            "Digi-Key",
            "https://www.digikey.com/product-detail/en/TSP-L-0100-103-3%25-RH/905-1068-ND",
        ),
    ),
    (
        "0388",
        "potentiometer",
        "potentiometer, 1kΩ, 0.2W, panel mount, breadboard compatible",
        "Input: Potentiometer",
        40,
        50,
        "each",
        Part.Status.ACTIVE,
        [
            (15, "2017-06-21"),
            (31, "2017-08-31"),
            (9, "2017-12-11"),
            (19, "2017-12-20"),
            (22, "2018-05-24"),
            (42, "2018-06-20"),
            (20, "2019-01-08"),
            (13, "2019-05-20"),
            (1, "2020-01-09"),
            (0, "2021-06-18"),
            (31, "2021-07-26"),
            (24, "2021-12-20"),
            (17, "2022-05-25"),
            (21, "2023-02-03"),
            (20, "2023-02-27"),
            (4, "2023-05-15"),
            (24, "2023-06-09"),
        ],
        [
            (50, "2018-06-20"),
            (0, "2021-07-26"),
            (10, "2021-10-28"),
            (20, "2022-05-25"),
            (10, "2023-02-03"),
            (0, "2023-05-15"),
            (5, "2023-06-09"),
        ],
        (
            "1.227",
            "Digi-Key",
            "https://www.digikey.com/products/en?keywords=P160KN-0QD15B1K",
        ),
    ),
    (
        "0390",
        "potentiometer",
        "potentiometer, 10kΩ, 0.5W, panel mount, breadboard compatible",
        "Input: Potentiometer",
        40,
        20,
        "each",
        Part.Status.ACTIVE,
        [
            (9, "2017-06-21"),
            (29, "2017-08-31"),
            (5, "2017-12-11"),
            (15, "2017-12-20"),
            (3, "2018-05-24"),
            (30, "2018-06-20"),
            (15, "2019-01-08"),
            (12, "2019-05-20"),
            (1, "2020-01-09"),
            (100, "2020-01-09"),
            (90, "2021-05-20"),
            (48, "2021-12-20"),
            (40, "2022-05-25"),
            (20, "2023-02-03"),
            (25, "2023-02-24"),
            (15, "2023-04-07"),
            (30, "2023-05-15"),
        ],
        [
            (180, "2018-06-20"),
            (140, "2018-09-04"),
            (110, "2019-01-08"),
            (90, "2019-01-22"),
            (20, "2019-01-25"),
            (200, "2019-02-20"),
            (90, "2020-01-22"),
            (0, "2020-11-16"),
            (73, "2021-04-26"),
            (22, "2021-07-12"),
            (72, "2022-02-08"),
            (50, "2022-05-25"),
            (50, "2023-02-03"),
            (25, "2023-02-24"),
            (0, "2023-02-27"),
            (12, "2023-04-07"),
            (0, "2023-05-15"),
            (50, "2023-06-17"),
        ],
        (
            "1.1818",
            "Digi-Key",
            "http://www.digikey.com/product-detail/en/tt-electronics-bi/P160KN-0QD15B10K/987-1308-ND/2408885",
        ),
    ),
    (
        "0392",
        "potentiometer",
        "potentiometer, 100kΩ, 0.5W, panel mount, breadboard compatible",
        "Input: Potentiometer",
        40,
        50,
        "each",
        Part.Status.ACTIVE,
        [
            (20, "2017-06-21"),
            (15, "2017-12-11"),
            (12, "2018-05-24"),
            (11, "2019-01-08"),
            (1, "2019-05-20"),
            (1, "2020-01-09"),
            (0, "2021-06-18"),
            (0, "2021-12-20"),
            (24, "2022-05-25"),
            (7, "2023-05-15"),
        ],
        [(0, "2022-01-25"), (0, "2022-05-25"), (0, "2023-05-15")],
        ("0.95", "Adafruit", "https://www.adafruit.com/products/1831"),
    ),
    (
        "0394",
        "circular soft potentiometer",
        'potentiometer, soft, circular, 10kΩ, outer diameter 2.2", inner diameter 1.4"',
        "Input: Touch",
        10,
        6,
        "each",
        Part.Status.ACTIVE,
        [
            (3, "2017-06-21"),
            (4, "2017-08-31"),
            (0, "2017-12-11"),
            (4, "2017-12-20"),
            (5, "2018-03-06"),
            (6, "2018-05-24"),
            (8, "2018-08-13"),
            (4, "2019-01-08"),
            (6, "2019-02-13"),
            (4, "2019-04-10"),
            (4, "2019-05-20"),
            (3, "2021-06-18"),
            (5, "2021-07-29"),
            (1, "2021-12-20"),
            (6, "2022-01-25"),
            (6, "2022-05-25"),
            (11, "2022-06-06"),
            (10, "2023-03-26"),
            (10, "2023-05-16"),
        ],
        [
            (4, "2017-08-31"),
            (2, "2018-01-18"),
            (2, "2019-01-17"),
            (2, "2021-07-12"),
            (10, "2021-07-29"),
            (5, "2022-01-25"),
            (5, "2022-05-25"),
            (10, "2022-06-06"),
            (2, "2023-05-16"),
        ],
        ("12.95", "Adafruit", "https://www.adafruit.com/products/1069"),
    ),
    (
        "1088",
        "Grove potentiometer",
        "potentiometer, Grove connector, 10kΩ, 300º range",
        "discontinued",
        0,
        15,
        "each",
        Part.Status.DISCONTINUED,
        [
            (12, "2017-06-21"),
            (12, "2017-12-11"),
            (11, "2018-05-24"),
            (10, "2019-01-08"),
            (10, "2019-05-22"),
            (10, "2021-06-17"),
        ],
        [],
        (
            "2.9",
            "SEEEDSTUDIO",
            "http://www.seeedstudio.com/Grove-Rotary-Angle-Sensor-p-770.html",
        ),
    ),
]

PARTS = PARTS + POTENTIOMETERS


# The source vocabulary is not clean. Singular and plural spellings coexist,
# and a few values describe how a part is used rather than what it is; those
# become tags instead.
GROUP_ALIASES = {
    "potentiometer": "Potentiometers",
    "potentiometers": "Potentiometers",
    "capacitors": "Capacitors",
    "board mount": "Board Mount",
    "wire to wire": "Wire to Wire",
    "misc.": "Misc.",
}
# Fine values that are a use, not a type. The part still gets a real group.
USE_AS_TAG = {"touch": "touch", "lending": "lending"}


def _classify(location_name):
    """Split a location string into (group, tag names).

    Some carry a classification as well as a place, shaped "Input:
    Potentiometers". Only the fine half is inventory's business; the macro half
    is a teaching taxonomy that ioref-web owns.

    Returns (None, []) for locations that really are just places.
    """
    if ":" not in location_name:
        return None, []
    _, _, fine = location_name.partition(":")
    fine = fine.strip()
    if not fine:
        return None, []

    key = fine.lower()
    if key in USE_AS_TAG:
        # e.g. "Input: Touch"; a soft potentiometer is still a potentiometer.
        return None, [USE_AS_TAG[key]]

    name = GROUP_ALIASES.get(key, fine)
    group, _ = Group.objects.get_or_create(slug=slugify(name), defaults={"name": name})
    return group, []


def _observed(now, when):
    """Resolve a count's timestamp.

    An int is an offset in days, used by the hand-written rows so the demo data
    stays recent. A string is a real observation date parsed from the CSV, kept
    as-is because the point of those rows is that the history is genuine.
    """
    if isinstance(when, int):
        return now - datetime.timedelta(days=when)
    date = datetime.date.fromisoformat(when)
    return timezone.make_aware(datetime.datetime.combine(date, datetime.time(12, 0)))


class Command(BaseCommand):
    help = "Load a small slice of real data for development."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing parts and locations first.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            StockEvent.objects.all().delete()
            PriceObservation.objects.all().delete()
            Part.objects.all().delete()
            Location.objects.all().delete()
            # Children first: parent is PROTECTed.
            Group.objects.all().delete()
            Tag.objects.all().delete()
            self.stdout.write("Flushed existing inventory.")

        locations = {
            code: Location.objects.get_or_create(code=code, defaults={"name": name})[0]
            for code, name in LOCATIONS
        }

        now = timezone.now()

        for (
            number,
            name,
            description,
            location,
            minimum,
            maximum,
            unit,
            status,
            inventory,
            backstock,
            price,
        ) in PARTS:
            group, tag_names = _classify(location)
            # A soft pot filed under "Touch" is still a potentiometer.
            if group is None and "potentiometer" in name.lower():
                group, _ = Group.objects.get_or_create(
                    slug="potentiometers", defaults={"name": "Potentiometers"}
                )

            part, _ = Part.objects.update_or_create(
                part_number=number,
                defaults={
                    "short_name": name,
                    "description": description,
                    "group": group,
                    "location": locations[location],
                    "min_quantity": minimum,
                    "max_quantity": maximum,
                    "unit": unit,
                    "status": status,
                },
            )
            part.tags.set(
                Tag.objects.get_or_create(slug=slugify(t), defaults={"name": t})[0]
                for t in tag_names
            )
            part.stock_events.all().delete()
            part.price_observations.all().delete()

            for kind, counts in (
                (StockEvent.Kind.INVENTORY, inventory),
                (StockEvent.Kind.BACKSTOCK, backstock),
            ):
                for quantity, when in counts:
                    StockEvent.objects.create(
                        part=part,
                        kind=kind,
                        quantity=quantity,
                        observed_at=_observed(now, when),
                    )

            if price:
                amount, supplier, link = price
                PriceObservation.objects.create(
                    part=part,
                    price=amount,
                    supplier=supplier,
                    purchase_link=link,
                    observed_at=now - datetime.timedelta(days=60),
                )

        restock = [p for p in Part.objects.all() if p.needs_restock]
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {Part.objects.count()} parts in "
                f"{Group.objects.count()} groups ({Tag.objects.count()} tags) across "
                f"{Location.objects.count()} locations "
                f"({StockEvent.objects.count()} stock events, "
                f"{PriceObservation.objects.count()} prices). "
                f"{len(restock)} below minimum."
            )
        )
