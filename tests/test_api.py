"""Mount the routers EXACTLY as register() does and test the actual paths."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(registry):
    app = FastAPI()
    for prefix, router in registry.routers:
        app.include_router(router, prefix=prefix)
    return TestClient(app)


def test_view_served_on_declared_public_path(client):
    r = client.get("/plugins/learning_wiki/view")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "_ds/plugin-kit.css" in r.text


def test_view_not_served_under_api(client):
    assert client.get("/api/plugins/learning_wiki/view").status_code == 404


def test_pages_endpoints(client, registry):
    from conftest import tool_by_name

    tool_by_name(registry, "wiki_file").invoke({"slug": "a", "title": "Alpha", "content_md": "body"})
    pages = client.get("/api/plugins/learning_wiki/pages").json()["pages"]
    assert pages[0]["slug"] == "a" and pages[0]["tier"] == "novice"

    page = client.get("/api/plugins/learning_wiki/pages/a").json()["page"]
    assert page["title"] == "Alpha" and page["content_md"] == "body"

    assert client.get("/api/plugins/learning_wiki/pages/nope").status_code == 404


def test_due_and_stats_endpoints(client, registry):
    from conftest import tool_by_name

    tool_by_name(registry, "card_add").invoke({"slug": "a", "prompt": "q?"})
    assert client.get("/api/plugins/learning_wiki/due").json()["due"] == 1
    stats = client.get("/api/plugins/learning_wiki/stats").json()
    assert stats["pages"] == 1 and stats["due"] == 1
