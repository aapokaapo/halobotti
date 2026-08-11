"""Halo Infinite Lobby WebSocket client.

Thin wrapper around :mod:`app.amqp_service` that resolves playlist asset IDs
to human-readable names and returns a ``{"playlist_name": wait_time_ms}`` dict.
"""

import logging
from typing import Optional

import spnkr_app
from app.amqp_service import fetch_raw_playlist_entries

logger = logging.getLogger(__name__)


async def fetch_playlist_wait_times() -> Optional[dict[str, int]]:
    """Fetch playlist wait times live from the Halo Infinite Lobby WebSocket.

    Tokens are read from *spnkr_app.player_cache*.  Playlist asset IDs are
    resolved to human-readable names via the spnkr discovery API.

    Returns:
        ``{"playlist_name": wait_time_ms, ...}`` or ``None`` on failure.
    """
    try:
        async for _ in spnkr_app.get_client():
            break
    except Exception as exc:
        logger.error("Token refresh failed: %s", exc)
        return None

    if spnkr_app.player_cache is None or not spnkr_app.player_cache.is_valid:
        logger.error("No valid player tokens available")
        return None

    spartan_token = spnkr_app.player_cache.spartan_token.token
    clearance_token = spnkr_app.player_cache.clearance_token.token

    entries = await fetch_raw_playlist_entries(spartan_token, clearance_token)
    if not entries:
        logger.warning("No playlist entries received from lobby WebSocket")
        return None

    logger.info("Resolving names for %d playlist(s)", len(entries))
    wait_times: dict[str, int] = {}
    try:
        async for client in spnkr_app.get_client():
            for entry in entries:
                asset_id = entry["asset_id"]
                version_id = entry["version_id"]
                try:
                    resp = await client.discovery_ugc.get_playlist(asset_id, version_id)
                    playlist = await resp.parse()
                    name = getattr(playlist, "public_name", None) or asset_id
                except Exception as exc:
                    logger.warning("Could not resolve name for %s: %s", asset_id, exc)
                    name = asset_id
                wait_times[name] = entry["wait_time_ms"]
            break
    except Exception as exc:
        logger.error("Failed to obtain spnkr client for name resolution: %s", exc)
        wait_times = {e["asset_id"]: e["wait_time_ms"] for e in entries}

    logger.info("Returning %d playlist wait time(s)", len(wait_times))
    return wait_times if wait_times else None
