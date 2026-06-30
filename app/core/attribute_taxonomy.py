"""SAP product-attribute spec (the controlled vocabulary the Product Management
System maps products onto).

Mirrors what SAP actually accepts: per-master-group product types (Boolean Y
attributes) + the Multiple-Choice valued attributes FABRIC / FIT / STYLE / WEIGHT
with fixed value lists. This is intentionally data-driven config — when SAP's
attribute list grows, edit these constants (later this can move to an admin
upload, like Google credentials).
"""
from __future__ import annotations

# SAP master item groups (the 8 in SAP's "List of Master Item Groups").
SAP_MASTER_GROUPS = ["ACCS", "T-SHIRTS", "SHIRTS", "SWEATS", "JACKETS",
                     "PANTS", "SHORTS", "SHOES"]

# SAP product export "Item Group" (the finer column) -> SAP master group.
# Unknown groups fall back to the value itself (already a master) or ACCS.
ITEM_GROUP_TO_MASTER = {
    "T-SHIRTS": "T-SHIRTS", "LONGSL": "T-SHIRTS", "TOPS": "T-SHIRTS",
    "POLOS": "SHIRTS", "SHIRTS": "SHIRTS", "DRESS": "SHIRTS",
    "PANTS": "PANTS", "SHORTS": "SHORTS", "SKIRT": "SHORTS",
    "JACKETS": "JACKETS",
    "HOODYS": "SWEATS", "SWEATER": "SWEATS", "KNIT": "SWEATS", "SWEATS": "SWEATS",
    "HEAD": "ACCS", "BAGS": "ACCS", "ACCS": "ACCS", "BELT": "ACCS",
    "SOCKS": "ACCS", "WALLET": "ACCS", "UNDERWEAR": "ACCS", "SWIMWEAR": "ACCS",
    "SHOES": "SHOES", "FOOTWEAR": "SHOES",
}

# SAP product-type attribute codes per master group (Boolean Y attributes).
# These are the classification targets — output is SAP-native, no remapping.
PRODUCT_TYPES_BY_GROUP = {
    "ACCS": [
        ("BACKPACK", "Backpacks"), ("BAG", "Bags"), ("BEANIE", "Beanies"),
        ("BELT", "Belts"), ("BRACELET", "Bracelets"), ("CAP", "Caps"),
        ("CHARM", "Charms"), ("EARRING", "Earrings"), ("GADGET", "Gadgets"),
        ("GLOVE", "Gloves"), ("NECKLACE", "Necklaces"), ("RING", "Rings"),
        ("SCARF", "Scarfs"), ("SOCK", "Socks"), ("SUNGLASSES", "Sunglasses"),
        ("SWIMWEAR", "Swimwear"), ("UNDERWEAR", "Underwear"),
        ("WALLET", "Wallets"), ("WATCH", "Watches"),
    ],
    "T-SHIRTS": [("TSHIRT", "T-Shirts"), ("LONGSLEEVE", "Longsleeves"),
                 ("TANKTOP", "Tanktops")],
    "SHIRTS": [("SHIRT_SS", "Shortsleeve Shirts"), ("SHIRT_LS", "Longsleeve Shirts"),
               ("POLO", "Polos"), ("DRESS", "Dresses")],
    "SWEATS": [("HOODIE", "Hoodys"), ("CREWNECK", "Crewneck Sweatshirts")],
    "JACKETS": [("JACKET", "Jackets"), ("COAT", "Coats"), ("VEST", "Vests"),
                ("BOMBERJACKET", "Bomber Jacket"), ("TRACKJACKET", "Track Jackets"),
                ("WINTERJACKET", "Winter Jackets"), ("ZIPPER", "Zip-Jackets")],
    "PANTS": [("WORK", "Work Pants"), ("CHINO", "Chino Pants"),
              ("CARGO", "Cargo Pants"), ("FIVE_POCKET", "5-Pocket Pants"),
              ("LEISURE", "Leisure Pants")],
    "SHORTS": [("WORK_SHORT", "Work Shorts"), ("CHINO_SHORT", "Chino Shorts"),
               ("CARGO_SHORT", "Cargo Shorts"), ("FIVE_POCKET_SHORT", "5 Pocket Shorts"),
               ("LEISURE_SHORT", "Leisure Shorts"), ("SKIRT", "Skirts")],
    "SHOES": [("SNEAKER", "Sneakers"), ("BOOT", "Boots"), ("SLIDE", "Slides"),
              ("SPORT", "Sport Shoes")],
}

# SAP Multiple-Choice valued attributes and their allowed values.
VALUE_LISTS = {
    "FABRIC": ["Denim", "Knit", "Viscose", "Flannel", "Seersucker", "Wool",
               "Leather", "Acetate", "Nylon"],
    "FIT": ["Regular", "Slim", "Loose", "Oversize"],
    "STYLE": ["Active", "Outdoor", "Formal", "Street", "Eco", "Contemporary",
              "Luxury", "Work"],
    "WEIGHT": ["Light", "Standard", "Heavy"],
}

VALUED_ATTRS = ["FABRIC", "FIT", "STYLE", "WEIGHT"]


def master_for_item_group(item_group: str) -> str:
    """Map a SAP product-export item group to a master group."""
    if not item_group:
        return "ACCS"
    key = str(item_group).strip().upper()
    if key in ITEM_GROUP_TO_MASTER:
        return ITEM_GROUP_TO_MASTER[key]
    if key in SAP_MASTER_GROUPS:
        return key
    return "ACCS"
