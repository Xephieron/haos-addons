# Rezeptbuch

Ein schönes, schlankes Rezeptbuch direkt in Home Assistant. Lege deine Rezepte
an, durchsuche sie, filtere nach Kategorien und hake beim Kochen die Zutaten ab.

## Installation

1. Öffne in Home Assistant **Einstellungen → Add-ons → Add-on-Store**.
2. Oben rechts über das Drei-Punkte-Menü **Repositories** hinzufügen:
   `https://github.com/Xephieron/haos-addons`
3. Das Add-on **Rezeptbuch** erscheint in der Liste. Auf **Installieren** klicken.
4. Nach der Installation auf **Starten** klicken.
5. Über **In Seitenleiste anzeigen** wird das Rezeptbuch bequem im Menü verlinkt.

## Bedienung

- **Neues Rezept**: Button oben rechts. Titel, optional Emoji, Kategorie, Zeit
  und Portionen. Zutaten und Schritte werden zeilenweise eingegeben – eine
  Zeile pro Zutat bzw. pro Zubereitungsschritt.
- **Suchen**: durchsucht Titel, Kategorie und Zutaten.
- **Filtern**: über die Kategorie-Chips unter der Suchleiste.
- **Bearbeiten / Löschen**: in der Detailansicht eines Rezepts.

## Datenspeicherung

Alle Rezepte werden als `recipes.json` im persistenten `/data`-Ordner des
Add-ons gespeichert. Dieser Ordner ist Teil der Home-Assistant-Backups, deine
Rezepte sind also automatisch mitgesichert.

## Ports

Das Add-on nutzt **Ingress** – es ist ausschließlich über die
Home-Assistant-Oberfläche erreichbar und veröffentlicht keinen eigenen Port
nach außen.
