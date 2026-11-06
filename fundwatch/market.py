"""Market snapshot: indices, Nasdaq, futures and FX."""
import logging

from . import sources
from .analysis import indicators, score_market
from .util import now_cn

log = logging.getLogger("fundwatch.market")

SCORING_CODES = ("sh000001", "sh000300", "sz399006", "sh000688")


def is_trading_day(http, today):
    """A trading day is detected from the Shanghai Composite quote date.

    Sina/Tencent keep the previous trading day's date on holidays, which is
    exactly what we need; the kline date is only a fallback.
    """
    try:
        quote = sources.fetch_index_quotes(
            http, [{"code": "sh000001", "name": "上证指数"}]).get("sh000001")
        if quote and quote.get("date"):
            return quote["date"] == today
    except Exception as exc:  # noqa: BLE001
        log.warning("trading-day quote check failed: %s", exc)
    hist = sources.fetch_kline(http, "sh000001", 5)
    if hist:
        return hist[-1]["date"] == today
    return now_cn().weekday() < 5


def build_market(http, cfg, today):
    quotes = {}
    try:
        quotes = sources.fetch_index_quotes(http, cfg["indices"])
    except Exception as exc:  # noqa: BLE001 - never fail the whole report
        log.warning("index quotes failed: %s", exc)
    rows = []
    for item in cfg["indices"]:
        code = item["code"]
        quote = quotes.get(code)
        hist = sources.fetch_kline(http, code, 260)
        closes = [row["close"] for row in hist if row.get("close")]
        if quote and quote.get("price"):
            if not hist or hist[-1]["date"] != today:
                closes.append(quote["price"])
        ind = indicators(closes) if closes else {}
        row = {
            "code": code,
            "name": item.get("name") or (quote or {}).get("name") or code,
            "price": (quote or {}).get("price") or (closes[-1] if closes else None),
            "change_pct": (quote or {}).get("change_pct"),
            "quote_date": (quote or {}).get("date", ""),
            "quote_time": (quote or {}).get("time", ""),
            "source": (quote or {}).get("source", "kline"),
        }
        row.update(ind)
        rows.append(row)

    scoring_rows = [row for row in rows if row["code"] in SCORING_CODES and row.get("ma20")]
    market_score, regime = score_market(scoring_rows)

    ndx = None
    try:
        ndx = sources.fetch_ndx(http)
    except Exception as exc:  # noqa: BLE001
        log.warning("ndx failed: %s", exc)
    nq = None
    try:
        nq = sources.fetch_nq_futures(http)
    except Exception as exc:  # noqa: BLE001
        log.warning("nq futures failed: %s", exc)
    fx = None
    try:
        fx = sources.fetch_usdcny(http)
    except Exception as exc:  # noqa: BLE001
        log.warning("fx failed: %s", exc)

    return {
        "date": today,
        "as_of": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "indices": rows,
        "score": market_score,
        "regime": regime,
        "ndx": ndx,
        "nq": nq,
        "fx": fx,
    }
