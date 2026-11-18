"""Category classification for an ingested invoice. Confirmed scope
(2026-11-18): DealerCenter's Cost Adds category is just Repair/Other.
These invoices come from an auto garage, so "repair" in practice covers
the whole range of mechanical service/maintenance work it does (oil
changes, tire/brake work, diagnostics, ...), not literally the word
"repair" — scan the line items for any of those terms; anything else
(detailing, shop supplies, admin fees, ...) is Other."""

REPAIR_KEYWORDS = frozenset(
    {
        "repair",
        "oil change",
        "oil",
        "lube",
        "tire",
        "tyre",
        "wheel",
        "rotation",
        "alignment",
        "brake",
        "rotor",
        "battery",
        "transmission",
        "engine",
        "suspension",
        "exhaust",
        "muffler",
        "clutch",
        "belt",
        "hose",
        "radiator",
        "coolant",
        "antifreeze",
        "spark plug",
        "ignition",
        "filter",
        "fluid",
        "tune-up",
        "tune up",
        "diagnostic",
        "alternator",
        "starter",
        "shock",
        "strut",
        "bearing",
        "gasket",
        "fuel pump",
        "fuel injector",
        "axle",
        "differential",
        "a/c",
        "air conditioning",
        "compressor",
        "headlight",
        "taillight",
        "bulb",
        "wiper",
        "inspection",
    }
)


def classify_category(line_items: list[dict] | None) -> str:
    for item in line_items or []:
        description = str(item.get("description") or "").lower()
        if any(keyword in description for keyword in REPAIR_KEYWORDS):
            return "Repair"
    return "Other"
