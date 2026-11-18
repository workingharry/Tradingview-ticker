"""Unit tests for TradingView WebSocket protocol parser and encoder."""

import unittest
import json
from engine.protocol import (
    parse_tradingview_packets,
    parse_tradingview_packets_with_remainder,
    StreamingTradingViewParser,
    encode_tradingview_message,
    encode_heartbeat_response,
)


class TestTradingViewProtocol(unittest.TestCase):
    def test_parse_single_data_packet(self):
        payload = json.dumps({"m": "qsd", "p": ["qs_1", {"n": "BINANCE:BTCUSDT", "v": {"lp": 65000.5}}]})
        raw = f"~m~{len(payload)}~m~{payload}"
        packets = parse_tradingview_packets(raw)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["type"], "data")
        self.assertEqual(packets[0]["data"]["m"], "qsd")
        self.assertEqual(packets[0]["data"]["p"][1]["v"]["lp"], 65000.5)

    def test_parse_heartbeat_packet(self):
        raw = "~m~4~m~~h~1"
        packets = parse_tradingview_packets(raw)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["type"], "heartbeat")
        self.assertEqual(packets[0]["data"], "~h~1")

    def test_parse_payload_containing_tilde_m_substring(self):
        # Adversarial test: JSON payload has the string "~m~" inside text values
        payload = json.dumps({"m": "qsd", "p": ["qs_1", {"n": "CUSTOM~m~TICKER", "v": {"lp": 123.45}}]})
        raw = f"~m~{len(payload)}~m~{payload}"
        packets = parse_tradingview_packets(raw)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["type"], "data")
        self.assertEqual(packets[0]["data"]["p"][1]["n"], "CUSTOM~m~TICKER")

    def test_parse_multiple_packets_in_single_buffer(self):
        p1 = json.dumps({"m": "quote_completed", "p": ["qs_1"]})
        p2 = "~h~2"
        p3 = json.dumps({"m": "qsd", "p": ["qs_1", {"n": "NASDAQ:AAPL", "v": {"lp": 220.0}}]})
        raw = f"~m~{len(p1)}~m~{p1}~m~{len(p2)}~m~{p2}~m~{len(p3)}~m~{p3}"
        packets = parse_tradingview_packets(raw)
        self.assertEqual(len(packets), 3)
        self.assertEqual(packets[0]["type"], "data")
        self.assertEqual(packets[1]["type"], "heartbeat")
        self.assertEqual(packets[2]["type"], "data")
        self.assertEqual(packets[2]["data"]["p"][1]["n"], "NASDAQ:AAPL")

    def test_unicode_kanji_character_count_vs_bytes(self):
        # Japanese Kanji: 6 characters = 18 UTF-8 bytes.
        # TradingView length headers represent character counts (len(str)), not byte counts.
        raw_payload = '{"m":"qsd","p":["qs_1",{"n":"TSE:7203","v":{"lp":3429.0,"desc":"トヨタ自動車"}}]}'
        char_len = len(raw_payload)
        byte_len = len(raw_payload.encode("utf-8"))
        self.assertNotEqual(char_len, byte_len)

        raw_frame = f"~m~{char_len}~m~{raw_payload}"
        packets = parse_tradingview_packets(raw_frame)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["type"], "data")
        self.assertEqual(packets[0]["data"]["p"][1]["v"]["desc"], "トヨタ自動車")

    def test_corrupted_prefix_resynchronization(self):
        # Corrupted noise preceding and between valid packet headers
        noisy_frame = "garbage_noise_12345~m~4~m~~h~1junk_bytes_xyz~m~4~m~~h~2tail_junk"
        packets = parse_tradingview_packets(noisy_frame)
        self.assertEqual(len(packets), 2)
        self.assertEqual(packets[0]["data"], "~h~1")
        self.assertEqual(packets[1]["data"], "~h~2")

    def test_incomplete_delimiter_tail_preserved(self):
        raw = "~m~4~m~~h~1~m~"
        packets, remainder = parse_tradingview_packets_with_remainder(raw)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["data"], "~h~1")
        self.assertEqual(remainder, "~m~")

    def test_incomplete_payload_tail_preserved(self):
        raw = "~m~20~m~short_payload"
        packets, remainder = parse_tradingview_packets_with_remainder(raw)
        self.assertEqual(len(packets), 0)
        self.assertEqual(remainder, raw)

    def test_streaming_parser_split_chunks_fuzzing(self):
        # Test that splitting a multi-packet sequence across every possible boundary
        # recovers the exact same packets without loss or corruption.
        p1 = json.dumps({"m": "set_auth_token", "p": ["test_token"]})
        p2 = "~h~42"
        p3 = json.dumps({"m": "qsd", "p": ["qs_1", {"n": "BINANCE:ETHUSDT", "v": {"lp": 3200.0}}]})
        combined = f"~m~{len(p1)}~m~{p1}~m~{len(p2)}~m~{p2}~m~{len(p3)}~m~{p3}"

        for split_pos in range(1, len(combined)):
            chunk1 = combined[:split_pos]
            chunk2 = combined[split_pos:]

            parser = StreamingTradingViewParser()
            packets1 = parser.feed(chunk1)
            packets2 = parser.feed(chunk2)
            total_packets = packets1 + packets2

            self.assertEqual(len(total_packets), 3, f"Failed at split position {split_pos}")
            self.assertEqual(total_packets[0]["type"], "data")
            self.assertEqual(total_packets[1]["type"], "heartbeat")
            self.assertEqual(total_packets[1]["data"], "~h~42")
            self.assertEqual(total_packets[2]["type"], "data")
            self.assertEqual(total_packets[2]["data"]["p"][1]["n"], "BINANCE:ETHUSDT")
            self.assertEqual(parser.buffered_length, 0)

    def test_encode_tradingview_message(self):
        # 1. Name and params
        encoded1 = encode_tradingview_message("set_auth_token", ["unauthorized_user_token"])
        self.assertTrue(encoded1.startswith("~m~"))
        packets1 = parse_tradingview_packets(encoded1)
        self.assertEqual(len(packets1), 1)
        self.assertEqual(packets1[0]["data"]["m"], "set_auth_token")

        # 2. Dict object
        encoded2 = encode_tradingview_message({"m": "quote_fast_symbols", "p": ["qs_1", "BINANCE:BTCUSDT"]})
        packets2 = parse_tradingview_packets(encoded2)
        self.assertEqual(len(packets2), 1)
        self.assertEqual(packets2[0]["data"]["m"], "quote_fast_symbols")

        # 3. Raw string
        encoded3 = encode_tradingview_message("~h~99")
        packets3 = parse_tradingview_packets(encoded3)
        self.assertEqual(len(packets3), 1)
        self.assertEqual(packets3[0]["data"], "~h~99")

    def test_encode_heartbeat_response(self):
        pong = encode_heartbeat_response("~h~42")
        self.assertEqual(pong, "~m~5~m~~h~42")
        packets = parse_tradingview_packets(pong)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["type"], "heartbeat")
        self.assertEqual(packets[0]["data"], "~h~42")


if __name__ == "__main__":
    unittest.main()
