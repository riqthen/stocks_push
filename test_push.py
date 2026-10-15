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

    def test_complete_market_turnover(self):
        stamp = int((self.now - timedelta(minutes=1)).timestamp())
        indexes = {"data": {"diff": [
            {"f12": "000002", "f6": 100e8, "f124": stamp},
            {"f12": "399107", "f6": 200e8, "f124": stamp}]}}
        first = {"data": {"total": 101, "diff": [
            {"f12": f"8{i:05d}", "f6": 1e8} for i in range(100)]}}
        last = {"data": {"total": 101, "diff": [{"f12": "920001", "f6": "-"}]}}
        with patch.object(push, "get_json", side_effect=[indexes, first, last]):
            self.assertEqual(push.get_turnover(self.now),
                             {"Shanghai": 100e8, "Shenzhen": 200e8, "Beijing": 100e8})

    def test_reject_incomplete_pages(self):
        stamp = int(self.now.timestamp())
        indexes = {"data": {"diff": [
            {"f12": "000002", "f6": 100, "f124": stamp},
            {"f12": "399107", "f6": 200, "f124": stamp}]}}
        page = {"data": {"total": 101, "diff": [
            {"f12": f"8{i:05d}", "f6": 1} for i in range(100)]}}
        with patch.object(push, "get_json", side_effect=[indexes, page, page]):
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                push.get_turnover(self.now)

    def test_previous_trading_session_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(push, "DATA_DIR", Path(directory)):
            previous = {"10:00": {"time": "2025-06-09T10:03:00+08:00",
                                   "markets": {"Shanghai": 90e8, "Shenzhen": 180e8, "Beijing": 80e8}}}
            (Path(directory) / "2025-06-09.json").write_text(json.dumps(previous), encoding="utf-8")
            with patch.object(push, "get_turnover", return_value={"Shanghai": 100e8,
                                                                      "Shenzhen": 200e8,
                                                                      "Beijing": 100e8}) as fetch:
                self.assertEqual(push.collect(self.now), "10:00")
                self.assertIsNone(push.collect(self.now))
                fetch.assert_called_once()
            saved = json.loads((Path(directory) / "2025-06-10.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["10:00"]["previous"], previous["10:00"]["markets"])
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
        index = {"data": {"diff": [
            {"f12": "000002", "f6": 100, "f124": stamp},
            {"f12": "399107", "f6": 200, "f124": stamp}]}}
        with patch.object(push, "datetime", wraps=datetime) as clock, patch.object(
                push, "get_json", side_effect=[index, {"data": {"total": 50, "diff": [
                    {"f12": f"8{i:05d}", "f6": 1} for i in range(50)]}}]):
            clock.now.return_value = self.now.replace(hour=19)
            close = self.now.replace(hour=15, minute=2)
            self.assertEqual(push.get_turnover(close, closing=True)["Beijing"], 50)


if __name__ == "__main__":
    unittest.main()
