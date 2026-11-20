import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = Path(__file__).resolve().parent / "app.db"


def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT 'New chat',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        """)


def create_user(name, email, password):
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO users(name, email, password_hash) VALUES (?, ?, ?)",
            (name.strip(), email.strip().lower(), generate_password_hash(password)),
        )
        return cursor.lastrowid


def authenticate_user(email, password):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None


def create_conversation(user_id, title="New chat"):
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO conversations(user_id, title) VALUES (?, ?)",
            (user_id, title),
        )
        conversation_id = cursor.lastrowid
        db.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conversation_id,),
        )
        return conversation_id


def list_conversations(user_id):
    with get_db() as db:
        return db.execute(
            """SELECT id, title, updated_at
               FROM conversations
               WHERE user_id = ?
               ORDER BY updated_at DESC""",
            (user_id,),
        ).fetchall()


def get_conversation(user_id, conversation_id):
    with get_db() as db:
        conversation = db.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
        if not conversation:
            return None, []
        messages = db.execute(
            """SELECT role, content, created_at
               FROM messages
               WHERE conversation_id = ?
               ORDER BY created_at ASC, id ASC""",
            (conversation_id,),
        ).fetchall()
        return conversation, messages


def add_message(conversation_id, role, content):
    with get_db() as db:
        db.execute(
            "INSERT INTO messages(conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content),
        )
        db.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conversation_id,),
        )


def update_conversation_title(conversation_id, title):
    with get_db() as db:
        db.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title[:60], conversation_id),
        )
