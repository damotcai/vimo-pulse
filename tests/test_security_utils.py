"""Unit tests for security_utils.py (SSRF, injection, path guards, atomic writes, rate limits)."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from security_utils import (
    SlidingWindowRateLimiter,
    atomic_write_json,
    cleanup_old_files,
    validate_ai_model,
    validate_outbound_https_url,
    validate_telegram_creds,
    validate_ticker,
)


class TestInputValidation(unittest.TestCase):
    def test_validate_ticker_valid(self) -> None:
        self.assertEqual(validate_ticker(" vcb "), "VCB")
        self.assertEqual(validate_ticker("VN30"), "VN30")
        self.assertEqual(validate_ticker("vnindex"), "VNINDEX")

    def test_validate_ticker_blocks_path_traversal_and_specials(self) -> None:
        for bad in ("", "A", "../server", "..\\server", "VCB/../X", "VCB%2F..", "TOOLONGTICKER1", "VCB;rm", "VCB.json"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_ticker(bad)

    def test_validate_ai_model(self) -> None:
        self.assertEqual(validate_ai_model("", "gemini-2.5-flash"), "gemini-2.5-flash")
        self.assertEqual(validate_ai_model("gpt-4o-mini", "gpt-4o-mini"), "gpt-4o-mini")
        self.assertEqual(validate_ai_model("meta-llama/llama-3.3-70b:free", "gpt-4o-mini"), "meta-llama/llama-3.3-70b:free")
        for bad in ("../secret", "gemini?key=x", "model name with spaces", "a"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_ai_model(bad, "gemini-2.5-flash")

    def test_validate_telegram_creds(self) -> None:
        tok = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        self.assertEqual(validate_telegram_creds(tok, "-1001234567890"), (tok, "-1001234567890"))
        self.assertEqual(validate_telegram_creds(tok, "@my_channel_01"), (tok, "@my_channel_01"))
        for bad_tok in ("", "not-a-token", "123456:short", "123456789:abc/../../sendMessage"):
            with self.assertRaises(ValueError):
                validate_telegram_creds(bad_tok, "12345678")
        for bad_cid in ("", "12", "bad chat", "../../evil"):
            with self.assertRaises(ValueError):
                validate_telegram_creds(tok, bad_cid)


class TestSSRFProtection(unittest.TestCase):
    def test_blocks_non_https_schemes(self) -> None:
        for url in ("http://api.openai.com/v1", "file:///etc/passwd", "ftp://example.com", "gopher://127.0.0.1:80"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_outbound_https_url(url)

    def test_blocks_private_loopback_and_metadata_targets(self) -> None:
        bad_urls = [
            "https://127.0.0.1/v1",
            "https://localhost/v1",
            "https://169.254.169.254/latest/meta-data",
            "https://10.0.0.1/v1",
            "https://192.168.1.1/v1",
            "https://172.16.0.5/v1",
            "https://[::1]/v1",
            "https://metadata.google.internal/computeMetadata/v1",
            "https://secret.internal/v1",
            "https://user:pass@api.openai.com/v1",
        ]
        for url in bad_urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_outbound_https_url(url)

    def test_blocks_dns_rebinding_to_private_ip(self) -> None:
        fake_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]
        with patch("security_utils.socket.getaddrinfo", return_value=fake_addrinfo):
            with self.assertRaises(ValueError):
                validate_outbound_https_url("https://evil-rebind.example.com/v1")

    def test_allows_public_https_domain(self) -> None:
        fake_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.7.192", 443))]
        with patch("security_utils.socket.getaddrinfo", return_value=fake_addrinfo):
            out = validate_outbound_https_url("https://api.openai.com/v1")
            self.assertEqual(out, "https://api.openai.com/v1")


class TestConcurrencyAndReliability(unittest.TestCase):
    def test_atomic_write_json_concurrent_threads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "state.json"
            errors: list[Exception] = []

            def worker(idx: int) -> None:
                try:
                    for j in range(15):
                        atomic_write_json(target, {"thread": idx, "iter": j, "payload": "x" * 200})
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            parsed = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn("thread", parsed)
            # Ensure no leftover .tmp files
            leftovers = list(Path(td).glob("*.tmp"))
            self.assertEqual(leftovers, [])

    def test_rate_limiter_enforces_and_bounds_memory(self) -> None:
        rl = SlidingWindowRateLimiter(max_keys=4)
        for _ in range(3):
            ok, retry = rl.allow("tg:1.2.3.4", limit=3, window_seconds=60.0)
            self.assertTrue(ok)
            self.assertEqual(retry, 0)
        ok, retry = rl.allow("tg:1.2.3.4", limit=3, window_seconds=60.0)
        self.assertFalse(ok)
        self.assertGreaterEqual(retry, 1)

        # Memory bound check
        for i in range(20):
            rl.allow(f"ip:{i}", limit=5, window_seconds=60.0)
        self.assertLessEqual(len(rl._buckets), 4)

    def test_cleanup_old_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            old_f = p / "old.xlsx"
            new_f = p / "new.xlsx"
            old_f.write_bytes(b"old")
            new_f.write_bytes(b"new")
            old_time = time.time() - 90000
            os.utime(old_f, (old_time, old_time))

            removed = cleanup_old_files(p, max_age_seconds=86400)
            self.assertEqual(removed, 1)
            self.assertFalse(old_f.exists())
            self.assertTrue(new_f.exists())


if __name__ == "__main__":
    unittest.main()
