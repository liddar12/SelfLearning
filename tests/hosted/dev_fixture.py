"""Explicit local-only browser fixture: uvicorn tests.hosted.dev_fixture:app.

No .env is loaded and no cloud network requests are made. Production imports app.py.
"""
from services.web.application import create_app
from .fakes import FakeStore, TEST_KEY

app = create_app(store=FakeStore(), env={"SELFLEARN_OPERATOR_TOKEN": TEST_KEY})
