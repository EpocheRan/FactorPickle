from __future__ import annotations

import pytest

from research.profile import BUILTIN
from research.report.render import _filter_report_sections
from research.report_sections import DEFAULT_SECTIONS, QUICK_SECTIONS, SECTIONS, validate


def test_default_profile_covers_every_report_section():
    assert set(BUILTIN["default"]["items"]) == set(SECTIONS)
    assert set(DEFAULT_SECTIONS) == set(SECTIONS)


def test_quick_profile_is_a_subset():
    assert set(QUICK_SECTIONS) < set(DEFAULT_SECTIONS)
    assert "appendix" not in QUICK_SECTIONS


def test_validate_rejects_unknown_section():
    with pytest.raises(KeyError):
        validate(("definition", "not_a_section"))


def test_filter_removes_unselected_section():
    html = (
        "header"
        "<!--REPORT_SECTION:definition-->DEF<!--/REPORT_SECTION:definition-->"
        "<!--REPORT_SECTION:rating-->RATING<!--/REPORT_SECTION:rating-->"
        "footer"
    )
    filtered = _filter_report_sections(html, {"definition"})
    assert "DEF" in filtered
    assert "RATING" not in filtered
    assert "REPORT_SECTION" not in filtered
