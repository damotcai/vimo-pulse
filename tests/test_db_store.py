"""Unit tests for SQLite WAL incremental database store & smart per-source sync protection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import macro_fetcher
from db_store import MacroDatabase


class TestSQLiteIncrementalStore(unittest.TestCase):
    def test_upsert_series_points_merges_incrementally_without_losing_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = MacroDatabase(Path(td) / "test.db")
            initial = [
                {"date": "2026-07", "index": 330.0, "yoy": 3.1, "mom": 0.2},
                {"date": "2026-08", "index": 332.0, "yoy": 3.3, "mom": 0.3},
                {"date": "2026-09", "index": 335.0, "yoy": 3.5, "mom": 0.3},
            ]
            merged1, added1 = db.upsert_series_points("bls:core_cpi", initial)
            self.assertEqual(len(merged1), 3)
            self.assertEqual(added1, 3)

            # Re-sending identical data should add 0 new points
            merged2, added2 = db.upsert_series_points("bls:core_cpi", initial)
            self.assertEqual(len(merged2), 3)
            self.assertEqual(added2, 0)

            # Partial incoming payload with only 1 new month must PRESERVE all 3 earlier months
            partial_new = [{"date": "2026-10", "index": 339.9, "yoy": 3.7, "mom": 0.3}]
            merged3, added3 = db.upsert_series_points("bls:core_cpi", partial_new)
            self.assertEqual(len(merged3), 4)
            self.assertEqual(added3, 1)
            self.assertEqual([p["date"] for p in merged3], ["2026-07", "2026-08", "2026-09", "2026-10"])

    def test_non_empty_validator_rejects_broken_or_empty_payloads(self) -> None:
        self.assertFalse(macro_fetcher._is_valid_source_data("bls", {}))
        self.assertFalse(macro_fetcher._is_valid_source_data("bls", {"cpi": [], "core_cpi": [], "unemployment": []}))
        self.assertFalse(macro_fetcher._is_valid_source_data("bea", {"pce": [], "core_pce": []}))
        self.assertFalse(macro_fetcher._is_valid_source_data("vn_market", {"indices": {"VNINDEX": {"series": []}}}))
        self.assertFalse(macro_fetcher._is_valid_source_data("news", []))

        good_bls = {
            "cpi": [{"date": f"2025-{m:02d}", "yoy": 3.0} for m in range(1, 13)],
            "core_cpi": [{"date": f"2025-{m:02d}", "yoy": 3.1} for m in range(1, 13)],
            "unemployment": [{"date": f"2025-{m:02d}", "value": 4.1} for m in range(1, 13)],
        }
        self.assertTrue(macro_fetcher._is_valid_source_data("bls", good_bls))

    def test_smart_sync_skips_fresh_db_and_never_overwrites_with_empty_or_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            test_db = MacroDatabase(Path(td) / "smart.db")
            good_bls = {
                "cpi": [{"date": f"2025-{m:02d}", "index": 300 + m, "yoy": 3.0, "mom": 0.2} for m in range(1, 13)],
                "core_cpi": [{"date": f"2025-{m:02d}", "index": 310 + m, "yoy": 3.1, "mom": 0.2} for m in range(1, 13)],
                "ppi": [],
                "unemployment": [{"date": f"2025-{m:02d}", "value": 4.1} for m in range(1, 13)],
                "nfp": [],
            }
            calls = {"n": 0}

            def good_fetch():
                calls["n"] += 1
                return good_bls

            with patch.object(macro_fetcher, "macro_db", test_db), \
                 patch.object(macro_fetcher, "_source_cache", {}), \
                 patch.object(macro_fetcher, "_db_bootstrapped", True):
                # 1. Missing in DB -> repairs immediately
                data1, info1 = macro_fetcher._get_cached_or_fetch_smart("bls", good_fetch, mode="smart")
                self.assertEqual(info1["action"], "repaired")
                self.assertEqual(len(data1["core_cpi"]), 12)
                self.assertEqual(calls["n"], 1)

                # 2. Second smart call -> served from SQLite DB with ZERO upstream calls!
                data2, info2 = macro_fetcher._get_cached_or_fetch_smart("bls", good_fetch, mode="smart", user_check=True)
                self.assertEqual(info2["action"], "db_cached")
                self.assertEqual(calls["n"], 1)

                # 3. Forced refresh hits empty response {} -> MUST PRESERVE valid SQLite DB data!
                data3, info3 = macro_fetcher._get_cached_or_fetch_smart("bls", lambda: {}, mode="smart", force=True)
                self.assertEqual(info3["action"], "db_preserved_on_err")
                self.assertEqual(len(data3["core_cpi"]), 12)

                # 4. Forced refresh raises RuntimeError -> MUST STILL PRESERVE valid SQLite DB data!
                def failing_fetch():
                    raise RuntimeError("BLS daily quota exceeded")

                data4, info4 = macro_fetcher._get_cached_or_fetch_smart("bls", failing_fetch, mode="smart", force=True)
                self.assertEqual(info4["action"], "db_preserved_on_err")
                self.assertEqual(len(data4["core_cpi"]), 12)

    def test_db_stats_reports_wal_and_points(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = MacroDatabase(Path(td) / "stats.db")
            db.save_source_snapshot("vcb", {"usd": {"sell": 26030}}, latest_date="2026-11-28", record_count=1)
            db.upsert_series_points("bea:core_pce", [{"date": "2026-10", "yoy": 3.26}])
            stats = db.get_db_stats()
            self.assertEqual(stats["engine"], "SQLite WAL")
            self.assertEqual(stats["sources_count"], 1)
            self.assertEqual(stats["total_series_points"], 1)


if __name__ == "__main__":
    unittest.main()
