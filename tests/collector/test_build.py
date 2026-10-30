"""Tests für den Build-Schritt des Datensammlers."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from ladekoenig_collector.build import run

FIXTURE = Path(__file__).parent.parent / "fixtures" / "afir_static_sample.json"


def _sources(tmp_path: Path) -> Path:
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {"key": "tesla", "name": "Tesla", "license": "CC0-1.0", "offer": "1"},
                    {"key": "broken", "name": "Kaputt", "license": "CC0-1.0", "offer": "2"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_run_writes_diagnostics(tmp_path: Path) -> None:
    broken = tmp_path / "broken.xml"
    broken.write_text("<?xml version='1.0'?><x/>", encoding="utf-8")
    out = tmp_path / "public"
    args = Namespace(
        sources=str(_sources(tmp_path)),
        out=str(out),
        cache=str(tmp_path / "cache"),
        cert=None,
        key=None,
        subscriptions_env=None,
        input=[f"tesla={FIXTURE}", f"broken={broken}"],
        generated="2026-10-30T00:00:00+00:00",
    )
    assert run(args) == 0
    diag = json.loads((out / "v1" / "diag.json").read_text(encoding="utf-8"))
    assert diag["sources"]["tesla"]["stations"] > 0
    assert "XML" in diag["sources"]["broken"]["error"]
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "Abonniert, aber ohne Daten" in page
    assert "Kaputt" in page
