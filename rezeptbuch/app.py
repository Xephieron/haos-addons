"""Rezeptbuch – ein Home Assistant Add-on mit schöner Web-UI.

Die Rezepte werden als JSON in /data/recipes.json gespeichert. Dieser Ordner
ist bei Add-ons persistent und wird von Home Assistant in Backups gesichert.
"""

import json
import os
import re
import uuid
from datetime import datetime

from flask import (
    Flask,
    abort,
    redirect,
    render_template,
    request,
    url_for,
)

# In der Add-on-Umgebung liegt /data; lokal zum Testen ein Ordner daneben.
DATA_DIR = "/data" if os.path.isdir("/data") else os.path.join(
    os.path.dirname(__file__), "data"
)
os.makedirs(DATA_DIR, exist_ok=True)
RECIPES_FILE = os.path.join(DATA_DIR, "recipes.json")

app = Flask(__name__)


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
    """Wandelt ein Textfeld (eine Zeile = ein Eintrag) in eine Liste."""
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
# Routen
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


@app.route("/new", methods=["GET", "POST"])
def new_recipe():
    if request.method == "POST":
        recipes = load_recipes()
        recipe = _recipe_from_form(request.form)
        recipe["id"] = uuid.uuid4().hex
        recipe["created"] = datetime.now().isoformat(timespec="seconds")
        recipes.append(recipe)
        save_recipes(recipes)
        return redirect(url_for("view_recipe", recipe_id=recipe["id"]))
    return render_template("edit.html", recipe=None)


@app.route("/recipe/<recipe_id>/edit", methods=["GET", "POST"])
def edit_recipe(recipe_id):
    recipes = load_recipes()
    recipe = find_recipe(recipes, recipe_id)
    if not recipe:
        abort(404)
    if request.method == "POST":
        recipe.update(_recipe_from_form(request.form))
        save_recipes(recipes)
        return redirect(url_for("view_recipe", recipe_id=recipe_id))
    return render_template("edit.html", recipe=recipe)


@app.route("/recipe/<recipe_id>/delete", methods=["POST"])
def delete_recipe(recipe_id):
    recipes = load_recipes()
    recipes = [r for r in recipes if r["id"] != recipe_id]
    save_recipes(recipes)
    return redirect(url_for("index"))


@app.template_filter("nl2br")
def nl2br(text):
    if not text:
        return ""
    return re.sub(r"\r?\n", "<br>", text)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
