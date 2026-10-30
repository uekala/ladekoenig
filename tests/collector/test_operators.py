"""Tests für die Zuordnung von Betreiber-IDs zu Klarnamen."""

from __future__ import annotations

from ladekoenig_collector.operators import (
    build_operator_names,
    operator_id,
    resolve_operator_ids,
)


def _rec(name: str, evse: str) -> tuple[dict, set[str]]:
    return {"o": name}, {evse}


def test_operator_id_detection() -> None:
    assert operator_id("DE*ISE") == "DEISE"
    assert operator_id("de*ise") == "DEISE"
    assert operator_id("DE * ISE") == "DEISE"
    assert operator_id("BOSCH") is None
    assert operator_id("WEMAG") is None
    assert operator_id("Tesla, Inc.") is None
    assert operator_id("DE*ISE GmbH") is None
    assert operator_id(None) is None


def test_names_resolved_from_other_sources() -> None:
    named = [_rec("E.ON Drive", f"DEISEE{i:04d}") for i in range(4)]
    other = [_rec("Andere GmbH", "DEISEE9999")]
    raw = [_rec("DE*ISE", "DEISEE0001"), _rec("DE*XYZ", "DEXYZE0001")]
    records = named + other + raw
    names = build_operator_names(records)
    assert names == {"DEISE": "E.ON Drive"}
    summary = resolve_operator_ids(records, names)
    assert raw[0][0]["o"] == "E.ON Drive"
    assert raw[1][0]["o"] == "DE*XYZ"
    assert summary["DEISE"] == {"stations": 1, "name": "E.ON Drive"}
    assert summary["DEXYZ"] == {"stations": 1}


def test_no_name_without_clear_majority() -> None:
    records = [_rec("A", f"DEABCE{i}") for i in range(3)] + [
        _rec("B", f"DEABCF{i}") for i in range(3)
    ]
    assert build_operator_names(records) == {}
