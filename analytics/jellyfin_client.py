"""Thin async wrapper around the Jellyfin REST API."""

from __future__ import annotations

import httpx

from config import JELLYFIN_API_KEY, JELLYFIN_URL

_HEADERS = {
    "Authorization": f'MediaBrowser Token="{JELLYFIN_API_KEY}"',
}
_TIMEOUT = httpx.Timeout(10.0)


async def _get(path: str, params: dict | None = None) -> dict | list | str:
    async with httpx.AsyncClient(
        base_url=JELLYFIN_URL, headers=_HEADERS, timeout=_TIMEOUT
    ) as client:
        r = await client.get(path, params=params)
        r.raise_for_status()
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.text


# ── Server ───────────────────────────────────────────────────────────

async def ping() -> str | None:
    """Returns server name if reachable, None otherwise."""
    try:
        return await _get("/System/Ping")
    except (httpx.HTTPError, OSError):
        return None


async def system_info() -> dict:
    return await _get("/System/Info")


async def system_info_public() -> dict:
    return await _get("/System/Info/Public")


# ── Sessions ─────────────────────────────────────────────────────────

async def active_sessions() -> list[dict]:
    return await _get("/Sessions")


# ── Library ──────────────────────────────────────────────────────────

async def item_count(item_type: str) -> int:
    data = await _get(
        "/Items",
        {"includeItemTypes": item_type, "recursive": "true", "limit": "0"},
    )
    return data.get("TotalRecordCount", 0)


async def library_folders() -> list[dict]:
    return await _get("/Library/VirtualFolders")


# ── Series / Episodes ────────────────────────────────────────────────

async def series_list() -> list[dict]:
    data = await _get(
        "/Items",
        {
            "includeItemTypes": "Series",
            "recursive": "true",
            "fields": "ProviderIds,Status,RecursiveItemCount",
            "limit": "500",
        },
    )
    return data.get("Items", [])


async def series_episodes(series_id: str) -> list[dict]:
    data = await _get(
        f"/Shows/{series_id}/Episodes",
        {"fields": "PremiereDate,ProviderIds"},
    )
    return data.get("Items", [])


# ── Users ────────────────────────────────────────────────────────────

async def users() -> list[dict]:
    return await _get("/Users")


# ── Playback Reporting plugin ────────────────────────────────────────

async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(
        base_url=JELLYFIN_URL, headers=_HEADERS, timeout=_TIMEOUT
    ) as client:
        r = await client.post(path, json=body)
        r.raise_for_status()
        return r.json()


async def custom_query(sql: str, replace_user_id: bool = True) -> dict:
    return await _post(
        "/user_usage_stats/submit_custom_query",
        {"CustomQueryString": sql, "ReplaceUserId": replace_user_id},
    )
