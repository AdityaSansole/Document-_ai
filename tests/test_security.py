from io import BytesIO
from pathlib import Path
import sqlite3
import sys
import types

import pytest

import database
import app as app_module


@pytest.fixture

def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    monkeypatch.setattr(app_module, "USER_DATA_ROOT", str(tmp_path / "user-data"))
    monkeypatch.setattr(app_module, "USER_INDEX_ROOT", str(tmp_path / "user-indexes"))
    app_module.rag_search_by_user.clear()
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret-key")
    yield app_module.app.test_client()
    app_module.rag_search_by_user.clear()


def register(client, name, email):
    response = client.post(
        "/api/register",
        json={
            "name": name,
            "email": email,
            "password": "correct-horse-battery",
        },
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    return response


def csrf_headers(client):
    with client.session_transaction() as user_session:
        authenticated = bool(user_session.get("user_id"))
    client.get("/" if authenticated else "/login")
    with client.session_transaction() as user_session:
        return {"X-CSRF-Token": user_session["_csrf_token"]}


def test_password_is_stored_as_a_hash(client):
    register(client, "A User", "a@example.com")

    with sqlite3.connect(database.DB_PATH) as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM users WHERE email = ?", ("a@example.com",)
        ).fetchone()[0]

    assert password_hash != "correct-horse-battery"
    assert password_hash.startswith("scrypt:")


def test_user_cannot_read_another_users_conversation(client):
    register(client, "Owner", "owner@example.com")
    created = client.post("/api/conversations", json={}, headers=csrf_headers(client))
    conversation_id = created.get_json()["id"]

    client.post("/api/logout", headers=csrf_headers(client))
    register(client, "Other", "other@example.com")
    response = client.get(f"/api/conversations/{conversation_id}")

    assert response.status_code == 404


def test_uploaded_documents_are_separated_by_user(client):
    register(client, "First", "first@example.com")
    response = client.post(
        "/api/upload",
        data={"files": (BytesIO(b"private first user document"), "notes.txt")},
        content_type="multipart/form-data",
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    first_user_files = list((Path(app_module.USER_DATA_ROOT) / "1").iterdir())
    assert len(first_user_files) == 1
    assert first_user_files[0].read_bytes() == b"private first user document"

    client.post("/api/logout", headers=csrf_headers(client))
    register(client, "Second", "second@example.com")
    second_user_dir = Path(app_module.USER_DATA_ROOT) / "2"

    assert second_user_dir.exists() is False
    assert first_user_files[0].parent != second_user_dir

def test_upload_rejects_unsupported_extensions(client):
    register(client, "A User", "a@example.com")
    response = client.post(
        "/api/upload",
        data={"files": (BytesIO(b"not a document"), "payload.exe")},
        content_type="multipart/form-data",
        headers=csrf_headers(client),
    )

    assert response.status_code == 200
    assert response.get_json()["saved"] == []
    assert response.get_json()["rejected"] == ["payload.exe"]


def test_upload_enforces_per_file_size_limit(client, monkeypatch):
    register(client, "A User", "a@example.com")
    monkeypatch.setitem(app_module.app.config, "MAX_UPLOAD_FILE_BYTES", 8)

    response = client.post(
        "/api/upload",
        data={"files": (BytesIO(b"123456789"), "large.txt")},
        content_type="multipart/form-data",
        headers=csrf_headers(client),
    )

    assert response.status_code == 200
    assert response.get_json()["rejected"] == ["large.txt"]
    user_dir = Path(app_module.USER_DATA_ROOT) / "1"
    assert not list(user_dir.iterdir())


def test_upload_enforces_file_count_limit(client, monkeypatch):
    register(client, "A User", "a@example.com")
    monkeypatch.setitem(app_module.app.config, "MAX_UPLOAD_FILES", 1)

    response = client.post(
        "/api/upload",
        data={"files": [
            (BytesIO(b"one"), "one.txt"),
            (BytesIO(b"two"), "two.txt"),
        ]},
        content_type="multipart/form-data",
        headers=csrf_headers(client),
    )

    assert response.status_code == 400
    assert "at most 1 file" in response.get_json()["error"]


def test_upload_enforces_per_user_storage_quota(client, monkeypatch):
    register(client, "A User", "a@example.com")
    monkeypatch.setitem(app_module.app.config, "MAX_USER_STORAGE_BYTES", 4)

    response = client.post(
        "/api/upload",
        data={"files": (BytesIO(b"12345"), "over-quota.txt")},
        content_type="multipart/form-data",
        headers=csrf_headers(client),
    )

    assert response.status_code == 413
    assert "storage limit" in response.get_json()["error"]


def test_search_error_does_not_expose_exception_details(client, monkeypatch):
    register(client, "A User", "a@example.com")

    class BrokenSearch:
        def search_and_summarize(self, *_args, **_kwargs):
            raise RuntimeError("private filesystem path and API details")

    monkeypatch.setattr(app_module, "get_rag_search", lambda: BrokenSearch())
    response = client.post(
        "/api/search", json={"query": "search"}, headers=csrf_headers(client)
    )

    assert response.status_code == 500
    assert "private filesystem path" not in response.get_json()["error"]


def test_state_changing_request_requires_csrf_token(client):
    register(client, "A User", "a@example.com")

    response = client.post("/api/conversations", json={})

    assert response.status_code == 400
    assert response.get_json()["error"].startswith("Invalid or missing security token")


def test_rag_instances_use_distinct_user_directories(monkeypatch):
    constructed = []

    class FakeRAGSearch:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    fake_module = types.ModuleType("src.search")
    fake_module.RAGSearch = FakeRAGSearch
    monkeypatch.setitem(sys.modules, "src.search", fake_module)
    app_module.rag_search_by_user.clear()

    for user_id in (101, 202):
        with app_module.app.test_request_context("/"):
            app_module.session["user_id"] = user_id
            app_module.get_rag_search()

    assert constructed[0]["data_dir"] != constructed[1]["data_dir"]
    assert constructed[0]["persist_dir"] != constructed[1]["persist_dir"]
    app_module.rag_search_by_user.clear()


def test_session_secret_persists_without_environment_setting(tmp_path, monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app.py"))

    first_key = app_module.load_secret_key()
    second_key = app_module.load_secret_key()

    assert first_key == second_key
    assert len(first_key) >= 64
    assert (tmp_path / "instance" / "secret_key").exists()


def test_production_requires_configured_session_secret(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(RuntimeError, match="Set SECRET_KEY"):
        app_module.load_secret_key()
