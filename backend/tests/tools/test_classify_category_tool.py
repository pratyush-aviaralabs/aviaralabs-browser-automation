import pytest

from dealercenter_agent.tools.base import ToolStatus
from dealercenter_agent.tools.classify_category_tool import ClassifyCategoryTool


@pytest.mark.parametrize(
    "line_items",
    [
        ["Labor - brake job"],
        ["Oil change service"],
        ["Tire rotation"],
        ["Pre-purchase inspection"],
        ["Install - new radio"],
        ["Diagnostic fee"],
        ["Lube and filter"],
        ["Brake pad", "Labor"],  # one matching line among several is enough
    ],
)
def test_classifies_as_repair_when_any_line_matches_keyword(line_items):
    result = ClassifyCategoryTool().run(None, line_items=line_items)
    assert result.status == ToolStatus.SUCCESS
    assert result.data["category"] == "Repair"


@pytest.mark.parametrize(
    "line_items",
    [
        ["Brake pad"],
        ["Air filter", "Wiper blades"],
        [],
    ],
)
def test_classifies_as_other_when_no_keyword_matches(line_items):
    result = ClassifyCategoryTool().run(None, line_items=line_items)
    assert result.data["category"] == "Other"


def test_classification_is_case_insensitive():
    result = ClassifyCategoryTool().run(None, line_items=["LABOR CHARGE"])
    assert result.data["category"] == "Repair"
