import asyncio
import logging
import struct
import time
import uuid
from typing import Optional

import aiohttp
import discord
import spnkr_app
from discord import Interaction
from discord.ext.pages import Page, Paginator
from spnkr.tools import LIFECYCLE_MAP

from database_app.database import engine_start
from discord_app.embeds import (
    create_match_info,
    create_match_skill_embed,
    create_rank_embed,
    create_series_info,
)
from spnkr_app import fetch_player_match_data, fetch_player_match_skills, get_client, get_xbl_profiles

logger = logging.getLogger(__name__)

bot = discord.Bot()

# ── WebSocket / AMQP constants ───────────────────────────────────────────────
_LOBBY_WS_URL = "wss://lobby-hi.svc.halowaypoint.com/"
_WS_TIMEOUT = 30  # seconds
_AMQP_PROTOCOL_HEADER = b"AMQP\x00\x01\x00\x00"

# ── Bond CompactBinary wire types ────────────────────────────────────────────
_BT_STOP    = 0
_BT_BOOL    = 2
_BT_UINT8   = 3
_BT_UINT16  = 4
_BT_UINT32  = 5
_BT_UINT64  = 6
_BT_FLOAT   = 7
_BT_DOUBLE  = 8
_BT_STRING  = 9
_BT_STRUCT  = 10
_BT_LIST    = 11
_BT_SET     = 12
_BT_MAP     = 13
_BT_INT8    = 14
_BT_INT16   = 15
_BT_INT32   = 16
_BT_INT64   = 17
_BT_WSTRING = 18


def _read_vint(buf: bytes, pos: int) -> tuple[int, int]:
    """Decode a Bond variable-length unsigned integer; return (value, new_pos)."""
    v, shift = 0, 0
    while pos < len(buf):
        b = buf[pos]; pos += 1
        v |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return v, pos


def _read_bond_value(buf: bytes, pos: int, wtype: int) -> tuple:
    """Decode one Bond value of the given wire type; return (value, new_pos)."""
    try:
        if wtype == _BT_BOOL:
            return bool(buf[pos]), pos + 1
        if wtype == _BT_UINT8:
            return buf[pos], pos + 1
        if wtype == _BT_INT8:
            return struct.unpack_from('b', buf, pos)[0], pos + 1
        if wtype in (_BT_UINT16, _BT_INT16, _BT_UINT32, _BT_INT32, _BT_UINT64, _BT_INT64):
            return _read_vint(buf, pos)
        if wtype == _BT_FLOAT:
            return struct.unpack_from('<f', buf, pos)[0], pos + 4
        if wtype == _BT_DOUBLE:
            return struct.unpack_from('<d', buf, pos)[0], pos + 8
        if wtype == _BT_STRING:
            length, p = _read_vint(buf, pos)
            return buf[p:p + length].decode('utf-8', errors='replace'), p + length
        if wtype == _BT_WSTRING:
            length, p = _read_vint(buf, pos)
            return buf[p:p + length * 2].decode('utf-16-le', errors='replace'), p + length * 2
        if wtype == _BT_STRUCT:
            return _parse_bond_struct_v2(buf, pos)
        if wtype in (_BT_LIST, _BT_SET):
            if pos >= len(buf):
                return [], pos
            elem_type = buf[pos]; pos += 1
            count, pos = _read_vint(buf, pos)
            items = []
            for _ in range(count):
                v, pos = _read_bond_value(buf, pos, elem_type)
                items.append(v)
            return items, pos
        if wtype == _BT_MAP:
            if pos + 2 > len(buf):
                return {}, pos
            key_t = buf[pos]; val_t = buf[pos + 1]; pos += 2
            count, pos = _read_vint(buf, pos)
            m = {}
            for _ in range(count):
                k, pos = _read_bond_value(buf, pos, key_t)
                v, pos = _read_bond_value(buf, pos, val_t)
                m[k] = v
            return m, pos
    except Exception as exc:
        logger.debug("Bond read error (wtype=%d, pos=%d): %s", wtype, pos, exc)
    return None, len(buf)


def _parse_bond_struct_v2(buf: bytes, pos: int = 0) -> tuple[dict, int]:
    """Parse a Bond CompactBinary v2 struct.

    In v2, explicit field ordinals are encoded as 2-byte uint16 LE (instead of
    varint in v1).  Returns (fields_dict, new_pos) where keys are ordinals.
    """
    fields: dict[int, object] = {}
    last_ordinal = 0
    while pos < len(buf):
        type_byte = buf[pos]; pos += 1
        wtype = type_byte & 0x1F
        if wtype in (0, 1):  # BT_STOP or BT_STOP_BASE
            break
        delta = (type_byte >> 5) & 0x07
        if delta == 0:
            # Explicit ordinal follows as uint16 LE
            if pos + 2 > len(buf):
                break
            ordinal = struct.unpack_from('<H', buf, pos)[0]
            pos += 2
        else:
            ordinal = last_ordinal + delta
        last_ordinal = ordinal
        v, pos = _read_bond_value(buf, pos, wtype)
        fields[ordinal] = v
    return fields, pos


def _bond_guid_to_str(guid_fields: dict) -> str:
    """Convert a parsed Bond GUID struct (4 × uint32 fields 0-3) to a UUID string."""
    d1 = guid_fields.get(0, 0)
    d2 = guid_fields.get(1, 0)
    d3 = guid_fields.get(2, 0)
    d4 = guid_fields.get(3, 0)
    try:
        # Bond.GUID stores the Windows GUID in four uint32 chunks (little-endian)
        raw = struct.pack('<IIII', d1, d2, d3, d4)
        return str(uuid.UUID(bytes_le=raw))
    except Exception:
        return f"{d1:08x}{d2:08x}{d3:08x}{d4:08x}"


def _extract_bond_data_from_frame(frame: bytes) -> Optional[bytes]:
    """Locate Bond payload within a raw AMQP binary frame.

    Searches for the AMQP data-section descriptor (0x00 0x53 0x75) and returns
    the enclosed binary content.  If the descriptor is not found the raw frame
    is returned as-is so the caller can still attempt parsing.
    """
    _DATA_SECTION = b'\x00\x53\x75'  # described-type + smallulong + data-section id
    idx = frame.find(_DATA_SECTION)
    if idx != -1:
        pos = idx + 3
        if pos < len(frame):
            prefix = frame[pos]; pos += 1
            if prefix == 0xa0 and pos < len(frame):    # vbin8
                length = frame[pos]; pos += 1
                return frame[pos:pos + length]
            if prefix == 0xb0 and pos + 4 <= len(frame):  # vbin32
                length = struct.unpack_from('>I', frame, pos)[0]
                pos += 4
                return frame[pos:pos + length]
    # No AMQP data-section wrapper found – try the raw frame body
    return frame if len(frame) > 8 else None


def _parse_playlist_bond(bond_bytes: bytes) -> list[dict]:
    """Parse a PlaylistResponse from Bond CompactBinary v2.

    Schema (field ordinals):
      PlaylistResponse  [51] → List<List<PlaylistContainer>>
      PlaylistContainer  [2] → double  WaitTime  (seconds)
                         [3] → PlaylistInformation
      PlaylistInformation[1] → GUID  AssetId
                         [2] → GUID  VersionId
      GUID               [0..3] → uint32 Data1..Data4

    Returns a list of dicts with keys 'wait_time_ms', 'asset_id', 'version_id'.
    """
    results: list[dict] = []
    try:
        top, _ = _parse_bond_struct_v2(bond_bytes, 0)
        containers = top.get(51)   # List<List<PlaylistContainer>>
        if not isinstance(containers, list):
            logger.debug("Field 51 not found or is not a list in Bond response")
            return results

        for container_list in containers:
            if not isinstance(container_list, list):
                continue
            for entry in container_list:
                if not isinstance(entry, dict):
                    continue
                wait_time_s = entry.get(2, 0.0)   # double, seconds
                info = entry.get(3)

                asset_id = ""
                version_id = ""
                if isinstance(info, dict):
                    asset_guid = info.get(1)
                    if isinstance(asset_guid, dict):
                        asset_id = _bond_guid_to_str(asset_guid)
                    version_guid = info.get(2)
                    if isinstance(version_guid, dict):
                        version_id = _bond_guid_to_str(version_guid)

                if isinstance(wait_time_s, (int, float)) and wait_time_s >= 0:
                    results.append({
                        "wait_time_ms": int(wait_time_s * 1000),
                        "asset_id": asset_id,
                        "version_id": version_id,
                    })
    except Exception as exc:
        logger.warning("Error parsing playlist Bond data: %s", exc)
    return results


# ── AMQP 1.0 frame helpers ───────────────────────────────────────────────────

# Server-to-client performative descriptors used to drive the state machine.
# Each is the 3-byte described-type prefix: 0x00 (described) + 0x53 (smallulong) + code.
_AMQP_DESC_BEGIN    = b'\x00\x53\x11'  # server BEGIN  → we send ATTACH
_AMQP_DESC_ATTACH   = b'\x00\x53\x12'  # server ATTACH → we send FLOW
_AMQP_DESC_TRANSFER = b'\x00\x53\x14'  # server TRANSFER → contains Bond payload


def _make_amqp_frame(channel: int, payload: bytes) -> bytes:
    """Wrap *payload* in an AMQP 1.0 frame (8-byte header)."""
    return struct.pack('>IBBH', 8 + len(payload), 2, 0x00, channel) + payload


def _encode_str_amqp(s: str) -> bytes:
    b = s.encode()
    return (b'\xa1' + bytes([len(b)]) + b) if len(b) < 256 else (b'\xb1' + struct.pack('>I', len(b)) + b)


def _classify_amqp_frame(data: bytes) -> Optional[bytes]:
    """Return the 3-byte AMQP performative descriptor found in *data*, or None.

    Reads the data-offset (DOFF) from byte 4 of the frame header to locate the
    frame body, then checks for the standard described-type marker (0x00 0x53).
    """
    if len(data) < 11:
        return None
    doff = data[4]            # data offset in 32-bit words
    body_start = doff * 4    # actual start of the performative
    if body_start + 3 > len(data):
        return None
    if data[body_start] == 0x00 and data[body_start + 1] == 0x53:
        return bytes(data[body_start:body_start + 3])
    return None


def _encode_amqp_open(container_id: str = "halobotti") -> bytes:
    """Encode an AMQP OPEN performative (descriptor 0x10)."""
    items = _encode_str_amqp(container_id) + b'\x40' + b'\x70' + struct.pack('>I', 65536)
    count = 3
    list_body = struct.pack('>I', count) + items
    list_enc = b'\xd0' + struct.pack('>I', len(list_body)) + list_body
    return _make_amqp_frame(0, b'\x00\x53\x10' + list_enc)


def _encode_amqp_begin() -> bytes:
    """Encode an AMQP BEGIN performative (descriptor 0x11) for channel 0."""
    items = (b'\x40'                                         # remote-channel: null
             + b'\x43'                                       # next-outgoing-id: uint 0
             + b'\x70' + struct.pack('>I', 0x7FFFFFFF)      # incoming-window
             + b'\x70' + struct.pack('>I', 0x7FFFFFFF))     # outgoing-window
    count = 4
    list_body = struct.pack('>I', count) + items
    list_enc = b'\xd0' + struct.pack('>I', len(list_body)) + list_body
    return _make_amqp_frame(0, b'\x00\x53\x11' + list_enc)


def _encode_amqp_attach(name: str = "halobotti-wt", handle: int = 0) -> bytes:
    """Encode an AMQP ATTACH performative (descriptor 0x12) for a receiver link.

    Fields: name, handle, role=true (receiver), snd-settle-mode=mixed,
    rcv-settle-mode=first, source=null, target=null.
    """
    items = (_encode_str_amqp(name)          # [0] name
             + b'\x43'                        # [1] handle = uint(0)
             + b'\x41'                        # [2] role = true (receiver)
             + b'\x50\x02'                    # [3] snd-settle-mode = mixed
             + b'\x50\x00'                    # [4] rcv-settle-mode = first
             + b'\x40'                        # [5] source = null
             + b'\x40')                       # [6] target = null
    count = 7
    list_body = struct.pack('>I', count) + items
    list_enc = b'\xd0' + struct.pack('>I', len(list_body)) + list_body
    return _make_amqp_frame(0, b'\x00\x53\x12' + list_enc)


def _encode_amqp_flow(link_credit: int = 1) -> bytes:
    """Encode an AMQP FLOW performative (descriptor 0x13) to request messages.

    Grants *link_credit* to the server so it starts delivering playlist data.
    Fields: next-incoming-id, incoming-window, next-outgoing-id,
    outgoing-window, handle, delivery-count, link-credit.
    """
    items = (b'\x43'                                        # [0] next-incoming-id = 0
             + b'\x70' + struct.pack('>I', 0x7FFFFFFF)     # [1] incoming-window
             + b'\x43'                                      # [2] next-outgoing-id = 0
             + b'\x43'                                      # [3] outgoing-window = 0
             + b'\x43'                                      # [4] handle = 0
             + b'\x43'                                      # [5] delivery-count = 0
             + b'\x52' + bytes([link_credit]))              # [6] link-credit (uint8)
    count = 7
    list_body = struct.pack('>I', count) + items
    list_enc = b'\xd0' + struct.pack('>I', len(list_body)) + list_body
    return _make_amqp_frame(0, b'\x00\x53\x13' + list_enc)


async def fetch_playlist_wait_times() -> Optional[dict]:
    """Fetch playlist wait times via WebSocket to the Halo Infinite Lobby API.

    Implements the full AMQPWSB10 state machine required by the lobby service:

    1. **Bootstrap** – send AMQP header + OPEN + BEGIN.
    2. **Attach** – on receiving the server's BEGIN (acknowledgeBootstrap), send
       an AMQP ATTACH frame to open a receiver link.
    3. **Initialize** – on receiving the server's ATTACH (acknowledgeAttach),
       send an AMQP FLOW frame to grant link credit and trigger delivery.
    4. **Receive** – on receiving a TRANSFER frame (playlistResponse), extract
       and parse the Bond CompactBinary v2 payload, then close the socket.

    Spartan and Clearance tokens are read from *spnkr_app.player_cache*.
    Playlist asset IDs are resolved to human-readable names via the spnkr
    discovery API before the dict is returned.

    Returns:
        ``{"playlist_name": wait_time_ms, ...}`` or ``None`` on failure.
    """
    logger.info("Fetching playlist wait times from Halo Lobby WebSocket")

    # Ensure player_cache is populated with valid tokens
    try:
        async for _ in spnkr_app.get_client():
            break
    except Exception as exc:
        logger.error("Failed to refresh player tokens: %s", exc)
        return None

    if spnkr_app.player_cache is None:
        logger.error("player_cache is None – cannot authenticate to lobby WebSocket")
        return None

    spartan_token = spnkr_app.player_cache.spartan_token.token
    clearance_token = spnkr_app.player_cache.clearance_token.token
    headers = {
        "X-343-Authorization-Spartan": spartan_token,
        "343-Clearance": clearance_token,
    }

    playlist_entries: list[dict] = []
    logger.debug("Connecting to lobby WebSocket at %s", _LOBBY_WS_URL)

    try:
        async with asyncio.timeout(_WS_TIMEOUT):
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    _LOBBY_WS_URL,
                    protocols=["AMQPWSB10"],
                    headers=headers,
                ) as ws:
                    # ── Step 1: Bootstrap ────────────────────────────────────
                    logger.debug("Sending AMQP bootstrap (header + OPEN + BEGIN)")
                    await ws.send_bytes(_AMQP_PROTOCOL_HEADER)
                    await ws.send_bytes(_encode_amqp_open())
                    await ws.send_bytes(_encode_amqp_begin())

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            data: bytes = msg.data
                            logger.debug("Received binary frame: %d bytes", len(data))

                            # Skip the AMQP 1.0 protocol header echo
                            if data[:4] == b"AMQP":
                                logger.debug("Skipping AMQP protocol header echo")
                                continue

                            frame_class = _classify_amqp_frame(data)
                            logger.debug(
                                "Frame class: %s",
                                frame_class.hex() if frame_class else "unknown",
                            )

                            if frame_class == _AMQP_DESC_BEGIN:
                                # ── Step 2: Attach ───────────────────────────
                                logger.debug(
                                    "Bootstrap acknowledged (server BEGIN received); "
                                    "sending ATTACH"
                                )
                                await ws.send_bytes(_encode_amqp_attach())

                            elif frame_class == _AMQP_DESC_ATTACH:
                                # ── Step 3: Initialize (send FLOW) ───────────
                                logger.debug(
                                    "ATTACH acknowledged; sending FLOW to request data"
                                )
                                await ws.send_bytes(_encode_amqp_flow())

                            elif frame_class == _AMQP_DESC_TRANSFER:
                                # ── Step 4: Receive playlist data ─────────────
                                logger.debug("TRANSFER received; extracting Bond payload")
                                bond_bytes = _extract_bond_data_from_frame(data)
                                if bond_bytes is not None:
                                    entries = _parse_playlist_bond(bond_bytes)
                                    if entries:
                                        logger.info(
                                            "Parsed %d playlist entries from Bond message",
                                            len(entries),
                                        )
                                        playlist_entries.extend(entries)
                                        # Close the socket cleanly now that we have the data
                                        try:
                                            await ws.close()
                                            logger.debug("Socket closed by client")
                                        except Exception as close_exc:
                                            logger.debug("Socket closed by server: %s", close_exc)
                                        break

                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error("WebSocket error: %s", ws.exception())
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            logger.info("WebSocket closed by server")
                            break

    except asyncio.TimeoutError:
        logger.error("WebSocket connection timed out after %ds", _WS_TIMEOUT)
    except aiohttp.ClientConnectorError as exc:
        logger.error("Cannot connect to lobby WebSocket: %s", exc)
    except aiohttp.WSServerHandshakeError as exc:
        logger.error("WebSocket handshake failed (status %s): %s", exc.status, exc)
    except Exception as exc:
        logger.exception("Unexpected error fetching playlist wait times: %s", exc)

    if not playlist_entries:
        logger.warning("No playlist entries received from lobby WebSocket")
        return None

    # Resolve asset IDs to human-readable names via the spnkr discovery API
    wait_times: dict[str, int] = {}
    logger.info("Resolving names for %d playlist(s)", len(playlist_entries))

    async def _lookup_name(entry: dict) -> tuple[str, int]:
        try:
            async for client in spnkr_app.get_client():
                resp = await client.discovery_ugc.get_playlist(
                    entry["asset_id"], entry["version_id"]
                )
                playlist = await resp.parse()
                # public_name is the human-readable label exposed by the spnkr
                # Asset model; fall back to the raw asset_id when absent so the
                # caller always gets a usable key.
                name = getattr(playlist, "public_name", None) or entry["asset_id"]
                return name, entry["wait_time_ms"]
        except Exception as exc:
            logger.warning(
                "Could not resolve name for playlist %s: %s", entry["asset_id"], exc
            )
        return entry["asset_id"], entry["wait_time_ms"]

    try:
        results = await asyncio.gather(*[_lookup_name(e) for e in playlist_entries])
        wait_times = {name: wt for name, wt in results if name}
    except Exception as exc:
        logger.error("Failed to resolve playlist names: %s", exc)
        wait_times = {e["asset_id"]: e["wait_time_ms"] for e in playlist_entries}

    logger.info("Returning %d playlist wait time(s)", len(wait_times))
    return wait_times if wait_times else None


class PublishView(discord.ui.View):
    def __init__(self):
        self.paginator = None
        super().__init__()

    def add_paginator(self, paginator):
        self.paginator = paginator

    @discord.ui.button(label="Publish")
    async def callback(self, button, interaction):
        if self.paginator:
            self.paginator.custom_view = None
            await self.paginator.respond(interaction)

        elif self.message:
            await interaction.response.edit_message(view=None)
            await self.message.channel.send(embeds=self.message.embeds)


@bot.listen('on_ready', once=True)
async def startup():
    print(f"Bot is up and running: {bot.user.name} - {bot.user.id}")
    await engine_start()


@bot.command(description="Hae pelaajan ranked-suoritus")
async def rank(ctx, gamertag: str) -> None:
    """Näytä pelaajan CSR-eteneminen ja viimeiset ranked-matsit."""
    message = await ctx.respond(f"Haetaan pelaajan {gamertag} data", ephemeral=True)
    try:
        async for client in get_client():
            profile = await get_xbl_profiles(client, gamertag)
            if not profile:
                await message.edit_original_response(content=f"Virhe: Pelaajaa '{gamertag}' ei löydy")
                return
            start_time = time.time()
            match_skills = await fetch_player_match_skills(profile[0].gamertag, count=20)
            end_time = time.time()
            print("match_skills took %f ms" % ((end_time - start_time) * 1000.0))
            pages = []
            xuids = []
            embed, files = await create_rank_embed(profile[0], match_skills)
            summary_page = Page(embeds=[embed], files=files)
            pages.append(summary_page)
            for match_skill in match_skills:
                for value in match_skill.value:
                    xuids.append(value.id)
            profiles = await get_xbl_profiles(client, xuids)
            for match_skill in match_skills:
                page = Page(embeds=[await create_match_skill_embed(profiles, match_skill)])
                pages.append(page)
            custom_view = PublishView()

            paginator = SeriesPaginator(pages=pages)
            custom_view.add_paginator(paginator)
            paginator.custom_view = custom_view
            await paginator.respond(message, ephemeral=True)
    except Exception as e:
        print(f"Virhe rank-komennossa: {e}")
        await message.edit_original_response(content="Virhe: Ranked-datan hakeminen epäonnistui")


@bot.command(description="Hae Ranked Arena -pelilistauksen odotusaika")
async def wait_time(ctx) -> None:
    """Näytä Ranked Arena -pelilistauksen arvioitu odotusaika."""
    message = await ctx.respond("Haetaan Ranked Arena odotusaikaa...", ephemeral=True)
    wait_times = await fetch_playlist_wait_times()

    if not wait_times:
        await message.edit_original_response(content="Virhe: Odotusaikoja ei voitu hakea")
        return

    ranked_name = next(
        (name for name in wait_times if "ranked arena" in name.lower()), None
    )

    if ranked_name is None:
        await message.edit_original_response(content="Virhe: Ranked Arena -pelilistaa ei löydy")
        return

    wait_ms = wait_times[ranked_name]
    wait_seconds = wait_ms / 1000
    minutes = int(wait_seconds // 60)
    seconds = int(wait_seconds % 60)

    embed = discord.Embed(
        title="Ranked Arena Odotusaika",
        description=f"**{minutes}m {seconds}s**",
        color=discord.Color.blue()
    )
    embed.add_field(name="Pelilistaus", value=ranked_name)
    # The Bond PlaylistResponse schema only exposes WaitTime and asset identifiers;
    # the isEnabled / online-status field is not present in the lobby WebSocket data.
    embed.set_footer(text="HaloBotti 2.0 by AapoKaapo", icon_url="https://halofin.land/HaloFinland.png")

    await message.edit_original_response(content="", embed=embed)


class SeriesPaginator(Paginator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def on_timeout(self):
        await self.goto_page(0)
        await super().on_timeout()


class MatchSelect(discord.ui.Select):
    def __init__(self, match_history):
        self.match_history = match_history
        options = []
        for custom_match in match_history:
            option = discord.SelectOption(
                label=f"{LIFECYCLE_MAP[custom_match.match_stats.match_info.lifecycle_mode]}: {custom_match.match_gamemode.public_name} - {custom_match.match_map.public_name}",
                value=f"{custom_match.match_stats.match_id}",
                emoji=None
            )
            options.append(option)
        super().__init__(placeholder="Select A Match", max_values=len(options), options=options)

    async def callback(self, interaction: Interaction):
        await interaction.response.defer()
        pages = []
        files = []
        match_ids = self.values
        selected_matches = [match for match in self.match_history if f"{match.match_stats.match_id}" in match_ids]
        embed, file = await create_series_info(selected_matches)
        series_page = Page(embeds=[embed], files=file)
        pages.append(series_page)
        files.append(file)
        for match in selected_matches:
            match_embed, file = await create_match_info(match)
            page = Page(embeds=[match_embed], files=file)
            pages.append(page)
            files.append(file)
        custom_view = PublishView()
        paginator = SeriesPaginator(pages=pages)
        custom_view.add_paginator(paginator)
        paginator.custom_view = custom_view
        await paginator.respond(interaction, ephemeral=True)


class SeriesView(discord.ui.View):
    def __init__(self, *args):
        super().__init__(*args)

    async def on_timeout(self):
        try:
            await self.parent.edit_original_response(delete_after=0)
        except AttributeError:
            await self.message.edit(delete_after=0)


@bot.command(description="Luo yhteenveto pelatuista matseista")
async def make_series(ctx, gamertag: str, count: Optional[int] = 25, start: Optional[int] = 0, match_type: str = "all") -> None:
    """Hae pelaajan matsit ja luo niistä sarjayhteenveto valintanäkymällä."""
    msg = await ctx.respond(content="Haetaan matseja...", ephemeral=True)
    try:
        match_history = await fetch_player_match_data(gamertag, start=start, count=count, match_type=match_type)
        if not match_history:
            await msg.edit_original_response(content="Virhe: Matseja ei löydy annetuilla hakuehdoilla")
            return
        await msg.edit_original_response(content=f"Haetaan matseja... ({len(match_history)}/{count})")
        select = MatchSelect(match_history)
        await msg.edit_original_response(content="", view=SeriesView(select))
    except Exception as e:
        print(f"Virhe make_series-komennossa: {e}")
        await msg.edit_original_response(content="Virhe: Matsien hakeminen epäonnistui")
