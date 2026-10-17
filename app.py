"""Vercel's discoverable ASGI entrypoint."""
from services.web.application import create_app

app = create_app()
