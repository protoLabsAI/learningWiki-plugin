"""FastAPI routers. TWO routers, two prefixes (the four-rules contract):

- view router → PUBLIC  /plugins/learning_wiki/*   (an iframe can't carry a bearer)
- data router → GATED   /api/plugins/learning_wiki/* (inherits the operator bearer)
"""

from __future__ import annotations


def build_view_router(cfg: dict):
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    from .view import PAGE

    r = APIRouter()

    @r.get("/view", response_class=HTMLResponse)
    async def _view() -> HTMLResponse:
        return HTMLResponse(PAGE)

    return r


def build_data_router(cfg: dict, get_store):
    from fastapi import APIRouter, HTTPException

    r = APIRouter()

    @r.get("/pages")
    async def _pages() -> dict:
        return {"pages": get_store().list_pages()}

    @r.get("/pages/{slug}")
    async def _page(slug: str) -> dict:
        page = get_store().get_page(slug)
        if page is None:
            raise HTTPException(status_code=404, detail=f"no page '{slug}'")
        return {"page": page}

    @r.get("/due")
    async def _due() -> dict:
        return {"due": get_store().due_count(), "cards": get_store().due_cards(limit=20)}

    @r.get("/stats")
    async def _stats() -> dict:
        return get_store().stats()

    return r
