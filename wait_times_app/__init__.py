"""Discord Cog for automated Halo Infinite playlist wait time polling.

Polls the Halo Infinite lobby WebSocket at a configurable interval, stores
historical wait times in the database, and exposes Discord slash commands for
querying current and historical wait time data.

Environment variables
---------------------
WAIT_TIMES_POLL_INTERVAL  Polling interval in seconds (default: 300).
WAIT_TIMES_RETENTION_DAYS Number of days to keep historical records (default: 30).
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord.ext import commands, tasks
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession as Session

import spnkr_app
from app.amqp_service import fetch_raw_playlist_entries
from app.models.playlist import PlaylistInfo, PlaylistWaitTimeRecord
from database_app.database import engine

logger = logging.getLogger(__name__)

_POLL_INTERVAL: int = int(os.environ.get("WAIT_TIMES_POLL_INTERVAL", "300"))
_RETENTION_DAYS: int = int(os.environ.get("WAIT_TIMES_RETENTION_DAYS", "30"))


# ── Database helpers ─────────────────────────────────────────────────────────

async def _save_wait_time_records(entries: list[dict], name_map: dict[str, str]) -> None:
    """Persist *entries* as :class:`PlaylistWaitTimeRecord` rows."""
    now = datetime.utcnow()
    async with Session(engine, expire_on_commit=False) as session:
        for entry in entries:
            asset_id = entry["asset_id"]
            name = name_map.get(asset_id) or asset_id
            record = PlaylistWaitTimeRecord(
                asset_id=asset_id,
                version_id=entry["version_id"],
                playlist_name=name,
                wait_time_ms=entry["wait_time_ms"],
                recorded_at=now,
            )
            session.add(record)
        await session.commit()


async def _upsert_playlist_info(entries: list[dict], name_map: dict[str, str]) -> None:
    """Insert or update :class:`PlaylistInfo` metadata rows."""
    now = datetime.utcnow()
    async with Session(engine, expire_on_commit=False) as session:
        for entry in entries:
            asset_id = entry["asset_id"]
            name = name_map.get(asset_id) or asset_id
            existing = await session.get(PlaylistInfo, asset_id)
            if existing:
                existing.version_id = entry["version_id"]
                existing.playlist_name = name
                existing.last_seen = now
                session.add(existing)
            else:
                session.add(PlaylistInfo(
                    asset_id=asset_id,
                    version_id=entry["version_id"],
                    playlist_name=name,
                    last_seen=now,
                ))
        await session.commit()


async def _purge_old_records(retention_days: int) -> int:
    """Delete records older than *retention_days* days; return deleted count."""
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    async with Session(engine, expire_on_commit=False) as session:
        statement = select(PlaylistWaitTimeRecord).where(
            PlaylistWaitTimeRecord.recorded_at < cutoff
        )
        results = await session.exec(statement)
        old_records = results.all()
        for record in old_records:
            await session.delete(record)
        await session.commit()
        return len(old_records)


async def _get_latest_wait_times() -> list[PlaylistWaitTimeRecord]:
    """Return the most recent wait time record for each known playlist."""
    async with Session(engine) as session:
        # Fetch all distinct asset_ids
        info_stmt = select(PlaylistInfo)
        info_results = await session.exec(info_stmt)
        playlist_infos = info_results.all()

        latest: list[PlaylistWaitTimeRecord] = []
        for info in playlist_infos:
            stmt = (
                select(PlaylistWaitTimeRecord)
                .where(PlaylistWaitTimeRecord.asset_id == info.asset_id)
                .order_by(PlaylistWaitTimeRecord.recorded_at.desc())
                .limit(1)
            )
            results = await session.exec(stmt)
            record = results.first()
            if record:
                latest.append(record)
        return latest


async def _get_playlist_stats(asset_id: str, hours: int = 24) -> Optional[dict]:
    """Return aggregated wait time statistics for a playlist over the last *hours*."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with Session(engine) as session:
        stmt = (
            select(PlaylistWaitTimeRecord)
            .where(PlaylistWaitTimeRecord.asset_id == asset_id)
            .where(PlaylistWaitTimeRecord.recorded_at >= since)
            .order_by(PlaylistWaitTimeRecord.recorded_at.asc())
        )
        results = await session.exec(stmt)
        records = results.all()

    if not records:
        return None

    wait_times = [r.wait_time_ms for r in records]
    avg_ms = sum(wait_times) / len(wait_times)
    min_ms = min(wait_times)
    max_ms = max(wait_times)

    # Simple trend: compare first half average to second half average
    mid = len(records) // 2
    if mid > 0:
        first_half = sum(r.wait_time_ms for r in records[:mid]) / mid
        second_half = sum(r.wait_time_ms for r in records[mid:]) / (len(records) - mid)
        if second_half > first_half * 1.1:
            trend = "↑ nouseva"
        elif second_half < first_half * 0.9:
            trend = "↓ laskeva"
        else:
            trend = "→ vakaa"
    else:
        trend = "→ vakaa"

    return {
        "name": records[0].playlist_name or asset_id,
        "count": len(records),
        "avg_ms": avg_ms,
        "min_ms": min_ms,
        "max_ms": max_ms,
        "trend": trend,
        "hours": hours,
    }


# ── Name resolution ──────────────────────────────────────────────────────────

async def _resolve_names(entries: list[dict]) -> dict[str, str]:
    """Resolve asset IDs to human-readable playlist names via the spnkr API.

    Returns a dict mapping asset_id → playlist_name.
    """
    name_map: dict[str, str] = {}
    try:
        async for client in spnkr_app.get_client():
            for entry in entries:
                asset_id = entry["asset_id"]
                version_id = entry["version_id"]
                if not asset_id or asset_id in name_map:
                    continue
                try:
                    resp = await client.discovery_ugc.get_playlist(asset_id, version_id)
                    playlist = await resp.parse()
                    name = getattr(playlist, "public_name", None) or asset_id
                    name_map[asset_id] = name
                except Exception as exc:
                    logger.warning("Could not resolve name for %s: %s", asset_id, exc)
                    name_map[asset_id] = asset_id
            break
    except Exception as exc:
        logger.error("Failed to obtain spnkr client for name resolution: %s", exc)
    return name_map


# ── Helper formatting ─────────────────────────────────────────────────────────

def _ms_to_human(ms: int) -> str:
    seconds = ms // 1000
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


# ── Discord Cog ───────────────────────────────────────────────────────────────

class WaitTimesApp(commands.Cog):
    """Discord Cog that polls Halo Infinite playlist wait times automatically."""

    def __init__(self, bot: discord.Bot) -> None:
        self.bot = bot
        self._poll_interval = _POLL_INTERVAL
        self._retention_days = _RETENTION_DAYS
        self._last_poll: Optional[datetime] = None
        self._poll_error_count: int = 0

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        logger.info(
            "WaitTimesApp cog ready (poll_interval=%ds, retention=%d days)",
            self._poll_interval,
            self._retention_days,
        )
        if not self._polling_loop.is_running():
            self._polling_loop.start()

    def cog_unload(self) -> None:
        self._polling_loop.cancel()

    # ── Polling task ─────────────────────────────────────────────────────────

    @tasks.loop(seconds=1)
    async def _polling_loop(self) -> None:
        """Internal loop driver – waits for the configured interval then polls."""
        if self._last_poll is None or (
            datetime.utcnow() - self._last_poll
        ).total_seconds() >= self._poll_interval:
            await self._do_poll()

    @_polling_loop.before_loop
    async def _before_polling_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _do_poll(self) -> None:
        """Fetch wait times, persist to DB, and purge old records."""
        logger.info("Polling Halo Infinite playlist wait times…")
        try:
            # Ensure tokens are fresh
            async for _ in spnkr_app.get_client():
                break
        except Exception as exc:
            logger.error("Token refresh failed during poll: %s", exc)
            self._poll_error_count += 1
            return

        if spnkr_app.player_cache is None or not spnkr_app.player_cache.is_valid:
            logger.error("No valid player tokens available for poll")
            self._poll_error_count += 1
            return

        spartan_token = spnkr_app.player_cache.spartan_token.token
        clearance_token = spnkr_app.player_cache.clearance_token.token

        entries = await fetch_raw_playlist_entries(spartan_token, clearance_token)

        if not entries:
            logger.warning("Poll returned no playlist entries")
            self._poll_error_count += 1
        else:
            self._poll_error_count = 0
            name_map = await _resolve_names(entries)
            await _save_wait_time_records(entries, name_map)
            await _upsert_playlist_info(entries, name_map)
            deleted = await _purge_old_records(self._retention_days)
            logger.info(
                "Poll complete: %d entries stored, %d old records purged",
                len(entries),
                deleted,
            )

        self._last_poll = datetime.utcnow()

    # ── Discord commands ──────────────────────────────────────────────────────

    @discord.slash_command(description="Näytä Halo Infinite pelilistausten odotusajat")
    async def waittimes(self, ctx: discord.ApplicationContext) -> None:
        """Show current wait times for all Halo Infinite playlists."""
        await ctx.defer(ephemeral=True)

        latest = await _get_latest_wait_times()

        if not latest:
            await ctx.followup.send(
                "Ei odotusaikatietoja saatavilla. Botti hakee dataa muutaman minuutin välein.",
                ephemeral=True,
            )
            return

        # Sort by wait time ascending
        latest.sort(key=lambda r: r.wait_time_ms)

        embed = discord.Embed(
            title="⏱️ Halo Infinite – Pelilistausten odotusajat",
            color=discord.Color.blue(),
        )

        for record in latest:
            name = record.playlist_name or record.asset_id
            embed.add_field(
                name=name,
                value=_ms_to_human(record.wait_time_ms),
                inline=True,
            )

        if self._last_poll:
            embed.set_footer(
                text=f"Päivitetty {self._last_poll.strftime('%H:%M:%S')} UTC | "
                     f"HaloBotti by AapoKaapo",
                icon_url="https://halofin.land/HaloFinland.png",
            )

        await ctx.followup.send(embed=embed, ephemeral=True)

    @discord.slash_command(description="Näytä tilastot yksittäiselle pelilistalle")
    async def playliststats(
        self,
        ctx: discord.ApplicationContext,
        playlist: str,
        hours: int = 24,
    ) -> None:
        """Show historical wait time statistics for a named playlist.

        Args:
            playlist: Playlist name (partial match accepted).
            hours:    How many hours of history to include (default 24).
        """
        await ctx.defer(ephemeral=True)

        # Resolve partial name to asset_id
        async with Session(engine) as session:
            info_stmt = select(PlaylistInfo)
            results = await session.exec(info_stmt)
            all_infos = results.all()

        match = next(
            (
                info
                for info in all_infos
                if playlist.lower() in (info.playlist_name or "").lower()
                or playlist.lower() in info.asset_id.lower()
            ),
            None,
        )

        if match is None:
            names = [info.playlist_name or info.asset_id for info in all_infos]
            listing = "\n".join(f"• {n}" for n in names) if names else "Ei tiedossa olevia pelilistoja."
            await ctx.followup.send(
                f"Pelilistaa '{playlist}' ei löydy. Tunnetut pelilistaukset:\n{listing}",
                ephemeral=True,
            )
            return

        stats = await _get_playlist_stats(match.asset_id, hours=hours)

        if stats is None:
            await ctx.followup.send(
                f"Ei historiatietoja pelilistalle '{match.playlist_name}' viimeiseltä {hours}h.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📊 {stats['name']} – Odotusaikatilastot",
            color=discord.Color.green(),
        )
        embed.add_field(name="Keskiarvo", value=_ms_to_human(int(stats["avg_ms"])), inline=True)
        embed.add_field(name="Minimi", value=_ms_to_human(stats["min_ms"]), inline=True)
        embed.add_field(name="Maksimi", value=_ms_to_human(stats["max_ms"]), inline=True)
        embed.add_field(name="Trendi", value=stats["trend"], inline=True)
        embed.add_field(name="Mittauksia", value=str(stats["count"]), inline=True)
        embed.add_field(name="Aikaväli", value=f"{hours}h", inline=True)
        embed.set_footer(
            text="HaloBotti by AapoKaapo",
            icon_url="https://halofin.land/HaloFinland.png",
        )

        await ctx.followup.send(embed=embed, ephemeral=True)
