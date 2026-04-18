"""Thin async wrapper around the TVmaze API (no auth required)."""

from __future__ import annotations

import asyncio

import httpx

_BASE = "https://api.tvmaze.com"
_TIMEOUT = httpx.Timeout(15.0)
# TVmaze allows 20 calls per 10 seconds — this semaphore keeps us safe
_SEM = asyncio.Semaphore(4)


async def _get(path: str, params: dict | None = None) -> dict | list | None:
    async with _SEM:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(f"{_BASE}{path}", params=params)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()


async def lookup_by_tvdb(tvdb_id: str | int) -> dict | None:
    return await _get("/lookup/shows", {"thetvdb": str(tvdb_id)})


async def lookup_by_imdb(imdb_id: str) -> dict | None:
    return await _get("/lookup/shows", {"imdb": imdb_id})


async def search_show(name: str) -> list[dict]:
    results = await _get("/search/shows", {"q": name})
    return results or []


async def show_episodes(tvmaze_id: int) -> list[dict]:
    episodes = await _get(f"/shows/{tvmaze_id}/episodes")
    return episodes or []
