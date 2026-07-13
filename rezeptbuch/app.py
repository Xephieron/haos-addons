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
os.makedirs(IMAGES_DIR, exist_ok=True)
RECIPES_FILE = os.path.join(DATA_DIR, "recipes.json")
OPTIONS_FILE = os.path.join(DATA_DIR, "options.json")

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB Upload-Limit


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
    }


# ---------------------------------------------------------------------------
# Bild-Handling
# ---------------------------------------------------------------------------
def _save_upload(file_storage):
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXT:
        ext = ".jpg"
    name = uuid.uuid4().hex + ext
    file_storage.save(os.path.join(IMAGES_DIR, name))
    return name


def _delete_image(name):
    if not name:
        return
    path = os.path.join(IMAGES_DIR, os.path.basename(name))
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def apply_image_changes(recipe):
    """Wendet Bild-Upload bzw. -Entfernung aus dem aktuellen Request an."""
    if request.form.get("remove_image") == "1" and recipe.get("image"):
        _delete_image(recipe["image"])
        recipe["image"] = ""
    file = request.files.get("image_file")
    if file and file.filename:
        if recipe.get("image"):
            _delete_image(recipe["image"])
        recipe["image"] = _save_upload(file)


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
        apply_image_changes(recipe)
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
        save_recipes(recipes)
        return redirect(url_for("view_recipe", recipe_id=recipe_id))
    return render_template(
        "edit.html", recipe=recipe, created=request.args.get("created") == "1"
    )


@app.route("/recipe/<recipe_id>/delete", methods=["POST"])
def delete_recipe(recipe_id):
    recipes = load_recipes()
    recipe = find_recipe(recipes, recipe_id)
    if recipe and recipe.get("image"):
        _delete_image(recipe["image"])
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
