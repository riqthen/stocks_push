"""Collect A-share turnover and send an ntfy notification from GitHub Actions."""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

TZ = ZoneInfo("Asia/Shanghai")
DATA_DIR = Path(__file__).resolve().parent / "data"
INDEX_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
BEIJING_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
SLOTS = ("09:30", "09:45", "10:00", "10:15", "10:30", "11:00", "11:30",
         "13:00", "13:30", "14:00", "14:15", "14:30", "14:45", "15:00")
MAX_DELAY = timedelta(minutes=10)


def get_json(url, params):
    request = Request(url + "?" + urlencode(params), headers=HEADERS)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid turnover: {value!r}")
    return value


def get_turnover(now, closing=False):
    indexes = get_json(INDEX_URL, {"secids": "1.000002,0.399107", "fields": "f12,f14,f6,f124"})
    rows = indexes["data"]["diff"]
    by_code = {row["f12"]: row for row in rows}
    if set(by_code) != {"000002", "399107"}:
        raise ValueError("Missing or unexpected A-share indices")
    values = {}
    for code, label in (("000002", "Shanghai"), ("399107", "Shenzhen")):
        row = by_code[code]
        stamp = datetime.fromtimestamp(int(row["f124"]), TZ)
        if closing:
            valid = stamp.date() == now.date() and stamp.hour >= 15 and stamp <= datetime.now(TZ) + timedelta(minutes=2)
        else:
            valid = stamp.date() == now.date() and now - stamp <= MAX_DELAY and stamp <= now + timedelta(minutes=2)
        if not valid:
            raise ValueError(f"Stale {label} quote: {stamp.isoformat()}")
        values[label] = amount(row["f6"])

    common = {"pz": 100, "po": 1, "np": 1, "ut": "bd1d9ddb04089700cf9c27f6f7426281",
              "fltt": 2, "invt": 2, "fid": "f12", "fs": "m:0 t:81 s:2048", "fields": "f12,f6"}
    seen = set()
    total_amount = 0
    expected = None
    for page in range(1, 21):
        payload = get_json(BEIJING_URL, {**common, "pn": page})
        data = payload["data"]
        rows = data["diff"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("Missing Beijing stocks")
        if expected is None:
            expected = int(data["total"])
            if expected < 50 or expected > 2000:
                raise ValueError(f"Unexpected Beijing stock count: {expected}")
        elif int(data["total"]) != expected:
            raise ValueError("Beijing stock count changed during pagination")
        for row in rows:
            code = row["f12"]
            if code in seen or not code.startswith(("4", "8", "92")):
                raise ValueError(f"Duplicate or unexpected Beijing stock: {code}")
            seen.add(code)
            # Suspended shares have no trades and may report '-' instead of zero.
            total_amount += 0 if row["f6"] == "-" else amount(row["f6"])
        if len(seen) == expected:
            values["Beijing"] = total_amount
            return values
        if len(seen) > expected:
            raise ValueError("Too many Beijing stocks")
    raise ValueError("Incomplete Beijing stock pages")


def current_slot(now):
    for slot in SLOTS:
        scheduled = datetime.combine(now.date(), datetime.strptime(slot, "%H:%M").time(), TZ)
        if scheduled <= now <= scheduled + MAX_DELAY:
            return slot
    return None


def previous_session(calendar, date):
    session = calendar.date_to_session(date, direction="previous")
    return calendar.previous_session(session).date().isoformat()


def collect(now, backfill=False):
    calendar = xcals.get_calendar("XSHG")
    if not calendar.is_session(now.date()):
        print("Exchange closed; skipping")
        return None
    slot = current_slot(now)
    if slot is None:
        print("Outside scheduled slot; skipping")
        return None
    path = DATA_DIR / f"{now.date().isoformat()}.json"
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if slot in records:
        print("Slot already collected; skipping")
        return None
    values = get_turnover(now, closing=backfill)
    prior_date = previous_session(calendar, now.date())
    prior_path = DATA_DIR / f"{prior_date}.json"
    prior = json.loads(prior_path.read_text(encoding="utf-8")).get(slot) if prior_path.exists() else None
    collected_at = datetime.now(TZ) if backfill else now
    record = {"time": collected_at.isoformat(timespec="seconds"), "markets": values, "previous_date": prior_date}
    if backfill:
        record["backfill"] = True
    if prior is not None:
        prior_time = datetime.fromisoformat(prior["time"])
        if abs((now - datetime.combine(now.date(), prior_time.timetz().replace(tzinfo=None), TZ)).total_seconds()) <= 300:
            record["previous"] = prior["markets"]
    DATA_DIR.mkdir(exist_ok=True)
    records[slot] = record
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Collected {slot}: {sum(values.values()) / 1e8:.2f} yi yuan")
    return slot


def message(record):
    current = sum(record["markets"].values()) / 1e8
    lines = [f"沪深京 A 股累计成交额：{current:,.2f} 亿元",
             f"沪 {record['markets']['Shanghai']/1e8:,.2f} ｜深 {record['markets']['Shenzhen']/1e8:,.2f} ｜京 {record['markets']['Beijing']/1e8:,.2f} 亿元",
             f"取数时间：{record['time'][11:16]}（北京时间）"]
    if record.get("backfill"):
        lines.append("今日 15:00 收盘档：收盘后补取，非当时保存的快照")
    if "previous" in record:
        previous = sum(record["previous"].values()) / 1e8
        delta = current - previous
        percent = f"（{delta / previous:+.1%}）" if previous else ""
        lines.append(f"上个交易日 {record['previous_date']} 同档：{previous:,.2f} 亿元；差额 {delta:+,.2f} 亿元{percent}")
    else:
        lines.append("上个交易日同档：暂无可比快照")
    lines.append("数据源：东方财富公开行情；可能延迟")
    return "\n".join(lines)


def notify(slot=None):
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic or not all(c.isascii() and (c.isalnum() or c in "_-") for c in topic):
        raise ValueError("Set NTFY_TOPIC to a random URL-safe topic in repository secrets")
    if slot is None:
        title = "A-share notification test"
        body = "测试通知：今日 15:00 沪深京成交额未能从行情源完整取得，暂无可信数据。这条消息仅验证推送通道。"
    else:
        today = datetime.now(TZ).date().isoformat()
        record = json.loads((DATA_DIR / f"{today}.json").read_text(encoding="utf-8"))[slot]
        title = f"A-share turnover {slot}"
        body = message(record)
    request = Request(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                      headers={"Title": title, "Tags": "chart_with_upwards_trend"}, method="POST")
    with urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"ntfy rejected notification: {response.status}")
    print("Notification sent")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("collect", "backfill-close", "notify", "test-notify"))
    parser.add_argument("--slot", choices=SLOTS)
    args = parser.parse_args()
    if args.action in ("collect", "backfill-close"):
        now = datetime.now(TZ)
        backfill = args.action == "backfill-close"
        if backfill:
            close = now.replace(hour=15, minute=2, second=0, microsecond=0)
            if now < close or now.hour >= 23:
                raise ValueError("Backfill is only available after today's market close")
            slot = collect(close, backfill=True)
        else:
            slot = collect(now)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"slot={slot or ''}\n")
    elif args.action == "test-notify":
        notify()
    else:
        if not args.slot:
            parser.error("--slot is required for notify")
        notify(args.slot)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
