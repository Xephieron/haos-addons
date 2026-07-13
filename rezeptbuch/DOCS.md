# Rezeptbuch

Ein schönes, schlankes Rezeptbuch direkt in Home Assistant. Lege Rezepte an –
**manuell**, **per KI aus Text** oder **per KI aus einem Foto** (über Ollama) –
durchsuche sie, filtere nach Kategorien, füge Bilder hinzu und hake beim Kochen
die Zutaten ab.

## Installation

1. Öffne in Home Assistant **Einstellungen → Add-ons → Add-on-Store**.
2. Oben rechts über das Drei-Punkte-Menü **Repositories** hinzufügen:
   `https://github.com/Xephieron/haos-addons`
3. Das Add-on **Rezeptbuch** erscheint in der Liste. Auf **Installieren** klicken.
4. Nach der Installation auf **Starten** klicken.
5. Über **In Seitenleiste anzeigen** wird das Rezeptbuch bequem im Menü verlinkt.

## Konfiguration (Ollama für die KI-Funktionen)

Die KI-Funktionen nutzen [Ollama](https://ollama.com). Trage im Tab
**Konfiguration** des Add-ons ein:

| Option | Beschreibung | Beispiel |
| ------ | ------------ | -------- |
| `ollama_url` | Adresse deiner Ollama-Instanz | `http://homeassistant.local:11434` |
| `ollama_model` | Text-Modell (für „Aus Text") | `llama3.2` |
| `ollama_vision_model` | Vision-Modell (für „Aus Foto") | `llava` |
| `todo_entity` | Ziel-Liste für „Zur Einkaufsliste" | `todo.shopping_list` |

Hinweise:
- Läuft Ollama als eigenes Add-on/Container auf demselben Host, ist die URL
  meist `http://<host-ip>:11434` oder `http://homeassistant.local:11434`.
- Für die Foto-Erkennung wird ein **Vision-Modell** benötigt (z.B. `llava`
  oder `llama3.2-vision`). Vorher in Ollama laden: `ollama pull llava`.
- Auf der Seite **Rezept hinzufügen** zeigt ein Statuspunkt, ob die Verbindung
  zu Ollama steht und welche Modelle verfügbar sind.

Ohne Ollama funktioniert das Rezeptbuch weiterhin – dann nutzt du einfach die
**manuelle** Eingabe.

## Bedienung

Über **＋ Neues Rezept** stehen drei Wege bereit:

- **Manuell eingeben** – Rezept selbst Feld für Feld ausfüllen.
- **Aus Text (KI)** – Rezept-Text einfügen; die KI erkennt Titel, Zutaten und
  Schritte und füllt das Formular vor. Du prüfst und speicherst.
- **Aus Foto (KI)** – ein Foto (z.B. aus einem Kochbuch) hochladen; die KI
  überträgt das Rezept und legt das Foto gleich als Bild an.

Nach einem KI-Import landest du immer auf dem Bearbeiten-Formular – die KI macht
nur einen Vorschlag, gespeichert wird erst, wenn du bestätigst.

Weitere Funktionen: **Suche** (Titel, Kategorie, Zutaten), **Kategorie-Filter**,
**Bild-Upload** pro Rezept, Zutaten beim Kochen **abhaken**.

### Einkaufsliste

In der Rezept-Detailansicht kannst du eine **einzelne Zutat** über den „＋"-Knopf
oder **alle Zutaten** über „🛒 Alle zur Einkaufsliste" mit einem Klick auf deine
Home-Assistant-Einkaufsliste setzen. Voraussetzung:

- Die **Einkaufsliste**- bzw. eine **To-do-Listen**-Integration ist in Home
  Assistant eingerichtet (die Standard-Entität heißt `todo.shopping_list`).
- Über die Option `todo_entity` lässt sich auch eine andere Liste ansteuern,
  z.B. eine Bring!- oder Google-Keep-Liste, sofern sie als To-do-Entität in HA
  vorhanden ist.

Das Add-on greift dafür über die Home-Assistant-Core-API (`homeassistant_api`)
auf den Dienst `todo.add_item` zu – eine eigene Integration ist nicht nötig.

### Video

Zu jedem Rezept kannst du ein Video hinterlegen – entweder als **YouTube-Link**
(wird direkt eingebettet) oder als **eigenes hochgeladenes Video** (MP4/WebM).
Ein hochgeladenes Video hat Vorrang vor einem Link.

## Datenspeicherung

Rezepte liegen als `recipes.json`, Bilder im Ordner `images/` – beides im
persistenten `/data`-Ordner des Add-ons. Dieser ist Teil der
Home-Assistant-Backups, deine Rezepte und Bilder sind also automatisch
mitgesichert.

## Ports

Das Add-on nutzt **Ingress** – es ist über die Home-Assistant-Oberfläche
erreichbar und veröffentlicht keinen eigenen Port nach außen. Add-on-Panels sind
in Home Assistant nur für **Administrator-Benutzer** sichtbar.
