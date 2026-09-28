"""Vercel entrypoint: the FastAPI preset serves the `app` object found in app.py.
Other hosts run `uvicorn backend.main:app` directly."""

from backend.main import app  # noqa: F401
