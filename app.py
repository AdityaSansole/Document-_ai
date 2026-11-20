import os
import secrets
import sqlite3
import uuid
from functools import wraps
from threading import RLock

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

from database import (
    add_message,
    authenticate_user,
    create_conversation,
    create_user,
    get_conversation,
    init_db,
    list_conversations,
    update_conversation_title,
)

load_dotenv()


def load_secret_key():
    configured_key = os.getenv("SECRET_KEY")
    if configured_key:
        return configured_key
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise RuntimeError("Set SECRET_KEY in the production environment before starting the app.")

    instance_dir = os.path.join(os.path.dirname(__file__), "instance")
    os.makedirs(instance_dir, exist_ok=True)
    key_path = os.path.join(instance_dir, "secret_key")
    try:
        descriptor = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        with open(key_path, "r", encoding="utf-8") as key_file:
            return key_file.read().strip()

    key = secrets.token_hex(32)
    with os.fdopen(descriptor, "w", encoding="utf-8") as key_file:
        key_file.write(key)
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=load_secret_key(),
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    MAX_UPLOAD_FILES=10,
    MAX_UPLOAD_FILE_BYTES=10 * 1024 * 1024,
    MAX_USER_STORAGE_BYTES=100 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.getenv("APP_ENV", "development").lower() == "production"
        or os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
    ),
)


@app.context_processor
def inject_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return {"csrf_token": session["_csrf_token"]}


@app.before_request
def protect_state_changing_requests():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("_csrf_token", "")
        supplied = request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected, supplied):
            return jsonify({"error": "Invalid or missing security token. Refresh the page and try again."}), 400

USER_DATA_ROOT = os.path.join(app.root_path, "data", "users")
USER_INDEX_ROOT = os.path.join(app.root_path, "faiss_store", "users")
ALLOWED_EXTENSIONS = {"pdf", "txt", "csv", "xlsx", "docx", "json"}
rag_search_by_user = {}
rag_search_lock = RLock()

os.makedirs(USER_DATA_ROOT, exist_ok=True)
init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Please sign in."}), 401
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)

    return wrapped


def get_rag_search():
    user_id = int(session["user_id"])
    with rag_search_lock:
        if user_id not in rag_search_by_user:
            # Load the ML stack only for a search; importing sentence-transformers
            # also loads PyTorch and Transformers.
            from src.search import RAGSearch

            rag_search_by_user[user_id] = RAGSearch(
                persist_dir=os.path.join(USER_INDEX_ROOT, str(user_id)),
                data_dir=os.path.join(USER_DATA_ROOT, str(user_id)),
            )
        return rag_search_by_user[user_id]


def get_user_data_dir(user_id):
    path = os.path.join(USER_DATA_ROOT, str(int(user_id)))
    os.makedirs(path, exist_ok=True)
    return path


def directory_size(path):
    total = 0
    for root, _, filenames in os.walk(path):
        for filename in filenames:
            try:
                total += os.path.getsize(os.path.join(root, filename))
            except OSError:
                app.logger.warning("Unable to inspect uploaded file size")
    return total


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/login")
def login_page():
    if session.get("user_id"):
        return redirect(url_for("index"))
    return render_template("auth.html", mode="login")


@app.route("/register")
def register_page():
    if session.get("user_id"):
        return redirect(url_for("index"))
    return render_template("auth.html", mode="register")


@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if not name or not email or len(password) < 8:
        return jsonify({"error": "Enter a name and email, and use at least 8 password characters."}), 400

    try:
        user_id = create_user(name, email, password)
    except sqlite3.IntegrityError:
        return jsonify({"error": "An account with that email already exists."}), 409

    session.clear()
    session["user_id"] = user_id
    session["name"] = name
    session["email"] = email
    return jsonify({"ok": True})


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip()
    password = str(data.get("password", ""))
    user = authenticate_user(email, password)

    if not user:
        return jsonify({"error": "Invalid email or password."}), 401

    session.clear()
    session["user_id"] = user["id"]
    session["name"] = user["name"]
    session["email"] = user["email"]
    return jsonify({"ok": True})


@app.post("/api/logout")
@login_required
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
@login_required
def me():
    return jsonify({
        "id": session["user_id"],
        "name": session["name"],
        "email": session["email"],
    })


@app.get("/api/conversations")
@login_required
def conversations():
    rows = list_conversations(session["user_id"])
    return jsonify({"conversations": [dict(row) for row in rows]})


@app.post("/api/conversations")
@login_required
def new_conversation():
    conversation_id = create_conversation(session["user_id"])
    return jsonify({"id": conversation_id, "title": "New chat"})


@app.get("/api/conversations/<int:conversation_id>")
@login_required
def conversation(conversation_id):
    conversation_row, message_rows = get_conversation(session["user_id"], conversation_id)
    if not conversation_row:
        return jsonify({"error": "Conversation not found."}), 404
    return jsonify({
        "conversation": dict(conversation_row),
        "messages": [dict(row) for row in message_rows],
    })


@app.post("/api/search")
@login_required
def search():
    data = request.get_json(silent=True) or {}
    query = str(data.get("query", "")).strip()
    conversation_id = data.get("conversation_id")

    if not query:
        return jsonify({"error": "Please enter a question."}), 400
    if len(query) > 4000:
        return jsonify({"error": "Questions must be 4,000 characters or fewer."}), 400

    if conversation_id:
        try:
            conversation_id = int(conversation_id)
        except (TypeError, ValueError):
            conversation_id = None

    if not conversation_id:
        conversation_id = create_conversation(session["user_id"], query[:60])
    else:
        conversation_row, _ = get_conversation(session["user_id"], conversation_id)
        if not conversation_row:
            return jsonify({"error": "Conversation not found."}), 404
        row, messages = get_conversation(session["user_id"], conversation_id)
        if len(messages) == 0:
            update_conversation_title(conversation_id, query)

    try:
        add_message(conversation_id, "user", query)
        answer = get_rag_search().search_and_summarize(query, top_k=5)
        add_message(conversation_id, "assistant", answer)
        return jsonify({"answer": answer, "conversation_id": conversation_id})
    except Exception:
        app.logger.exception("RAG search failed for user_id=%s", session["user_id"])
        return jsonify({"error": "Search failed. Check the server logs and try again."}), 500


@app.post("/api/upload")
@login_required
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files selected."}), 400
    if len(files) > app.config["MAX_UPLOAD_FILES"]:
        file_word = "file" if app.config["MAX_UPLOAD_FILES"] == 1 else "files"
        return jsonify({
            "error": f"Upload at most {app.config['MAX_UPLOAD_FILES']} {file_word} at a time."
        }), 400

    user_data_dir = get_user_data_dir(session["user_id"])
    remaining_quota = app.config["MAX_USER_STORAGE_BYTES"] - directory_size(user_data_dir)
    saved = []
    rejected = []
    for uploaded in files:
        original_filename = secure_filename(uploaded.filename or "")
        if not original_filename:
            continue
        if not allowed_file(original_filename):
            rejected.append(original_filename)
            continue
        content = uploaded.stream.read(app.config["MAX_UPLOAD_FILE_BYTES"] + 1)
        if not content:
            rejected.append(original_filename)
            continue
        if len(content) > app.config["MAX_UPLOAD_FILE_BYTES"]:
            rejected.append(original_filename)
            continue
        if len(content) > remaining_quota:
            return jsonify({"error": "Your document storage limit has been reached."}), 413

        stored_filename = f"{uuid.uuid4().hex}_{original_filename}"
        try:
            with open(os.path.join(user_data_dir, stored_filename), "xb") as destination:
                destination.write(content)
        except OSError:
            app.logger.exception("Document upload failed for user_id=%s", session["user_id"])
            return jsonify({"error": "The document could not be saved. Try again."}), 500
        remaining_quota -= len(content)
        saved.append(original_filename)

    with rag_search_lock:
        rag_search_by_user.pop(int(session["user_id"]), None)

    return jsonify({"saved": saved, "rejected": rejected})


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "The upload is too large. Keep each file under 10 MB and each request under 25 MB."}), 413


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=os.getenv("FLASK_DEBUG", "0") == "1")
