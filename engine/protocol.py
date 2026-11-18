"""TradingView WebSocket Protocol Parser & Encoder.

Handles length-delimited framing (~m~<len>~m~<payload>), heartbeat echoes (~h~<id>),
corrupted byte resynchronization, and stateful streaming reassembly.
"""

import re
import json
import logging
from typing import List, Dict, Any, Optional, Tuple, Union

logger = logging.getLogger("tradingview.protocol")

_PACKET_PATTERN = re.compile(r"~m~(\d+)~m~")


def parse_tradingview_packets(raw_data: str) -> List[Dict[str, Any]]:
    """
    Stateless parser for TradingView length-delimited packets.
    Extracts packets formatted as `~m~<len>~m~<payload>`.
    Includes forward-scanning resynchronization for noisy or corrupted framing.
    """
    packets, _ = parse_tradingview_packets_with_remainder(raw_data)
    return packets


def parse_tradingview_packets_with_remainder(raw_data: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Parses complete packets and returns (completed_packets, unconsumed_buffer).
    Used directly or via StreamingTradingViewParser for partial chunk reassembly.
    """
    packets: List[Dict[str, Any]] = []
    cursor = 0
    total_len = len(raw_data)

    while cursor < total_len:
        match = _PACKET_PATTERN.match(raw_data, cursor)
        if not match:
            # Check if this might be an incomplete delimiter at the tail of the buffer
            tail = raw_data[cursor:]
            if tail.startswith("~") and ("~m~" not in tail or tail.count("~m~") < 2):
                # Incomplete delimiter header; leave in buffer for next chunk
                break

            # Resynchronize by scanning forward for the next '~m~'
            next_pos = raw_data.find("~m~", cursor + 1)
            if next_pos == -1:
                # No more packet headers in buffer
                cursor = total_len
                break
            logger.debug("Protocol parser skipped corrupted segment of %d chars", next_pos - cursor)
            cursor = next_pos
            continue

        try:
            expected_length = int(match.group(1))
        except ValueError:
            cursor = match.end()
            continue

        start = match.end()
        end = start + expected_length
        if end > total_len:
            # Incomplete payload; preserve in buffer for subsequent network chunks
            break

        payload = raw_data[start:end]
        cursor = end

        if payload.startswith("~h~"):
            packets.append({
                "type": "heartbeat",
                "raw": payload,
                "data": payload
            })
        else:
            try:
                parsed_json = json.loads(payload)
                packets.append({
                    "type": "data",
                    "raw": payload,
                    "data": parsed_json
                })
            except json.JSONDecodeError as exc:
                logger.debug("Discarding unparseable JSON payload: %s", exc)

    unconsumed = raw_data[cursor:]
    return packets, unconsumed


class StreamingTradingViewParser:
    """
    Stateful streaming parser buffer.
    Maintains unconsumed partial chunks across network frames.
    """

    def __init__(self) -> None:
        self._buffer: str = ""

    def feed(self, chunk: str) -> List[Dict[str, Any]]:
        self._buffer += chunk
        packets, self._buffer = parse_tradingview_packets_with_remainder(self._buffer)
        return packets

    def clear(self) -> None:
        self._buffer = ""

    @property
    def buffered_length(self) -> int:
        return len(self._buffer)


def encode_tradingview_message(
    name_or_msg: Union[str, Dict[str, Any]],
    params: Optional[list] = None
) -> str:
    """
    Encodes an outgoing message into TradingView's framing: `~m~<len>~m~<payload>`.
    Supports:
      1. encode_tradingview_message("set_auth_token", ["token"])
      2. encode_tradingview_message('{"m":"set_auth_token","p":["token"]}')
      3. encode_tradingview_message({"m": "...", "p": [...]})
    """
    if params is not None:
        payload = json.dumps({"m": name_or_msg, "p": params}, separators=(',', ':'))
    elif isinstance(name_or_msg, dict):
        payload = json.dumps(name_or_msg, separators=(',', ':'))
    else:
        payload = str(name_or_msg)

    return f"~m~{len(payload)}~m~{payload}"


def encode_heartbeat_response(heartbeat_str: str) -> str:
    """
    Formats a heartbeat response: `~m~<len>~m~~h~<id>`.
    """
    return f"~m~{len(heartbeat_str)}~m~{heartbeat_str}"
