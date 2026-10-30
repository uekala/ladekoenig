"""Öffentliche Datenseite (GitHub Pages) für Ladekrone.

Eine einzige statische HTML-Seite ohne externe Abhängigkeiten. Schriften liegen
als Dateien neben der Seite (keine Abrufe bei Google), das Herzstück ist eine
Punktkarte aus den echten Kacheln: Punktgröße = Ladestationen, Farbe = Anteil
mit gemeldetem Preis.
"""

from __future__ import annotations

import html
import math
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ASSETS = Path(__file__).parent / "site"
LOCAL_TZ = ZoneInfo("Europe/Berlin")
REPO_URL = "https://github.com/uekala/ladekrone"
GERMANY = (47.2, 55.1, 5.8, 15.1)  # Breite min/max, Länge min/max
MONTHS = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]

CROWN = """<svg class="crown" viewBox="0 0 256 256" aria-hidden="true">
<path d="M14 206 L34 78 L88 132 L128 44 L168 132 L222 78 L242 206 Z" fill="#F7B500"
 stroke="#B87A00" stroke-width="10" stroke-linejoin="round"/>
<rect class="crown-dark" x="14" y="200" width="228" height="36" rx="12"/>
<circle class="crown-dark" cx="34" cy="74" r="16"/>
<circle class="crown-dark" cx="128" cy="36" r="18"/>
<circle class="crown-dark" cx="222" cy="74" r="16"/>
<path d="M141 96 L100 160 H127 L115 206 L160 138 H133 Z" fill="#2B1B3D"
 stroke="#2B1B3D" stroke-width="6" stroke-linejoin="round"/>
</svg>"""


def _num(value: int | float) -> str:
    return f"{int(value):,}".replace(",", ".")


def _stand(generated: str) -> str:
    try:
        moment = datetime.fromisoformat(generated).astimezone(LOCAL_TZ)
    except (TypeError, ValueError):
        return html.escape(str(generated))
    return f"{moment.day}. {MONTHS[moment.month - 1]} {moment.year}, {moment:%H:%M} Uhr"


# --------------------------------------------------------------------------
# Punktkarte aus den Kacheln
# --------------------------------------------------------------------------


def _mix(a: str, b: str, t: float) -> str:
    ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb, strict=True))


def dot_map(tile_counts: dict[str, tuple[int, int]], tile_deg: float) -> str:
    """SVG-Punktkarte. ``tile_counts``: Kachel -> (Stationen, davon mit Preis)."""
    cells = []
    for key, (stations, priced) in tile_counts.items():
        try:
            y, x = (int(part) for part in key.split("_"))
        except ValueError:
            continue
        lat, lon = (y + 0.5) * tile_deg, (x + 0.5) * tile_deg
        # Nur Deutschland zeigen; einzelne Stationen mit falschen Koordinaten
        # würden den Ausschnitt sonst auf ganz Europa aufziehen.
        if stations and GERMANY[0] <= lat <= GERMANY[1] and GERMANY[2] <= lon <= GERMANY[3]:
            cells.append((y, x, stations, priced))
    if not cells:
        return ""
    ys = [c[0] for c in cells]
    xs = [c[1] for c in cells]
    mid_lat = (min(ys) + max(ys) + 1) / 2 * tile_deg
    squash = math.cos(math.radians(mid_lat))  # Längengrade schrumpfen nach Norden
    step = 10.0
    width = (max(xs) - min(xs) + 1) * step * squash
    height = (max(ys) - min(ys) + 1) * step
    peak = max(c[2] for c in cells)
    dots = []
    for y, x, stations, priced in sorted(cells, key=lambda c: c[2]):
        cx = (x - min(xs) + 0.5) * step * squash
        cy = (max(ys) - y + 0.5) * step
        radius = 0.9 + 3.4 * math.sqrt(stations / peak)
        share = priced / stations
        color = _mix("#A58BF5", "#18A57A", share)
        label = f"{_num(stations)} Stationen, {round(share * 100)} % mit Preis"
        delay = (max(ys) - y) * 22  # einmaliges Einblenden von Nord nach Süd
        dots.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{radius:.2f}" fill="{color}" '
            f'style="--d:{delay}"><title>{label}</title></circle>'
        )
    return (
        f'<svg class="map" viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
        f'aria-labelledby="map-title">'
        f'<title id="map-title">Ladestationen in Deutschland je Kachel</title>'
        + "".join(dots)
        + "</svg>"
    )


# --------------------------------------------------------------------------
# Seite
# --------------------------------------------------------------------------


def _source_rows(sources: list[dict[str, Any]]) -> str:
    rows = []
    for source in sorted(sources, key=lambda s: s.get("stations", 0), reverse=True):
        stations = int(source.get("stations", 0))
        priced = int(source.get("with_price", 0))
        if not stations:
            continue
        share = priced / stations
        name = html.escape(source.get("attribution") or source.get("name") or source["key"])
        rows.append(
            "<tr>"
            f'<th scope="row">{name}</th>'
            f'<td class="lic">{html.escape(source.get("license", ""))}</td>'
            f'<td class="n">{_num(stations)}</td>'
            f'<td class="share"><span class="bar" aria-hidden="true">'
            f'<span style="width:{share * 100:.1f}%"></span></span>'
            f'<span class="n">{round(share * 100)} %</span></td>'
            "</tr>"
        )
    return "\n".join(rows)


def _waiting(sources: list[dict[str, Any]], diagnostics: dict[str, dict[str, Any]]) -> str:
    items = []
    seen = set()
    for key, diag in diagnostics.items():
        name = html.escape(diag.get("name", key))
        if diag.get("error"):
            reason = "hat noch keine Daten geliefert"
            if "XML" in diag["error"]:
                reason = "liefert ein Format, das Ladekrone noch nicht liest"
            items.append(f"<li><strong>{name}</strong> {reason}</li>")
            seen.add(key)
    for source in sources:
        if source["key"] in seen:
            continue
        if source.get("stations") and not source.get("with_price"):
            name = html.escape(source.get("name") or source["key"])
            stations = _num(source["stations"])
            items.append(
                f"<li><strong>{name}</strong> meldet {stations} Stationen, aber keine Preise</li>"
            )
    if not items:
        return ""
    return (
        '<section class="waiting" aria-labelledby="waiting-title">'
        '<h2 id="waiting-title">Noch ohne Preise</h2>'
        "<p>Diese Ladenetze sind abonniert, liefern aber noch keine Ad-hoc-Preise. "
        "Sobald sie es tun, erscheinen sie hier und in Home Assistant von selbst.</p>"
        f"<ul>{''.join(items)}</ul></section>"
    )


CSS = """
@font-face{font-family:"Atkinson";src:url(assets/fonts/atkinson-400.woff2) format("woff2");
font-weight:400;font-display:swap}
@font-face{font-family:"Atkinson";src:url(assets/fonts/atkinson-700.woff2) format("woff2");
font-weight:700;font-display:swap}
@font-face{font-family:"Bricolage";src:url(assets/fonts/bricolage.woff2) format("woff2");
font-weight:200 800;font-display:swap}
:root{--ink:#2B1B3D;--muted:#64547A;--paper:#FFFFFF;--wash:#F5F0FF;--line:#E4DAF7;
--gold:#F7B500;--link:#5B3FD0;--mint:#18A57A;--track:#ECE5FB;--crown-dark:#2B1B3D}
@media (prefers-color-scheme:dark){:root{--ink:#F4EEFF;--muted:#BFB2D6;--paper:#1F1630;
--wash:#2A2040;--line:#3D3158;--link:#C3B3FF;--mint:#3FD4A3;--track:#3A2E55;--crown-dark:#E9E2F7}}
*{box-sizing:border-box}
html{color-scheme:light dark}
body{margin:0;background:var(--paper);color:var(--ink);
font:1.125rem/1.6 "Atkinson",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--link);text-underline-offset:.18em}
a:focus-visible,.btn:focus-visible{outline:3px solid var(--gold);outline-offset:3px;
border-radius:6px}
.wrap{max-width:72rem;margin:0 auto;padding:0 1.25rem}
header.top{display:flex;align-items:center;justify-content:space-between;padding:1.1rem 0}
.brand{display:flex;align-items:center;gap:.6rem;font:700 1.35rem "Bricolage",sans-serif;
color:var(--ink);text-decoration:none;letter-spacing:-.01em}
.crown{width:2.2rem;height:2.2rem}.crown-dark{fill:var(--crown-dark)}
.hero{background:var(--wash);border-radius:2rem;padding:clamp(1.5rem,4vw,3rem);
display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:clamp(1.5rem,4vw,3rem);
align-items:center}
h1{font:750 clamp(2.2rem,5.2vw,3.7rem)/1.04 "Bricolage",sans-serif;letter-spacing:-.025em;
margin:0 0 1rem;font-variation-settings:"wdth" 92}
.lead{font-size:1.2rem;max-width:34rem;margin:0 0 1.5rem}
.actions{display:flex;flex-wrap:wrap;gap:.75rem;margin-bottom:1.5rem}
.btn{display:inline-block;padding:.75rem 1.3rem;border-radius:999px;font-weight:700;
text-decoration:none;border:2px solid var(--ink);color:var(--ink)}
.btn.primary{background:var(--gold);border-color:var(--gold);color:#2B1B3D}
.fresh{color:var(--muted);margin:0;font-size:1rem}
.fresh strong{color:var(--ink)}
figure{margin:0}
.map{width:100%;height:auto;max-height:34rem;display:block}
figcaption{display:flex;flex-wrap:wrap;gap:.4rem 1.2rem;font-size:.95rem;color:var(--muted);
margin-top:.75rem}
.key{display:inline-flex;align-items:center;gap:.45rem}
.key i{display:inline-block;border-radius:50%;width:.6rem;height:.6rem}
.dot.few{background:#A58BF5}.dot.many{background:#18A57A}
main section{padding:3rem 0 0}
h2{font:700 1.7rem/1.2 "Bricolage",sans-serif;letter-spacing:-.015em;margin:0 0 .75rem}
.intro{max-width:44rem;margin:0 0 1.5rem;color:var(--muted)}
.table{overflow-x:auto;border:1px solid var(--line);border-radius:1.1rem}
table{border-collapse:collapse;width:100%;min-width:34rem}
thead th{font-weight:700;text-align:left;font-size:.95rem;color:var(--muted);
padding:.8rem 1rem;border-bottom:1px solid var(--line)}
tbody th,tbody td{padding:.7rem 1rem;border-bottom:1px solid var(--line);text-align:left;
font-weight:400}
tbody tr:last-child th,tbody tr:last-child td{border-bottom:0}
tbody th{font-weight:700}
.n,.lic,.fresh strong{font-family:"Bricolage",sans-serif;font-variant-numeric:tabular-nums}
.n{white-space:nowrap}
td.n,thead th.n{text-align:right}
.share{display:flex;align-items:center;gap:.7rem;min-width:11rem}
.bar{flex:1;height:.6rem;background:var(--track);border-radius:999px;overflow:hidden}
.bar span{display:block;height:100%;background:var(--mint);border-radius:999px}
.waiting ul{padding-left:1.2rem;margin:0}.waiting li{margin:.3rem 0}
.steps{list-style:none;padding:0;margin:0;display:grid;gap:1rem;
grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));counter-reset:step}
.steps li{counter-increment:step;background:var(--wash);border-radius:1.25rem;padding:1.2rem 1.3rem}
.steps li::before{content:counter(step);display:grid;place-items:center;width:2.1rem;height:2.1rem;
border-radius:50%;background:var(--gold);color:#2B1B3D;font:700 1.1rem "Bricolage",sans-serif;
margin-bottom:.6rem}
code{font-size:.95em;background:var(--track);padding:.1em .35em;border-radius:.35em;
overflow-wrap:anywhere}
footer{margin:3.5rem 0 2rem;padding-top:1.5rem;border-top:1px solid var(--line);
color:var(--muted);font-size:.95rem}
footer p{max-width:48rem;margin:.4rem 0}
@media (max-width:760px){.hero{grid-template-columns:1fr}.map{max-height:24rem}}
@media (prefers-reduced-motion:no-preference){
.map circle{animation:pop .5s ease-out both;animation-delay:calc(var(--d,0)*1ms)}
@keyframes pop{from{opacity:0}to{opacity:1}}}
"""


def render_index(
    meta: dict[str, Any],
    diagnostics: dict[str, dict[str, Any]],
    tile_counts: dict[str, tuple[int, int]],
) -> str:
    sources = meta.get("sources") or []
    priced_total = sum(int(s.get("with_price", 0)) for s in sources)
    with_data = sum(1 for s in sources if s.get("stations"))
    stand = _stand(meta.get("generated", ""))
    svg = dot_map(tile_counts, float(meta.get("tile_deg", 0.25)))
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ladekrone – günstig laden in deiner Nähe</title>
<meta name="description" content="Ad-hoc-Ladepreise aus den AFIR-Meldungen der Ladenetze,
aufbereitet für Home Assistant.">
<link rel="icon" href="assets/icon.svg" type="image/svg+xml">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header class="top">
<a class="brand" href="{REPO_URL}">{CROWN}Ladekrone</a>
<a href="{REPO_URL}">Quellcode auf GitHub</a>
</header>
<main>
<section class="hero" aria-labelledby="title">
<div>
<h1 id="title">Laden zum besten Preis, ganz in deiner Nähe.</h1>
<p class="lead">Ladekrone zeigt dir in Home Assistant, wo du gerade am günstigsten
ohne Vertrag laden kannst, und wann es später noch günstiger wird. Die Preise melden
die Ladenetze selbst, so wie es die EU-Verordnung AFIR verlangt.</p>
<div class="actions">
<a class="btn primary" href="#loslegen">Ladekrone einrichten</a>
<a class="btn" href="#quellen">Woher die Preise kommen</a>
</div>
<p class="fresh">Stand: <strong>{stand}</strong>.
{_num(meta.get("stations", 0))} Ladestationen mit {_num(meta.get("points", 0))} Ladepunkten
aus {with_data} Quellen, davon {_num(priced_total)} Stationen mit Preis.</p>
</div>
<figure>
{svg}
<figcaption>
<span class="key"><i class="dot few"></i>wenige Preise</span>
<span class="key"><i class="dot many"></i>fast überall Preise</span>
<span>Je größer der Punkt, desto mehr Ladestationen.</span>
</figcaption>
</figure>
</section>

<section id="quellen" aria-labelledby="quellen-title">
<h2 id="quellen-title">Woher die Preise kommen</h2>
<p class="intro">Alle Daten stammen aus den AFIR-Meldungen der Ladenetze an den nationalen
Zugangspunkt <a href="https://mobilithek.info/">Mobilithek</a>. Ladekrone nutzt nur
Angebote mit offener Lizenz, rechnet Netto- in Bruttopreise um und fasst doppelte
Stationen zusammen. Der Balken zeigt, welcher Anteil der Stationen einen Preis meldet.</p>
<div class="table" role="region" aria-labelledby="quellen-title" tabindex="0">
<table>
<thead><tr><th scope="col">Quelle</th><th scope="col">Lizenz</th>
<th scope="col" class="n">Stationen</th><th scope="col">Mit Preis</th></tr></thead>
<tbody>
{_source_rows(sources)}
</tbody>
</table>
</div>
</section>

{_waiting(sources, diagnostics)}

<section id="loslegen" aria-labelledby="loslegen-title">
<h2 id="loslegen-title">So legst du los</h2>
<p class="intro">Du brauchst Home Assistant mit HACS. Einen eigenen Zugang zur Mobilithek
brauchst du nicht.</p>
<ol class="steps">
<li>In HACS unter <em>Benutzerdefinierte Repositories</em> die Adresse
<code>github.com/uekala/ladekrone</code> als Integration hinzufügen.</li>
<li><strong>Ladekrone</strong> installieren und Home Assistant neu starten.</li>
<li>Unter <em>Geräte &amp; Dienste</em> Ladekrone hinzufügen und dein Suchgebiet
auf der Karte wählen. Mehrere Gebiete sind möglich.</li>
</ol>
</section>
</main>
<footer>
<p>Ad-hoc-Preise gelten ohne Vertrag. Mit Ladekarte oder App kann es günstiger oder teurer
sein. Alle Angaben ohne Gewähr.</p>
<p>Daten: Mobilithek, AFIR-Angebote der Ladenetze unter den oben genannten Lizenzen.
<a href="v1/diag.json">Diagnose je Quelle</a> und
<a href="v1/meta.json">Metadaten</a> als JSON.</p>
<p>Schriften: Atkinson Hyperlegible (Braille Institute) und Bricolage Grotesque,
beide unter der <a href="assets/fonts/OFL-atkinson.txt">SIL Open Font License</a>.</p>
</footer>
</div>
</body>
</html>
"""


def copy_assets(out_dir: Path) -> None:
    """Schriften und Symbol neben die Seite legen."""
    target = out_dir / "assets"
    shutil.copytree(ASSETS, target, dirs_exist_ok=True)
    (target / "icon.svg").write_text(
        CROWN.replace('class="crown" ', 'xmlns="http://www.w3.org/2000/svg" ')
        .replace(' aria-hidden="true"', "")
        .replace('class="crown-dark"', 'fill="#2B1B3D"'),
        encoding="utf-8",
    )
