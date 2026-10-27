# Ladekönig

Günstigste **Ad-hoc-Ladepreise** in deiner Umgebung, direkt in Home Assistant – so wie Tankerkönig für Sprit, nur für Ladesäulen.

Seit der EU-Verordnung AFIR müssen Ladesäulenbetreiber Standorte, Leistung und Ad-hoc-Preise maschinenlesbar über den nationalen Zugangspunkt [Mobilithek](https://mobilithek.info/) bereitstellen. Ladekönig macht diese Daten in Home Assistant nutzbar – ohne dass du selbst einen Mobilithek-Zugang brauchst.

## Was es kann

- **Günstigster Preis** im Umkreis als Sensor (€/kWh), mit Betreiber, Adresse, Entfernung, Leistung, Blockiergebühr und den fünf günstigsten Alternativen
- **Tageszeitpreise** werden berücksichtigt, z. B. der günstigere Nachttarif an Superchargern
- **Filter** nach Mindestleistung, AC/DC, Stecker und Betreiber
- **Mehrere Suchgebiete**: zum Beispiel „Zuhause“, „Arbeit“ und „nur Supercharger“
- Der Preissensor trägt Koordinaten und erscheint dadurch auf der Karte

## So funktioniert es

```
Mobilithek (AFIR-Angebote der Betreiber)
   │  täglich, GitHub Action mit Client-Zertifikat
   ▼
GitHub Pages: kompakte JSON-Kacheln (0,25° × 0,25°)
   │  alle 6 Stunden, nur die Kacheln im eigenen Umkreis
   ▼
Home Assistant: Preise alle 5 Minuten neu bewertet (Zeitfenster!)
```

Deine Position verlässt Home Assistant nicht – abgerufen werden nur ganze Kacheln.

## Installation über HACS

1. HACS → Integrationen → ⋮ → **Benutzerdefinierte Repositories**
2. `https://github.com/uekala/ladekoenig` als Kategorie **Integration** hinzufügen
3. **Ladekönig** installieren und Home Assistant neu starten
4. Einstellungen → Geräte & Dienste → **Integration hinzufügen** → Ladekönig

## Einrichtung

| Feld | Bedeutung |
|------|-----------|
| Name | Name des Suchgebiets, z. B. „Zuhause“ |
| Suchgebiet | Mittelpunkt und Radius auf der Karte (0,5–50 km) |
| Mindestleistung | Alle, ab 22 kW, ab 50 kW oder ab 150 kW |
| Stromart | Alle, AC oder DC |
| Stecker | Alle, CCS, Typ 2 oder CHAdeMO |
| Nur diese Betreiber | Optional, kommagetrennt, z. B. `Tesla` oder `EnBW, Ionity` |

Alles lässt sich später über **Konfigurieren** ändern.

## Sensoren

| Sensor | Inhalt |
|--------|--------|
| Günstigster Preis | Aktuell günstigster Ad-hoc-Preis in €/kWh. Attribute: `operator`, `address`, `distance_km`, `power_kw`, `current_type`, `connectors`, `minute_fee`, `minute_fee_from`, `session_fee`, `price_updated`, `latitude`, `longitude`, `offers` (Top 5) |
| Ladestationen im Umkreis | Passende Stationen, Attribut `with_price` = davon mit gemeldetem Preis |
| Datenstand | Zeitpunkt der letzten Datenaufbereitung (Diagnose) |

## Beispiele

**Benachrichtigung, wenn der Supercharger in der Nähe günstig wird** (Eintrag mit Betreiberfilter `Tesla`):

```yaml
alias: Supercharger günstig
triggers:
  - trigger: numeric_state
    entity_id: sensor.supercharger_gunstigster_preis
    below: 0.40
actions:
  - action: notify.notify
    data:
      title: Supercharger günstig
      message: >-
        {{ state_attr('sensor.supercharger_gunstigster_preis', 'address') }}:
        {{ states('sensor.supercharger_gunstigster_preis') }} €/kWh
mode: single
```

**Dashboard-Karte mit den günstigsten Säulen:**

```yaml
type: markdown
content: >-
  {% for o in state_attr('sensor.zuhause_gunstigster_preis', 'offers') %}
  **{{ '%.2f' | format(o.price) }} €/kWh** · {{ o.operator }} · {{ o.distance_km }} km · {{ o.power_kw }} kW
  {% endfor %}
```

Die Entitäts-IDs hängen vom gewählten Namen ab.

## Datenquelle und Lizenzen

- Grundlage sind die AFIR-Datenangebote der Betreiber in der Mobilithek (DATEX II v3).
- Verarbeitet werden nur Angebote mit offener Lizenz (CC0, CC BY 4.0, DL-DE). Die genutzten Quellen und Namensnennungen stehen auf der [Datenseite](https://uekala.github.io/ladekoenig/) und im Attribut `attribution` des Preissensors.
- Die Daten werden aufbereitet: Nettopreise in Bruttopreise umgerechnet, Ladepunkte gruppiert, nach Kacheln aufgeteilt.
- **Ad-hoc-Preise** sind die Preise ohne Vertrag. Mit Ladekarte oder App kann es günstiger oder teurer sein.
- Alle Angaben ohne Gewähr.

## Grenzen (Version 0.1)

- Preise stammen aus den statischen Angeboten und werden täglich aktualisiert. Kurzfristige Preisänderungen über die dynamischen AFIR-Daten folgen später.
- Nicht alle Betreiber melden schon an die Mobilithek, und nicht jede gemeldete Säule hat einen Preis.
- Nur Deutschland.
- Angebote im XML-Format werden noch nicht gelesen, nur DATEX II v3 als JSON.
- Feiertagsregeln in Tarifen werden nicht ausgewertet.

## Datenquelle selbst betreiben

Für Forks oder eine eigene Datenquelle:

1. Auf [mobilithek.info](https://mobilithek.info/) registrieren, eine Organisation anlegen bzw. beitreten und die Rolle **Bestellmanagement** erhalten.
2. Unter „Mein Profil“ ein **Maschinenkonto** mit Zertifikat anlegen und die `.p12`-Datei samt Passwort sichern.
3. Die gewünschten Angebote aus `collector/sources.json` (Feld `offer`) **abonnieren** und die Abo-IDs notieren.
4. Im GitHub-Repository unter *Settings → Secrets and variables → Actions* anlegen:
   - `MOBILITHEK_CERT_P12_BASE64` – Ausgabe von `base64 -w0 zertifikat.p12`
   - `MOBILITHEK_CERT_PASSWORD` – Passwort des Zertifikats
   - `MOBILITHEK_SUBSCRIPTIONS` – JSON mit den Abo-IDs, z. B. `{"tesla": "1234…", "enbw": "5678…"}`
5. Unter *Settings → Pages* als Quelle **GitHub Actions** wählen.
6. Workflow **Daten sammeln** einmal manuell starten.

GitHub pausiert zeitgesteuerte Workflows in öffentlichen Repositories nach 60 Tagen ohne Aktivität. Dann den Workflow unter *Actions* wieder aktivieren.

Lokal testen mit einer heruntergeladenen Angebotsdatei:

```bash
cd collector
python -m ladekoenig_collector --sources sources.json --out ../public --input tesla=/pfad/zur/datei.json
```

## Entwicklung

```bash
pip install -r requirements_test.txt ruff
ruff check . && ruff format --check .
pytest
```

## Lizenz

MIT – siehe [LICENSE](LICENSE).
