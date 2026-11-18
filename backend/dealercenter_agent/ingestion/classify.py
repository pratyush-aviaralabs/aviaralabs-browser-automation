"""Category classification for an ingested invoice. Confirmed scope
(2026-11-18): DealerCenter's Cost Adds category is just Repair/Other —
scan the line items for the word "repair"; anything else is Other."""

REPAIR_KEYWORD = "repair"


def classify_category(line_items: list[dict] | None) -> str:
    for item in line_items or []:
        description = str(item.get("description") or "").lower()
        if REPAIR_KEYWORD in description:
            return "Repair"
    return "Other"
