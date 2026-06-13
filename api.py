"""
Portfolio REST API  —  deploy this on Render (Python web service)
-----------------------------------------------------------------
Environment variables to set in Render:
  API_SECRET        — shared secret, must match the bot's API_SECRET
  WEBHOOK_URL       — your Discord channel webhook (forwarded from the portfolio form)

Endpoints
  GET    /projects
  POST   /projects           { title, description, tags[], icon, span }
  PATCH  /projects/<id>      any subset of the above fields
  DELETE /projects/<id>

  GET    /donate
  POST   /donate             { name, url, label, icon }
  DELETE /donate/<name>

  POST   /contact            called by the portfolio form (proxies to Discord webhook)

All mutating routes require header:  X-Secret: <API_SECRET>
"""

import os
import uuid
import requests
from datetime import datetime
from functools import wraps
from flask import Flask, jsonify, request, abort

app = Flask(__name__)

API_SECRET  = os.environ["API_SECRET"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")

from flask import send_from_directory

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# ── In-memory store (swap for sqlite/postgres when you want persistence) ──────

store = {
    "projects": [
        {
            "id": "proj-1",
            "title": "Project one",
            "description": "A full-stack web app with a clean animated UI, built with React, Node, and a custom API layer.",
            "tags": ["React", "Node.js", "PostgreSQL"],
            "icon": "💻",
            "span": 4,
        },
        {
            "id": "proj-2",
            "title": "Design system",
            "description": "A component library and design tokens used across multiple products.",
            "tags": ["Figma", "Storybook"],
            "icon": "🎨",
            "span": 2,
        },
        {
            "id": "proj-3",
            "title": "Performance audit",
            "description": "Cut load time by 60% for a client's marketing site through bundle and asset optimization.",
            "tags": ["Lighthouse", "Vite"],
            "icon": "⚡",
            "span": 2,
        },
    ],
    "donate": [
        {"name": "Ko-fi",          "url": "", "label": "One-time support", "icon": "☕"},
        {"name": "GitHub Sponsors","url": "", "label": "Monthly support",  "icon": "💵"},
        {"name": "PayPal",         "url": "", "label": "Direct donation",  "icon": "✌"},
    ],
}


# ── Auth ──────────────────────────────────────────────────────────────────────

def require_secret(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Secret") != API_SECRET:
            abort(401)
        return fn(*args, **kwargs)
    return wrapper


# ── CORS (so the portfolio page on Render can fetch /projects + /donate) ──────

@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Secret"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PATCH,DELETE,OPTIONS"
    return response

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def options(_path):
    return "", 204


# ── Projects ──────────────────────────────────────────────────────────────────

@app.route("/projects", methods=["GET"])
def get_projects():
    return jsonify({"projects": store["projects"]})


@app.route("/projects", methods=["POST"])
@require_secret
def add_project():
    data = request.get_json(force=True)
    required = {"title", "description", "tags"}
    if not required.issubset(data):
        return jsonify({"error": f"Missing fields: {required - data.keys()}"}), 400

    project = {
        "id":          "proj-" + uuid.uuid4().hex[:6],
        "title":       data["title"],
        "description": data["description"],
        "tags":        data["tags"],
        "icon":        data.get("icon", "💻"),
        "span":        int(data.get("span", 2)),
    }
    store["projects"].append(project)
    return jsonify(project), 201


@app.route("/projects/<project_id>", methods=["PATCH"])
@require_secret
def edit_project(project_id):
    project = next((p for p in store["projects"] if p["id"] == project_id), None)
    if not project:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json(force=True)
    allowed = {"title", "description", "tags", "icon", "span"}
    for key in allowed.intersection(data):
        project[key] = int(data[key]) if key == "span" else data[key]

    return jsonify(project)


@app.route("/projects/<project_id>", methods=["DELETE"])
@require_secret
def delete_project(project_id):
    before = len(store["projects"])
    store["projects"] = [p for p in store["projects"] if p["id"] != project_id]
    if len(store["projects"]) == before:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"deleted": project_id})


# ── Donate ────────────────────────────────────────────────────────────────────

@app.route("/donate", methods=["GET"])
def get_donate():
    return jsonify({"links": store["donate"]})


@app.route("/donate", methods=["POST"])
@require_secret
def set_donate():
    data = request.get_json(force=True)
    if not {"name", "url", "label"}.issubset(data):
        return jsonify({"error": "Missing fields"}), 400

    existing = next((l for l in store["donate"] if l["name"].lower() == data["name"].lower()), None)
    if existing:
        existing.update({"url": data["url"], "label": data["label"], "icon": data.get("icon", existing.get("icon", "🔗"))})
        return jsonify(existing)

    link = {"name": data["name"], "url": data["url"], "label": data["label"], "icon": data.get("icon", "🔗")}
    store["donate"].append(link)
    return jsonify(link), 201


@app.route("/donate/<name>", methods=["DELETE"])
@require_secret
def delete_donate(name):
    before = len(store["donate"])
    store["donate"] = [l for l in store["donate"] if l["name"].lower() != name.lower()]
    if len(store["donate"]) == before:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"deleted": name})


# ── Contact form proxy  (called by the portfolio page instead of Discord directly) ──

@app.route("/contact", methods=["POST"])
def contact():
    """
    The portfolio form POSTs here. We forward to Discord as a rich embed
    and store the submission so the bot can list pending replies.
    """
    data = request.get_json(force=True)
    name    = data.get("name", "")
    email   = data.get("email", "")
    message = data.get("message", "")

    submission = {
        "id":        uuid.uuid4().hex[:8],
        "name":      name,
        "email":     email,
        "message":   message,
        "timestamp": datetime.utcnow().isoformat(),
        "replied":   False,
    }
    store.setdefault("submissions", []).append(submission)

    if WEBHOOK_URL:
        try:
            requests.post(WEBHOOK_URL, json={
                "embeds": [{
                    "title": "📬 New portfolio message",
                    "color": 0xC861FF,
                    "fields": [
                        {"name": "Name",    "value": name,    "inline": True},
                        {"name": "Email",   "value": email,   "inline": True},
                        {"name": "Message", "value": message},
                        {"name": "ID",      "value": f"`{submission['id']}`  — use `/reply` to respond", "inline": False},
                    ],
                    "timestamp": submission["timestamp"],
                }]
            }, timeout=5)
        except Exception:
            pass  # don't fail the form if Discord is down

    return jsonify({"ok": True, "id": submission["id"]})


@app.route("/submissions", methods=["GET"])
@require_secret
def list_submissions():
    return jsonify({"submissions": store.get("submissions", [])})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
