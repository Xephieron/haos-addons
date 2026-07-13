# Changelog

## 1.1.1

- 🐞 Fix: Der KI-Lade-Spinner wurde beim Öffnen der „Rezept hinzufügen"-Seite
  dauerhaft angezeigt (CSS überschrieb das `hidden`-Attribut). Er erscheint jetzt
  nur noch während ein KI-Import wirklich läuft.

## 1.1.0

- 🤖 KI-Import über Ollama: Rezepte aus **Text** erstellen lassen
- 📷 KI-Import aus einem **Foto** (Vision-Modell, z.B. llava)
- 🖼️ **Bilder** zu Rezepten hochladen – Anzeige auf Karte und Detailseite
- ⚙️ Ollama-Konfiguration über die Add-on-Optionen (URL, Text- und Vision-Modell)
- 🟢 Verbindungsstatus zu Ollama auf der „Rezept hinzufügen"-Seite
- 🎨 Design an das Home-Assistant-Standarddesign angepasst (HA-Blau, Material-Look)
- Beide Wege bleiben erhalten: **KI** oder **manuell**

## 1.0.0

- Erste Version des Rezeptbuchs 🎉
- Rezepte anlegen, bearbeiten und löschen
- Suche über Titel, Kategorie und Zutaten
- Filtern nach Kategorien
- Zutaten beim Kochen abhaken
- Persistente Speicherung in `/data` (backup-fähig)
- Ingress-Integration in die Home-Assistant-Oberfläche
