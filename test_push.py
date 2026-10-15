import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import push


class TurnoverTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2025, 6, 10, 10, 2, tzinfo=push.TZ)

    def test_time_slots(self):
        self.assertEqual(push.current_slot(self.now), "10:00")
        self.assertIsNone(push.current_slot(self.now.replace(hour=12)))
        self.assertIsNone(push.current_slot(self.now.replace(minute=11)))

    def quotes(self, stamp, duplicate=False):
        indexes = {"data": {"diff": [
            {"f12": "000002", "f6": 100e8, "f124": stamp},
            {"f12": "399107", "f6": 200e8, "f124": stamp}]}}

        def fetch(url, params):
            if url == push.INDEX_URL:
                return indexes
            market = params["fs"]
            total = 50 if "t:81" in market else 501 if duplicate and "m:1" in market else 500
            offset = (params["pn"] - 1) * 100
            rows = []
            for i in range(offset, min(offset + 100, total)):
                number = i if not duplicate or offset == 0 else i - 100
                code = f"8{number:05d}" if "t:81" in market else f"{number:06d}"
                value = "-" if "m:1" in market and i == 1 else 1e8
                change = 0 if "m:1" in market and i == 0 else 1 if i % 2 == 0 else -1
                rows.append({"f12": code, "f6": value, "f3": change})
            return {"data": {"total": total, "diff": rows}}

        return fetch

    def test_complete_market_turnover_and_breadth(self):
        stamp = int((self.now - timedelta(minutes=1)).timestamp())
        with patch.object(push, "get_json", side_effect=self.quotes(stamp)):
            values, breadth = push.get_turnover(self.now)
        self.assertEqual(values, {"Shanghai": 100e8, "Shenzhen": 200e8, "Beijing": 50e8})
        self.assertEqual(breadth, {"up": 526, "down": 524})
        self.assertIn("涨跌家数 526:524", push.message({"time": self.now.isoformat(),
                                                         "markets": values, "breadth": breadth,
                                                         "previous_date": "2025-06-09"}))

    def test_reject_incomplete_pages(self):
        stamp = int(self.now.timestamp())
        with patch.object(push, "get_json", side_effect=self.quotes(stamp, duplicate=True)):
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                push.get_turnover(self.now)
    def test_nontraded_stock_with_missing_change_counts_as_up(self):
        stamp = int(self.now.timestamp())
        fetch = self.quotes(stamp)

        def missing_change(url, params):
            payload = fetch(url, params)
            if url == push.BEIJING_URL and "m:1" in params["fs"] and params["pn"] == 1:
                payload["data"]["diff"][1]["f3"] = "-"
            return payload

        with patch.object(push, "get_json", side_effect=missing_change):
            _, breadth = push.get_turnover(self.now)
        self.assertEqual(breadth, {"up": 526, "down": 524})
    def test_reject_invalid_change_for_traded_stock(self):
        stamp = int(self.now.timestamp())
        fetch = self.quotes(stamp)

        def invalid(url, params):
            payload = fetch(url, params)
            if url == push.BEIJING_URL and "m:1" in params["fs"] and params["pn"] == 1:
                payload["data"]["diff"][2]["f3"] = "-"
            return payload

        with patch.object(push, "get_json", side_effect=invalid):
            with self.assertRaisesRegex(ValueError, "Invalid Shanghai stock change"):
                push.get_turnover(self.now)
    def test_previous_trading_session_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(push, "DATA_DIR", Path(directory)):
            previous = {"10:00": {"time": "2025-06-09T10:03:00+08:00",
                                   "markets": {"Shanghai": 90e8, "Shenzhen": 180e8, "Beijing": 80e8}}}
            (Path(directory) / "2025-06-09.json").write_text(json.dumps(previous), encoding="utf-8")
            with patch.object(push, "get_turnover", return_value=(
                    {"Shanghai": 100e8, "Shenzhen": 200e8, "Beijing": 100e8},
                    {"up": 3000, "down": 2000})) as fetch:
                self.assertEqual(push.collect(self.now), "10:00")
                self.assertIsNone(push.collect(self.now))
                fetch.assert_called_once()
            saved = json.loads((Path(directory) / "2025-06-10.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["10:00"]["previous"], previous["10:00"]["markets"])
            self.assertEqual(saved["10:00"]["breadth"], {"up": 3000, "down": 2000})
            self.assertIn("涨跌家数 3000:2000", push.message(saved["10:00"]))
            self.assertIn("上个交易日", push.message(saved["10:00"]))

    def test_reject_stale_quote(self):
        stamp = int((self.now - timedelta(minutes=20)).timestamp())
        indexes = {"data": {"diff": [
            {"f12": "000002", "f6": 100, "f124": stamp},
            {"f12": "399107", "f6": 200, "f124": stamp}]}}
        with patch.object(push, "get_json", return_value=indexes):
            with self.assertRaisesRegex(ValueError, "Stale"):
                push.get_turnover(self.now)

    def test_closing_quote_allows_post_close_updates(self):
        stamp = int(self.now.replace(hour=16, minute=12).timestamp())
        with patch.object(push, "datetime", wraps=datetime) as clock, patch.object(
                push, "get_json", side_effect=self.quotes(stamp)):
            clock.now.return_value = self.now.replace(hour=19)
            close = self.now.replace(hour=15, minute=2)
            self.assertEqual(push.get_turnover(close, closing=True)[0]["Beijing"], 50e8)

if __name__ == "__main__":
    unittest.main()
