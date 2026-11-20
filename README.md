# Document AI

Flask-based document Q&A app with per-account authentication, chat history, private uploads, and a FAISS-backed RAG search.

## Local setup

1. Create a virtual environment and install dependencies with `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env`, then set `GROQ_API_KEY`. For local development, the app creates a persistent session key under the ignored `instance/secret_key` file if `SECRET_KEY` is not set.
3. Start the app with `python app.py` and open <http://127.0.0.1:5000>.
4. Install test dependencies with `pip install -r requirements-dev.txt`, then run `python -m pytest`.

Passwords are stored as password hashes in the ignored SQLite database `app.db`; the original passwords are not stored.

## Document privacy and upload limits

Each account's uploaded files and FAISS index are isolated under `data/users/<user_id>/` and `faiss_store/users/<user_id>/`. The app accepts up to 10 files per request, limits each file to 10 MiB, each request to 25 MiB, and each account to 100 MiB of stored uploads. Supported extensions are PDF, TXT, CSV, XLSX, DOCX, and JSON.

Files that were previously placed directly in the shared `data/` folder are intentionally not included in account searches. Re-upload those documents through the signed-in UI to associate them with an account.

## Production configuration

- Set `APP_ENV=production` and provide a long, random `SECRET_KEY` through the hosting platform's secret manager. Startup fails if production has no configured key.
- Serve only behind HTTPS; production sessions use Secure, HttpOnly, SameSite=Lax cookies.
- Keep `.env`, `app.db`, `instance/`, `data/`, and `faiss_store/` out of source control. The ignore rules do not remove files already tracked, so confirm with `git ls-files app.db data` before publishing.
- Use a production WSGI server such as Gunicorn. Do not expose Flask's development server to the internet.
