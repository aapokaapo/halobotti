"""AMQP WebSocket service for fetching Halo Infinite playlist wait times.

Implements the AMQPWSB10 state machine required by the Halo Infinite lobby
service at wss://lobby-hi.svc.halowaypoint.com/ and decodes the Bond
CompactBinary v2 payload to extract per-playlist wait times.

Protocol state machine
----------------------
1. Bootstrap – send AMQP header + OPEN + BEGIN.
2. Attach    – on server BEGIN, send ATTACH (open receiver link).
3. Flow      – on server ATTACH, send FLOW (grant link credit).
4. Receive   – on server TRANSFER, parse Bond payload, close socket.
"""

import logging
import struct
import uuid
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ── WebSocket constants ──────────────────────────────────────────────────────
LOBBY_WS_URL = "wss://lobby-hi.svc.halowaypoint.com/"
LOBBY_WS_HOST = "lobby-hi.svc.halowaypoint.com"
WS_TIMEOUT = 30  # seconds
_AMQP_PROTOCOL_HEADER = b"AMQP\x00\x01\x00\x00"

# AMQP source address for the playlist wait-time topic.  Set to the correct
# queue/topic name if the server requires an explicit source address on ATTACH.
# Community reverse-engineering suggests "v1/playlist-waittimes"; adjust if needed.
LOBBY_AMQP_SOURCE: str = "v1/playlist-waittimes"

# ── AMQP 1.0 performative descriptors (server → client) ─────────────────────
_AMQP_DESC_BEGIN = b'\x00\x53\x11'    # server BEGIN    → we send ATTACH
_AMQP_DESC_ATTACH = b'\x00\x53\x12'  # server ATTACH   → we send FLOW
_AMQP_DESC_TRANSFER = b'\x00\x53\x14'  # server TRANSFER → Bond payload
_AMQP_DESC_END = b'\x00\x53\x17'     # server END      → session closed
_AMQP_DESC_CLOSE = b'\x00\x53\x18'   # server CLOSE    → connection closed

# ── Bond CompactBinary wire types ────────────────────────────────────────────
_BT_BOOL = 2
_BT_UINT8 = 3
_BT_UINT16 = 4
_BT_UINT32 = 5
_BT_UINT64 = 6
_BT_FLOAT = 7
_BT_DOUBLE = 8
_BT_STRING = 9
_BT_STRUCT = 10
_BT_LIST = 11
_BT_SET = 12
_BT_MAP = 13
_BT_INT8 = 14
_BT_INT16 = 15
_BT_INT32 = 16
_BT_INT64 = 17
_BT_WSTRING = 18


# ── Bond parser ──────────────────────────────────────────────────────────────

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
        raw = struct.pack('<IIII', d1, d2, d3, d4)
        return str(uuid.UUID(bytes_le=raw))
    except Exception:
        return f"{d1:08x}{d2:08x}{d3:08x}{d4:08x}"


def _extract_bond_data_from_frame(frame: bytes) -> Optional[bytes]:
    """Locate Bond payload within a raw AMQP TRANSFER frame.

    Searches for the AMQP data-section descriptor (0x00 0x53 0x75) and returns
    the enclosed binary content.  If the descriptor is not found the raw frame
    is returned as-is so the caller can still attempt parsing.
    """
    _DATA_SECTION = b'\x00\x53\x75'
    idx = frame.find(_DATA_SECTION)
    if idx != -1:
        pos = idx + 3
        if pos < len(frame):
            prefix = frame[pos]; pos += 1
            if prefix == 0xa0 and pos < len(frame):      # vbin8
                length = frame[pos]; pos += 1
                return frame[pos:pos + length]
            if prefix == 0xb0 and pos + 4 <= len(frame):  # vbin32
                length = struct.unpack_from('>I', frame, pos)[0]
                pos += 4
                return frame[pos:pos + length]
    return frame if len(frame) > 8 else None


def parse_playlist_bond(bond_bytes: bytes) -> list[dict]:
    """Parse a PlaylistResponse from Bond CompactBinary v2.

    Schema (field ordinals):
      PlaylistResponse   [51] → List<List<PlaylistContainer>>
      PlaylistContainer   [2] → double  WaitTime  (seconds)
                          [3] → PlaylistInformation
      PlaylistInformation [1] → GUID  AssetId
                          [2] → GUID  VersionId
      GUID               [0..3] → uint32 Data1..Data4

    Returns a list of dicts with keys 'wait_time_ms', 'asset_id', 'version_id'.
    """
    results: list[dict] = []
    try:
        top, _ = _parse_bond_struct_v2(bond_bytes, 0)
        containers = top.get(51)
        if not isinstance(containers, list):
            logger.debug("Field 51 not found or is not a list in Bond response")
            return results

        for container_list in containers:
            if not isinstance(container_list, list):
                continue
            for entry in container_list:
                if not isinstance(entry, dict):
                    continue
                wait_time_s = entry.get(2, 0.0)
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


# ── AMQP 1.0 frame builders ──────────────────────────────────────────────────

def _make_amqp_frame(channel: int, payload: bytes) -> bytes:
    """Wrap *payload* in an AMQP 1.0 frame (8-byte header)."""
    return struct.pack('>IBBH', 8 + len(payload), 2, 0x00, channel) + payload


def _encode_str_amqp(s: str) -> bytes:
    b = s.encode()
    return (b'\xa1' + bytes([len(b)]) + b) if len(b) < 256 else (b'\xb1' + struct.pack('>I', len(b)) + b)


def _classify_amqp_frame(data: bytes) -> Optional[bytes]:
    """Return the 3-byte AMQP performative descriptor found in *data*, or None."""
    if len(data) < 11:
        return None
    doff = data[4]
    body_start = doff * 4
    if body_start + 3 > len(data):
        return None
    if data[body_start] == 0x00 and data[body_start + 1] == 0x53:
        return bytes(data[body_start:body_start + 3])
    return None


def _encode_amqp_open(container_id: str = "halobotti") -> bytes:
    """Encode an AMQP OPEN performative (descriptor 0x10).

    Includes the required *hostname* field so the server can route the
    connection to the correct virtual container.
    """
    items = (_encode_str_amqp(container_id)
             + _encode_str_amqp(LOBBY_WS_HOST)  # hostname (field 1) – required for routing
             + b'\x70' + struct.pack('>I', 65536))  # max-frame-size (field 2)
    list_body = struct.pack('>I', 3) + items
    list_enc = b'\xd0' + struct.pack('>I', len(list_body)) + list_body
    return _make_amqp_frame(0, b'\x00\x53\x10' + list_enc)


def _encode_amqp_begin() -> bytes:
    """Encode an AMQP BEGIN performative (descriptor 0x11) for channel 0."""
    items = (b'\x40'
             + b'\x43'
             + b'\x70' + struct.pack('>I', 0x7FFFFFFF)
             + b'\x70' + struct.pack('>I', 0x7FFFFFFF))
    list_body = struct.pack('>I', 4) + items
    list_enc = b'\xd0' + struct.pack('>I', len(list_body)) + list_body
    return _make_amqp_frame(0, b'\x00\x53\x11' + list_enc)


def _encode_amqp_attach(name: str = "halobotti-wt") -> bytes:
    """Encode an AMQP ATTACH performative (descriptor 0x12) for a receiver link.

    Sets the *source* address to :data:`LOBBY_AMQP_SOURCE` so the server knows
    which queue/topic to read from.  Update that constant if the address changes.
    """
    # Source is a composite type: descriptor 0x00 0x53 0x28, then a list with the
    # address string as the first (and only required) field.
    src_addr = _encode_str_amqp(LOBBY_AMQP_SOURCE)
    src_list_body = struct.pack('>I', 1) + src_addr
    src_list_enc = b'\xd0' + struct.pack('>I', len(src_list_body)) + src_list_body
    source = b'\x00\x53\x28' + src_list_enc

    items = (_encode_str_amqp(name)
             + b'\x43'      # handle = uint(0)
             + b'\x41'      # role   = true (receiver)
             + b'\x50\x02'  # snd-settle-mode = mixed
             + b'\x50\x00'  # rcv-settle-mode = first
             + source       # source with address
             + b'\x40')     # target = null
    list_body = struct.pack('>I', 7) + items
    list_enc = b'\xd0' + struct.pack('>I', len(list_body)) + list_body
    return _make_amqp_frame(0, b'\x00\x53\x12' + list_enc)


def _encode_amqp_flow(link_credit: int = 1) -> bytes:
    """Encode an AMQP FLOW performative (descriptor 0x13) to request messages."""
    items = (b'\x43'
             + b'\x70' + struct.pack('>I', 0x7FFFFFFF)
             + b'\x43'
             + b'\x43'
             + b'\x43'
             + b'\x43'
             + b'\x52' + bytes([link_credit]))
    list_body = struct.pack('>I', 7) + items
    list_enc = b'\xd0' + struct.pack('>I', len(list_body)) + list_body
    return _make_amqp_frame(0, b'\x00\x53\x13' + list_enc)


# ── Public API ───────────────────────────────────────────────────────────────

async def fetch_raw_playlist_entries(spartan_token: str, clearance_token: str) -> list[dict]:
    """Connect to the Halo Infinite lobby WebSocket and return raw playlist entries.

    Implements the full AMQPWSB10 state machine:
    1. Bootstrap – send AMQP header + OPEN + BEGIN.
    2. Attach    – on server BEGIN, send ATTACH.
    3. Flow      – on server ATTACH, send FLOW.
    4. Receive   – on server TRANSFER, parse Bond payload, close socket.

    Args:
        spartan_token:   Spartan V4 JWT (must include ``v4=`` prefix).
        clearance_token: 343 clearance token.

    Returns:
        List of dicts with keys ``asset_id``, ``version_id``, ``wait_time_ms``.
        Returns an empty list on any failure.
    """
    if not spartan_token.startswith("v4="):
        spartan_token = f"v4={spartan_token}"

    headers = {
        "Accept": "application/x-bond-compact-binary",
        "Accept-Language": "en-US",
        "User-Agent": "SHIVA-2043073184/6.10025.12948.0 (release; PC)",
        "343-Telemetry-Session-Id": str(uuid.uuid4()),
        "X-343-Authorization-Spartan": spartan_token,
        "343-clearance": clearance_token,
    }

    playlist_entries: list[dict] = []
    logger.debug("Connecting to lobby WebSocket at %s", LOBBY_WS_URL)

    try:
        import asyncio
        async with asyncio.timeout(WS_TIMEOUT):
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    LOBBY_WS_URL,
                    protocols=["AMQPWSB10"],
                    headers=headers,
                ) as ws:
                    logger.debug("Sending AMQP bootstrap (header + OPEN + BEGIN)")
                    await ws.send_bytes(_AMQP_PROTOCOL_HEADER)
                    await ws.send_bytes(_encode_amqp_open())
                    await ws.send_bytes(_encode_amqp_begin())

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            data: bytes = msg.data
                            logger.debug("Received binary frame: %d bytes", len(data))

                            if data[:4] == b"AMQP":
                                logger.debug("Skipping AMQP protocol header echo")
                                continue

                            frame_class = _classify_amqp_frame(data)

                            if frame_class == _AMQP_DESC_BEGIN:
                                logger.info("Server BEGIN received; sending ATTACH")
                                await ws.send_bytes(_encode_amqp_attach())

                            elif frame_class == _AMQP_DESC_ATTACH:
                                logger.info("Server ATTACH received; sending FLOW")
                                await ws.send_bytes(_encode_amqp_flow())

                            elif frame_class == _AMQP_DESC_TRANSFER:
                                logger.debug("TRANSFER received; extracting Bond payload")
                                bond_bytes = _extract_bond_data_from_frame(data)
                                if bond_bytes is not None:
                                    entries = parse_playlist_bond(bond_bytes)
                                    if entries:
                                        logger.info(
                                            "Parsed %d playlist entries from Bond message",
                                            len(entries),
                                        )
                                        playlist_entries.extend(entries)
                                        try:
                                            await ws.close()
                                        except Exception:
                                            pass
                                        break

                            elif frame_class == _AMQP_DESC_END:
                                logger.warning(
                                    "Server sent AMQP END (session closed); "
                                    "frame: %s", data[:32].hex()
                                )
                                break

                            elif frame_class == _AMQP_DESC_CLOSE:
                                logger.warning(
                                    "Server sent AMQP CLOSE (connection closed); "
                                    "frame: %s", data[:32].hex()
                                )
                                break

                            elif frame_class is not None:
                                logger.debug(
                                    "Unhandled AMQP performative %s", frame_class.hex()
                                )

                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error("WebSocket error: %s", ws.exception())
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            logger.info("WebSocket closed by server")
                            break

    except TimeoutError:
        logger.error("WebSocket connection timed out after %ds", WS_TIMEOUT)
    except aiohttp.ClientConnectorError as exc:
        logger.error("Cannot connect to lobby WebSocket: %s", exc)
    except aiohttp.WSServerHandshakeError as exc:
        logger.error("WebSocket handshake failed (status %s): %s", exc.status, exc)
    except Exception as exc:
        logger.exception("Unexpected error fetching playlist wait times: %s", exc)

    return playlist_entries
