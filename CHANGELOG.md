# Änderungen

## 0.3.0

- **Neuer Name: Ladekrone.** Ladekönig heißt jetzt Ladekrone, die Krone bleibt. Repository, Integration (`ladekrone`) und Datenseite sind umbenannt.
- **Web-App** unter uekala.github.io/ladekrone/app: günstigster Preis in der Nähe per Standort oder Ortssuche, „Später günstiger“ für die nächsten 24 Stunden, Filter, Route, offline nutzbar und installierbar. Rechnet mit derselben Logik wie die Integration.
- **Ortsverzeichnis** (`v1/places.json`): Postleitzahlen und Orte aus den Adressen der Stationen, für die Suche ohne fremden Dienst.
- Die Standard-Datenquelle wird in den Optionen nicht mehr festgeschrieben. So ziehen Einträge bei einem Umzug der Daten automatisch mit.
- **Umstieg:** In HACS das Repository `uekala/ladekrone` hinzufügen und Ladekrone installieren, die alte Integration Ladekönig entfernen und die Suchgebiete neu anlegen. Entitäts-IDs richten sich wie bisher nach dem Namen des Suchgebiets.

## 0.2.0

- **Günstigster Preis in 24 h**: neuer Sensor mit dem niedrigsten Preis der nächsten 24 Stunden im Umkreis, inklusive Zeitfenster (`from`, `until`).
- **Tageszeitpreise**: Jedes Angebot zeigt, bis wann sein Preis gilt (`until`) und welcher danach kommt (`next_price`).
- **Suche, die mitfährt**: Der Mittelpunkt kann einem `device_tracker`, einer `person` oder einer `zone` folgen. Fehlende Kacheln werden beim Ortswechsel nachgeladen.
- **Betreibernamen**: Wo eine Quelle nur die Betreiber-ID meldet (z. B. `DE*EWE`), steht jetzt der Firmenname. Betrifft über 11.000 Stationen.
- **Dynamische AFIR-Preise**: Der Datensammler liest Preisaktualisierungen aus den dynamischen Angeboten.
- **Logo** für Home Assistant ab 2026.3.
- **Neue Datenseite**: freundlich gestaltet, mit einer Punktkarte Deutschlands aus den echten Daten, hellem und dunklem Modus und gut lesbarer Schrift (Atkinson Hyperlegible). Sie zeigt auch abonnierte Quellen ohne Preise und verlinkt eine Diagnose je Quelle.
- **Sprache**: „Ladenetze“ statt „Betreiber“ in Einrichtung, README und Datenseite.
- Die Vorschau auf 24 Stunden läuft außerhalb der Ereignisschleife und wird nur einmal pro Viertelstunde neu berechnet.

## 0.1.0

- Erste Version: günstigster Ad-hoc-Preis im Umkreis, Filter, mehrere Suchgebiete, Datensammler mit GitHub Pages.
