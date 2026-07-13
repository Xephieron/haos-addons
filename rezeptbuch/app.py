"""Rezeptbuch – ein Home Assistant Add-on mit schöner Web-UI.

Funktionen:
  * Rezepte manuell anlegen, bearbeiten, löschen
  * Rezepte per KI aus Text erfassen (Ollama)
  * Rezepte per KI aus einem Foto erfassen (Ollama Vision-Modell)
  * Bilder zu Rezepten hochladen und anzeigen

Die Rezepte werden als JSON in /data/recipes.json gespeichert, Bilder in
/data/images. Beide liegen im persistenten /data-Ordner und werden von Home
Assistant in Backups gesichert.
"""

import base64
import json
import os
import re
import uuid
import urllib.error
import urllib.request
from datetime import datetime

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

# In der Add-on-Umgebung liegt /data; lokal zum Testen ein Ordner daneben.
DATA_DIR = "/data" if os.path.isdir("/data") else os.path.join(
    os.path.dirname(__file__), "data"
)
IMAGES_DIR = os.path.join(DATA_DIR, "images")
VIDEOS_DIR = os.path.join(DATA_DIR, "videos")
os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(VIDEOS_DIR, exist_ok=True)
RECIPES_FILE = os.path.join(DATA_DIR, "recipes.json")
OPTIONS_FILE = os.path.join(DATA_DIR, "options.json")

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v", ".ogg", ".ogv"}

# Zugang zur Home-Assistant-Core-API (für die Einkaufsliste).
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
CORE_API = "http://supervisor/core/api"

app = Flask(__name__)
# Große Uploads erlauben (eigene Videos können mehrere MB groß sein).
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024  # 256 MB


# ---------------------------------------------------------------------------
# Ingress-Unterstützung
#
# Home Assistant reicht das Add-on unter einem Pfad wie
# /api/hassio_ingress/<token>/ durch. Damit url_for() die richtigen Links
# erzeugt, setzen wir SCRIPT_NAME aus dem X-Ingress-Path-Header.
# ---------------------------------------------------------------------------
class IngressMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        ingress_path = environ.get("HTTP_X_INGRESS_PATH", "")
        if ingress_path:
            environ["SCRIPT_NAME"] = ingress_path
        return self.wsgi_app(environ, start_response)


app.wsgi_app = IngressMiddleware(app.wsgi_app)


# ---------------------------------------------------------------------------
# Add-on-Optionen (Ollama-Konfiguration)
# ---------------------------------------------------------------------------
DEFAULT_OPTIONS = {
    "ollama_url": "http://homeassistant.local:11434",
    "ollama_model": "llama3.2",
    "ollama_vision_model": "llava",
    "todo_entity": "todo.shopping_list",
}


def load_options():
    opts = dict(DEFAULT_OPTIONS)
    if os.path.exists(OPTIONS_FILE):
        try:
            with open(OPTIONS_FILE, "r", encoding="utf-8") as fh:
                opts.update({k: v for k, v in json.load(fh).items() if v})
        except (json.JSONDecodeError, OSError):
            pass
    return opts


def ollama_base_url():
    return (load_options().get("ollama_url") or DEFAULT_OPTIONS["ollama_url"]).rstrip("/")


# ---------------------------------------------------------------------------
# Ollama-Anbindung
# ---------------------------------------------------------------------------
RECIPE_JSON_HINT = (
    "Gib AUSSCHLIESSLICH ein JSON-Objekt zurück mit diesen Feldern: "
    '"title" (String), "emoji" (ein einzelnes passendes Emoji), '
    '"category" (z.B. Hauptgericht, Dessert, Frühstück, Beilage), '
    '"servings" (Portionen als String), "time" (Zubereitungszeit als String, z.B. "30 Min."), '
    '"ingredients" (Array von Strings, je eine Zutat mit Menge), '
    '"steps" (Array von Strings, je ein Zubereitungsschritt), '
    '"notes" (String, optional). Antworte auf Deutsch.'
)

TEXT_PROMPT_PREFIX = (
    "Du bist ein hilfreicher Assistent, der Kochrezepte strukturiert. "
    "Extrahiere aus dem folgenden Text ein Rezept. " + RECIPE_JSON_HINT + "\n\nText:\n\"\"\"\n"
)


def build_text_prompt(text):
    # Kein str.format() – der Nutzertext darf beliebige Zeichen (auch {}) enthalten.
    return TEXT_PROMPT_PREFIX + text + "\n\"\"\""

IMAGE_PROMPT = (
    "Du bist ein hilfreicher Assistent, der Kochrezepte strukturiert. "
    "Auf dem Bild ist ein Rezept zu sehen (z.B. abfotografiert aus einem Kochbuch "
    "oder ein Foto des fertigen Gerichts mit Zutatenliste). Extrahiere das Rezept. "
    + RECIPE_JSON_HINT
)


def ollama_generate(prompt, images=None, model=None, timeout=240):
    """Ruft die Ollama-/api/generate-Schnittstelle auf und liefert den Text."""
    opts = load_options()
    base = (opts.get("ollama_url") or DEFAULT_OPTIONS["ollama_url"]).rstrip("/")
    model = model or opts.get("ollama_model") or DEFAULT_OPTIONS["ollama_model"]

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    if images:
        payload["images"] = images

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("response", "")


def _extract_json(text):
    """Robustes Parsen: erst direkt, sonst erstes {...} aus dem Text."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _coerce_list(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return []


def recipe_from_ai(response_text):
    data = _extract_json(response_text)
    if isinstance(data, list):
        data = next((d for d in data if isinstance(d, dict)), None)
    if not isinstance(data, dict):
        raise ValueError("Kein Rezept-Objekt in der KI-Antwort gefunden.")
    return {
        "title": (str(data.get("title") or "").strip() or "Neues Rezept"),
        "emoji": (str(data.get("emoji") or "🍽️").strip()[:4] or "🍽️"),
        "category": str(data.get("category") or "").strip(),
        "servings": str(data.get("servings") or "").strip(),
        "time": str(data.get("time") or "").strip(),
        "ingredients": _coerce_list(data.get("ingredients")),
        "steps": _coerce_list(data.get("steps")),
        "notes": str(data.get("notes") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Home-Assistant-Einkaufsliste (Core-API)
# ---------------------------------------------------------------------------
def _ha_request(path, method="GET", body=None):
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("Kein Zugriff auf die Home-Assistant-API (SUPERVISOR_TOKEN fehlt).")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        CORE_API + path,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + SUPERVISOR_TOKEN,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
        return resp.status, (json.loads(raw) if raw else None)


def ha_todo_entities():
    """Liste aller verfügbaren To-do-/Einkaufslisten-Entitäten."""
    _, states = _ha_request("/states")
    return sorted(
        s["entity_id"]
        for s in (states or [])
        if str(s.get("entity_id", "")).startswith("todo.")
    )


def ha_validate_list():
    """Prüft, ob die konfigurierte Liste existiert; sonst hilfreicher Fehler."""
    entity = load_options().get("todo_entity") or DEFAULT_OPTIONS["todo_entity"]
    try:
        status, _ = _ha_request("/states/" + entity)
        if status == 200:
            return entity
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    available = ", ".join(ha_todo_entities()) or "keine gefunden"
    raise ValueError(
        f"Die Liste '{entity}' existiert in Home Assistant nicht. "
        f"Verfügbare To-do-/Einkaufslisten: {available}. "
        f"Trage die richtige unter 'todo_entity' in den Add-on-Optionen ein."
    )


def ha_add_item(entity, item):
    """Fügt einen Eintrag zur angegebenen Liste hinzu."""
    _ha_request(
        "/services/todo/add_item",
        method="POST",
        body={"entity_id": entity, "item": item},
    )
    print(f"[Rezeptbuch] Zur Einkaufsliste ({entity}): {item}", flush=True)
    return True


# ---------------------------------------------------------------------------
# Video-Helfer (YouTube-Erkennung)
# ---------------------------------------------------------------------------
def youtube_id(url):
    if not url:
        return ""
    match = re.search(
        r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/|v/|live/))([A-Za-z0-9_-]{11})",
        url,
    )
    return match.group(1) if match else ""


def is_video_file_url(url):
    if not url:
        return False
    path = url.split("?")[0].lower()
    return any(path.endswith(ext) for ext in ALLOWED_VIDEO_EXT)


# Für die Templates verfügbar machen.
app.jinja_env.globals["youtube_id"] = youtube_id
app.jinja_env.globals["is_video_file_url"] = is_video_file_url


# ---------------------------------------------------------------------------
# Datenhaltung
# ---------------------------------------------------------------------------
def load_recipes():
    if not os.path.exists(RECIPES_FILE):
        return []
    try:
        with open(RECIPES_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []


def save_recipes(recipes):
    tmp = RECIPES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(recipes, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, RECIPES_FILE)


def find_recipe(recipes, recipe_id):
    return next((r for r in recipes if r["id"] == recipe_id), None)


def _lines_to_list(text):
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _recipe_from_form(form):
    return {
        "title": form.get("title", "").strip() or "Unbenanntes Rezept",
        "emoji": form.get("emoji", "").strip() or "🍽️",
        "category": form.get("category", "").strip(),
        "servings": form.get("servings", "").strip(),
        "time": form.get("time", "").strip(),
        "ingredients": _lines_to_list(form.get("ingredients", "")),
        "steps": _lines_to_list(form.get("steps", "")),
        "notes": form.get("notes", "").strip(),
        "video_url": form.get("video_url", "").strip(),
    }


# ---------------------------------------------------------------------------
# Bild-Handling
# ---------------------------------------------------------------------------
def _save_upload(file_storage, directory, allowed_ext, fallback_ext):
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in allowed_ext:
        ext = fallback_ext
    name = uuid.uuid4().hex + ext
    file_storage.save(os.path.join(directory, name))
    return name


def _delete_file(directory, name):
    if not name:
        return
    path = os.path.join(directory, os.path.basename(name))
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def apply_image_changes(recipe):
    """Wendet Bild-Upload bzw. -Entfernung aus dem aktuellen Request an."""
    if request.form.get("remove_image") == "1" and recipe.get("image"):
        _delete_file(IMAGES_DIR, recipe["image"])
        recipe["image"] = ""
    file = request.files.get("image_file")
    if file and file.filename:
        if recipe.get("image"):
            _delete_file(IMAGES_DIR, recipe["image"])
        recipe["image"] = _save_upload(file, IMAGES_DIR, ALLOWED_IMAGE_EXT, ".jpg")


def apply_video_changes(recipe):
    """Wendet Video-Upload bzw. -Entfernung aus dem aktuellen Request an."""
    if request.form.get("remove_video") == "1" and recipe.get("video"):
        _delete_file(VIDEOS_DIR, recipe["video"])
        recipe["video"] = ""
    file = request.files.get("video_file")
    if file and file.filename:
        if recipe.get("video"):
            _delete_file(VIDEOS_DIR, recipe["video"])
        recipe["video"] = _save_upload(file, VIDEOS_DIR, ALLOWED_VIDEO_EXT, ".mp4")


# ---------------------------------------------------------------------------
# Routen – Ansicht
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    recipes = load_recipes()
    query = request.args.get("q", "").strip().lower()
    category = request.args.get("category", "").strip()

    if query:
        recipes = [
            r
            for r in recipes
            if query in r["title"].lower()
            or query in r.get("category", "").lower()
            or any(query in ing.lower() for ing in r.get("ingredients", []))
        ]
    if category:
        recipes = [r for r in recipes if r.get("category", "") == category]

    recipes.sort(key=lambda r: r["title"].lower())

    all_categories = sorted(
        {r["category"] for r in load_recipes() if r.get("category")}
    )
    return render_template(
        "index.html",
        recipes=recipes,
        query=query,
        categories=all_categories,
        active_category=category,
    )


@app.route("/recipe/<recipe_id>")
def view_recipe(recipe_id):
    recipe = find_recipe(load_recipes(), recipe_id)
    if not recipe:
        abort(404)
    return render_template("recipe.html", recipe=recipe)


@app.route("/image/<path:filename>")
def serve_image(filename):
    return send_from_directory(IMAGES_DIR, filename)


@app.route("/video/<path:filename>")
def serve_video(filename):
    return send_from_directory(VIDEOS_DIR, filename)


# ---------------------------------------------------------------------------
# Einkaufsliste
# ---------------------------------------------------------------------------
@app.route("/api/shopping/add", methods=["POST"])
def shopping_add():
    item = (request.form.get("item") or "").strip()
    if not item:
        return jsonify({"ok": False, "error": "Keine Zutat angegeben."}), 400
    try:
        entity = ha_validate_list()
        ha_add_item(entity, item)
        return jsonify({"ok": True, "item": item, "entity": entity})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.route("/recipe/<recipe_id>/shopping", methods=["POST"])
def shopping_add_all(recipe_id):
    recipe = find_recipe(load_recipes(), recipe_id)
    if not recipe:
        return jsonify({"ok": False, "error": "Rezept nicht gefunden."}), 404
    ingredients = recipe.get("ingredients", [])
    if not ingredients:
        return jsonify({"ok": False, "error": "Dieses Rezept hat keine Zutaten."}), 400
    try:
        entity = ha_validate_list()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 502
    added, errors = 0, []
    for ing in ingredients:
        try:
            ha_add_item(entity, ing)
            added += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    if added == 0:
        return jsonify({"ok": False, "error": errors[0] if errors else "Fehler."}), 502
    return jsonify({"ok": True, "added": added, "total": len(ingredients), "entity": entity})


# ---------------------------------------------------------------------------
# Routen – Anlegen (Hub mit drei Wegen)
# ---------------------------------------------------------------------------
@app.route("/add")
def add():
    opts = load_options()
    return render_template(
        "add.html",
        error=request.args.get("error"),
        prefill_text=request.args.get("text", ""),
        options=opts,
    )


@app.route("/new", methods=["GET", "POST"])
def new_recipe():
    if request.method == "POST":
        recipes = load_recipes()
        recipe = _recipe_from_form(request.form)
        recipe["id"] = uuid.uuid4().hex
        recipe["created"] = datetime.now().isoformat(timespec="seconds")
        recipe["image"] = ""
        recipe["video"] = ""
        apply_image_changes(recipe)
        apply_video_changes(recipe)
        recipes.append(recipe)
        save_recipes(recipes)
        return redirect(url_for("view_recipe", recipe_id=recipe["id"]))
    return render_template("edit.html", recipe=None)


@app.route("/add/text", methods=["POST"])
def add_from_text():
    text = request.form.get("text", "").strip()
    if not text:
        return redirect(url_for("add", error="Bitte gib zuerst einen Rezept-Text ein."))
    try:
        response = ollama_generate(build_text_prompt(text))
        recipe = recipe_from_ai(response)
    except (urllib.error.URLError, OSError) as exc:
        return redirect(url_for("add", error=f"Ollama nicht erreichbar: {exc}", text=text))
    except (ValueError, json.JSONDecodeError):
        return redirect(url_for("add", error="Die KI-Antwort konnte nicht als Rezept gelesen werden. Versuch es erneut.", text=text))

    recipe["id"] = uuid.uuid4().hex
    recipe["created"] = datetime.now().isoformat(timespec="seconds")
    recipe["image"] = ""
    recipes = load_recipes()
    recipes.append(recipe)
    save_recipes(recipes)
    return redirect(url_for("edit_recipe", recipe_id=recipe["id"], created="1"))


@app.route("/add/photo", methods=["POST"])
def add_from_photo():
    file = request.files.get("image_file")
    if not file or not file.filename:
        return redirect(url_for("add", error="Bitte wähle zuerst ein Foto aus."))

    raw = file.read()
    b64 = base64.b64encode(raw).decode("utf-8")
    vision_model = load_options().get("ollama_vision_model") or DEFAULT_OPTIONS["ollama_vision_model"]
    try:
        response = ollama_generate(IMAGE_PROMPT, images=[b64], model=vision_model)
        recipe = recipe_from_ai(response)
    except (urllib.error.URLError, OSError) as exc:
        return redirect(url_for("add", error=f"Ollama nicht erreichbar: {exc}"))
    except (ValueError, json.JSONDecodeError):
        return redirect(url_for("add", error="Aus dem Foto konnte kein Rezept gelesen werden. Versuch ein deutlicheres Bild."))

    # Das hochgeladene Foto gleich als Rezeptbild speichern.
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        ext = ".jpg"
    name = uuid.uuid4().hex + ext
    with open(os.path.join(IMAGES_DIR, name), "wb") as fh:
        fh.write(raw)

    recipe["id"] = uuid.uuid4().hex
    recipe["created"] = datetime.now().isoformat(timespec="seconds")
    recipe["image"] = name
    recipes = load_recipes()
    recipes.append(recipe)
    save_recipes(recipes)
    return redirect(url_for("edit_recipe", recipe_id=recipe["id"], created="1"))


# ---------------------------------------------------------------------------
# Routen – Bearbeiten / Löschen
# ---------------------------------------------------------------------------
@app.route("/recipe/<recipe_id>/edit", methods=["GET", "POST"])
def edit_recipe(recipe_id):
    recipes = load_recipes()
    recipe = find_recipe(recipes, recipe_id)
    if not recipe:
        abort(404)
    if request.method == "POST":
        recipe.update(_recipe_from_form(request.form))
        apply_image_changes(recipe)
        apply_video_changes(recipe)
        save_recipes(recipes)
        return redirect(url_for("view_recipe", recipe_id=recipe_id))
    return render_template(
        "edit.html", recipe=recipe, created=request.args.get("created") == "1"
    )


@app.route("/recipe/<recipe_id>/delete", methods=["POST"])
def delete_recipe(recipe_id):
    recipes = load_recipes()
    recipe = find_recipe(recipes, recipe_id)
    if recipe:
        _delete_file(IMAGES_DIR, recipe.get("image"))
        _delete_file(VIDEOS_DIR, recipe.get("video"))
    recipes = [r for r in recipes if r["id"] != recipe_id]
    save_recipes(recipes)
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# API – Ollama-Verbindungsstatus (für die Anzeige auf der Add-Seite)
# ---------------------------------------------------------------------------
@app.route("/api/ollama/status")
def ollama_status():
    opts = load_options()
    base = ollama_base_url()
    try:
        req = urllib.request.Request(base + "/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        models = sorted(m.get("name", "") for m in body.get("models", []))
        return jsonify(
            {
                "ok": True,
                "url": base,
                "models": models,
                "model": opts.get("ollama_model"),
                "vision_model": opts.get("ollama_vision_model"),
            }
        )
    except Exception as exc:  # noqa: BLE001 – jede Netzwerkstörung melden
        return jsonify({"ok": False, "url": base, "error": str(exc)})


@app.template_filter("nl2br")
def nl2br(text):
    if not text:
        return ""
    return re.sub(r"\r?\n", "<br>", text)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
