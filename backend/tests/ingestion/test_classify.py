from dealercenter_agent.ingestion.classify import classify_category


def test_classifies_repair_when_any_line_item_mentions_repair():
    line_items = [
        {"description": "Bumper repair", "quantity": 1, "unit_price": 220.0, "total": 220.0},
        {"description": "Shop supplies", "quantity": 1, "unit_price": 85.0, "total": 85.0},
    ]
    assert classify_category(line_items) == "Repair"


def test_classifies_repair_case_insensitively():
    line_items = [{"description": "FRONT BUMPER REPAIR", "quantity": 1, "unit_price": 1, "total": 1}]
    assert classify_category(line_items) == "Repair"


def test_classifies_other_when_no_line_item_mentions_repair():
    line_items = [
        {"description": "Full detail", "quantity": 1, "unit_price": 150.0, "total": 150.0},
        {"description": "Interior shampoo", "quantity": 1, "unit_price": 60.0, "total": 60.0},
    ]
    assert classify_category(line_items) == "Other"


def test_classifies_other_on_empty_or_missing_line_items():
    assert classify_category([]) == "Other"
    assert classify_category(None) == "Other"
