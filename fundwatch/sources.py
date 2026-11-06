"""Market data source adapters: Sina, Tencent and Eastmoney.

Every adapter is defensive: it returns None / empty list on failure so the
caller can fall back to another source.  Parsing helpers are pure functions and
are covered by tests/fixtures.
"""
import json
import logging
import re
import time

from .http import HttpError

log = logging.getLogger("fundwatch.sources")

SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}
TENCENT_HEADERS = {"Referer": "https://gu.qq.com/"}
EM_HEADERS = {"Referer": "https://fund.eastmoney.com/"}

SINA_QUOTE_RE = re.compile(r'var hq_str_([a-zA-Z0-9_]+)="([^"]*)"')


# --------------------------------------------------------------------------- parsers
def parse_sina_quotes(body):
    out = {}
    for name, payload in SINA_QUOTE_RE.findall(body or ""):
        if payload:
            out[name] = payload.split(",")
    return out


def sina_index_row(code, fields):
    if len(fields) < 6:
        return None
    try:
        price = float(fields[3])
        prev = float(fields[2])
    except (TypeError, ValueError):
        return None
    if price <= 0 or prev <= 0:
        return None
    return {
        "code": code,
        "name": fields[0],
        "price": price,
        "prev_close": prev,
        "open": _to_float(fields[1]),
        "high": _to_float(fields[4]),
        "low": _to_float(fields[5]),
        "change_pct": (price / prev - 1.0) * 100.0,
        "date": fields[30] if len(fields) > 30 else "",
        "time": fields[31] if len(fields) > 31 else "",
        "source": "sina",
    }


def tencent_index_row(code, fields):
    if len(fields) < 6:
        return None
    try:
        price = float(fields[3])
        prev = float(fields[4])
    except (TypeError, ValueError):
        return None
    if price <= 0 or prev <= 0:
        return None
    pct = None
    if len(fields) > 32:
        pct = _to_float(fields[32])
    if pct is None:
        pct = (price / prev - 1.0) * 100.0
    return {
        "code": code,
        "name": fields[1],
        "price": price,
        "prev_close": prev,
        "open": _to_float(fields[5]),
        "high": _to_float(fields[33]) if len(fields) > 33 else None,
        "low": _to_float(fields[34]) if len(fields) > 34 else None,
        "change_pct": pct,
        "date": fields[30] if len(fields) > 30 else "",
        "time": fields[31] if len(fields) > 31 else "",
        "source": "tencent",
    }


def tencent_fund_row(code, fields):
    """v_jj011609="code~name~est_nav~est_chg~~nav~acc_nav~nav_chg~date~"."""
    if len(fields) < 8:
        return None
    return {
        "code": code,
        "name": fields[1],
        "est_nav": _to_float(fields[2]) or 0.0,
        "est_chg": _to_float(fields[3]) or 0.0,
        "nav": _to_float(fields[5]),
        "acc_nav": _to_float(fields[6]),
        "nav_chg": _to_float(fields[7]),
        "nav_date": fields[8] if len(fields) > 8 else "",
        "source": "tencent",
    }


def sina_us_row(code, fields):
    if len(fields) < 5:
        return None
    price = _to_float(fields[1])
    chg = _to_float(fields[4])
    pct = _to_float(fields[2])
    if price is None:
        return None
    return {
        "code": code,
        "name": fields[0],
        "price": price,
        "change_pct": pct if pct is not None else 0.0,
        "change": chg if chg is not None else 0.0,
        "quote_time": fields[3] if len(fields) > 3 else "",
        "prev_close": price - chg if chg is not None else None,
        "source": "sina",
    }


def sina_futures_row(code, fields):
    if len(fields) < 9:
        return None
    price = _to_float(fields[0])
    prev = _to_float(fields[7])
    if price is None or not prev:
        return None
    return {
        "code": code,
        "name": fields[13] if len(fields) > 13 else code,
        "price": price,
        "prev_settle": prev,
        "change_pct": (price / prev - 1.0) * 100.0,
        "quote_time": fields[6] if len(fields) > 6 else "",
        "source": "sina",
    }


def sina_fx_row(fields):
    if len(fields) < 12:
        return None
    price = _to_float(fields[8]) or _to_float(fields[1])
    pct = _to_float(fields[11])
    if price is None:
        return None
    return {"price": price, "change_pct": pct if pct is not None else 0.0, "source": "sina"}


def parse_tencent_kline(body, symbol):
    try:
        data = json.loads(body)
    except ValueError:
        return []
    node = (data.get("data") or {}).get(symbol) or {}
    rows = node.get("day") or node.get("qfqday") or []
    out = []
    for row in rows:
        if len(row) < 3:
            continue
        try:
            out.append({"date": row[0], "close": float(row[2]), "open": float(row[1]),
                        "high": float(row[3]) if len(row) > 3 else None,
                        "low": float(row[4]) if len(row) > 4 else None,
                        "volume": float(row[5]) if len(row) > 5 else None})
        except (TypeError, ValueError):
            continue
    return out


def parse_em_kline(body):
    try:
        data = json.loads(body)
    except ValueError:
        return []
    klines = ((data.get("data") or {}).get("klines")) or []
    out = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            out.append({"date": parts[0], "close": float(parts[2]), "open": float(parts[1]),
                        "high": float(parts[3]) if len(parts) > 3 else None,
                        "low": float(parts[4]) if len(parts) > 4 else None,
                        "volume": float(parts[5]) if len(parts) > 5 else None})
        except (TypeError, ValueError):
            continue
    return out


def parse_em_history(body):
    try:
        data = json.loads(body)
    except ValueError:
        return []
    rows = data.get("Datas") or data.get("Data") or []
    if isinstance(rows, dict):
        rows = rows.get("LSJZList") or []
    out = []
    for row in rows:
        try:
            out.append({
                "date": row.get("FSRQ"),
                "nav": float(row.get("DWJZ")),
                "chg_pct": _to_float(row.get("JZZZL")),
            })
        except (TypeError, ValueError):
            continue
    return out


def parse_em_fund_info(body):
    try:
        data = json.loads(body)
    except ValueError:
        return None
    rows = data.get("Datas") or []
    if isinstance(rows, list):
        return rows[0] if rows else None
    return None


def _to_float(value):
    try:
        if value in (None, "", "-", "--"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- fetchers
def sina_quotes(http, codes):
    """Fetch one or more Sina symbols in a single request."""
    if not codes:
        return {}
    url = "https://hq.sinajs.cn/list=" + ",".join(codes)
    body = http.get(url, headers=SINA_HEADERS, encoding="gbk", ttl=30)
    return parse_sina_quotes(body)


def fetch_index_quotes(http, indices):
    wanted = [item["code"] for item in indices]
    rows = {}
    try:
        raw = sina_quotes(http, wanted)
        for code in wanted:
            if code in raw:
                row = sina_index_row(code, raw[code])
                if row:
                    rows[code] = row
    except HttpError as exc:
        log.warning("sina index quotes failed: %s", exc)
    missing = [code for code in wanted if code not in rows]
    if missing:
        try:
            url = "https://qt.gtimg.cn/q=" + ",".join(missing)
            body = http.get(url, headers=TENCENT_HEADERS, encoding="gbk", ttl=30)
            parsed = _parse_tencent_quotes(body)
            for code in missing:
                if code in parsed:
                    row = tencent_index_row(code, parsed[code])
                    if row:
                        rows[code] = row
        except HttpError as exc:
            log.warning("tencent index quotes failed: %s", exc)
    return rows


def fetch_fund_quotes(http, codes):
    codes = [str(c) for c in codes]
    rows = {}
    try:
        url = "https://qt.gtimg.cn/q=" + ",".join("jj%s" % c for c in codes)
        body = http.get(url, headers=TENCENT_HEADERS, encoding="gbk", ttl=30)
        parsed = _parse_tencent_quotes(body)
        for code in codes:
            key = "jj" + code
            if key in parsed:
                row = tencent_fund_row(code, parsed[key])
                if row:
                    rows[code] = row
    except HttpError as exc:
        log.warning("tencent fund quotes failed: %s", exc)
    missing = [c for c in codes
               if c not in rows or not (rows[c].get("est_nav") or 0)]
    if missing:
        try:
            url = ("https://fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo"
                   "?pageIndex=1&pageSize=50&plat=Android&appType=ttjj&product=EFund"
                   "&Version=1&deviceid=1&Fcodes=" + ",".join(missing))
            body = http.get(url, headers=EM_HEADERS, ttl=30)
            for item in (json.loads(body).get("Datas") or []):
                code = str(item.get("FCODE", ""))
                if not code:
                    continue
                em_row = {
                    "code": code,
                    "name": item.get("SHORTNAME") or code,
                    "nav": _to_float(item.get("NAV")),
                    "acc_nav": _to_float(item.get("ACCNAV")),
                    "nav_chg": _to_float(item.get("NAVCHGRT")),
                    "nav_date": item.get("PDATE") or "",
                    "est_nav": _to_float(item.get("GSZ")) or 0.0,
                    "est_chg": _to_float(item.get("GSZZL")) or 0.0,
                    "est_time": item.get("GZTIME") or "",
                    "source": "eastmoney",
                }
                if code in rows:
                    for key in ("est_nav", "est_chg", "est_time"):
                        if em_row.get(key):
                            rows[code][key] = em_row[key]
                    rows[code]["source"] = "eastmoney"
                else:
                    rows[code] = em_row
        except (HttpError, ValueError) as exc:
            log.warning("eastmoney fund quotes failed: %s", exc)
    return rows


def fetch_fund_history(http, code, pages=1, page_size=300):
    """Daily NAV history (oldest-first).

    Primary source: fund.eastmoney.com/pingzhongdata (full series in one file,
    much less likely to be throttled).  Fallback: the mobile FundMNHisNetList
    endpoint, which can silently return a single row when rate limited.
    """
    rows = _pzd_history(http, code)
    if len(rows) >= 20:
        return rows
    rows = _mobile_history(http, code, pages=pages, page_size=page_size)
    return rows


def _pzd_history(http, code):
    url = ("https://fund.eastmoney.com/pingzhongdata/%s.js?v=20260101" % code)
    try:
        body = http.get(url, headers=EM_HEADERS, ttl=6 * 3600)
    except HttpError as exc:
        log.warning("pingzhongdata failed for %s: %s", code, exc)
        return []
    return parse_pzd_nav(body)


def parse_pzd_nav(body):
    match = re.search(r"var Data_netWorthTrend\s*=\s*(\[.*?\]);", body or "", re.S)
    if not match:
        return []
    try:
        points = json.loads(match.group(1))
    except ValueError:
        return []
    out = []
    for point in points:
        try:
            stamp = int(point.get("x")) / 1000.0
            nav = float(point.get("y"))
        except (TypeError, ValueError):
            continue
        date = time.strftime("%Y-%m-%d", time.gmtime(stamp + 8 * 3600))
        out.append({"date": date, "nav": nav,
                    "chg_pct": _to_float(point.get("equityReturn"))})
    out.sort(key=lambda item: item["date"])
    return out


def _mobile_history(http, code, pages=1, page_size=300):
    rows = []
    for page in range(1, pages + 1):
        url = ("https://fundmobapi.eastmoney.com/FundMNewApi/FundMNHisNetList"
               "?FCODE=%s&pageIndex=%d&pageSize=%d&deviceid=1&plat=Android"
               "&product=EFund&version=1" % (code, page, page_size))
        chunk, total = _history_page(http, url)
        if total and len(chunk) < min(page_size, total) and len(chunk) < 60:
            log.warning("fund history for %s looks throttled (%s/%s), retrying once",
                        code, len(chunk), total)
            time.sleep(1.5)
            chunk, total = _history_page(http, url, ttl=0)
        if not chunk:
            log.warning("fund history empty for %s page %s (total=%s)", code, page, total)
            break
        rows.extend(chunk)
        if len(chunk) < page_size:
            break
    # de-duplicate by date and return oldest-first
    seen = set()
    ordered = []
    for row in rows:
        if row["date"] in seen:
            continue
        seen.add(row["date"])
        ordered.append(row)
    ordered.sort(key=lambda item: item["date"])
    return ordered


def _history_page(http, url, ttl=6 * 3600):
    try:
        body = http.get(url, headers=EM_HEADERS, ttl=ttl)
    except HttpError as exc:
        log.warning("fund history request failed: %s", exc)
        return [], 0
    try:
        data = json.loads(body)
    except ValueError:
        return [], 0
    return parse_em_history(body), int(data.get("TotalCount") or 0)


def fetch_fund_info(http, code):
    url = ("https://fundmobapi.eastmoney.com/FundMNewApi/FundMNDetailInformation"
           "?FCODE=%s&deviceid=1&plat=Android&product=EFund&version=1" % code)
    try:
        body = http.get(url, headers=EM_HEADERS, ttl=24 * 3600)
        data = json.loads(body).get("Datas") or {}
        return {
            "code": code,
            "name": data.get("SHORTNAME") or code,
            "type": data.get("FTYPE") or "",
            "risk": data.get("RISKLEVEL") or "",
            "benchmark_name": data.get("INDEXNAME") or "",
            "benchmark_code": data.get("INDEXCODE") or "",
        }
    except (HttpError, ValueError) as exc:
        log.warning("fund info failed for %s: %s", code, exc)
        return None


def fetch_fund_holdings(http, code):
    url = ("https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition"
           "?FCODE=%s&deviceid=1&plat=Android&product=EFund&version=1" % code)
    try:
        body = http.get(url, headers=EM_HEADERS, ttl=24 * 3600)
        data = json.loads(body).get("Datas") or {}
        stocks = []
        for item in data.get("fundStocks") or []:
            try:
                stocks.append({"code": item.get("GPDM"), "name": item.get("GPJC"),
                               "weight": float(item.get("JZBL") or 0),
                               "exchange": item.get("TEXCH")})
            except (TypeError, ValueError):
                continue
        return stocks
    except (HttpError, ValueError) as exc:
        log.warning("fund holdings failed for %s: %s", code, exc)
        return []


def fetch_kline(http, code, days=260):
    """Index kline with Tencent primary and Eastmoney fallback."""
    symbol = code
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           "?param=%s,day,,,%d,qfq" % (symbol, days))
    try:
        body = http.get(url, headers=TENCENT_HEADERS, ttl=6 * 3600)
        rows = parse_tencent_kline(body, symbol)
        if rows:
            return rows
    except HttpError as exc:
        log.warning("tencent kline failed for %s: %s", code, exc)
    secid = _secid(code)
    if secid:
        url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
               "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
               "&klt=101&fqt=1&end=20500101&lmt=%d" % (secid, days))
        try:
            body = http.get(url, headers=EM_HEADERS, ttl=6 * 3600)
            return parse_em_kline(body)
        except HttpError as exc:
            log.warning("eastmoney kline failed for %s: %s", code, exc)
    return []


def fetch_ndx(http):
    try:
        raw = sina_quotes(http, ["gb_ndx"])
        if "gb_ndx" in raw:
            return sina_us_row("gb_ndx", raw["gb_ndx"])
    except HttpError as exc:
        log.warning("sina ndx failed: %s", exc)
    return None


def fetch_nq_futures(http):
    try:
        raw = sina_quotes(http, ["hf_NQ"])
        if "hf_NQ" in raw:
            return sina_futures_row("hf_NQ", raw["hf_NQ"])
    except HttpError as exc:
        log.warning("sina nq futures failed: %s", exc)
    return None


def fetch_usdcny(http):
    try:
        raw = sina_quotes(http, ["fx_susdcny"])
        if "fx_susdcny" in raw:
            return sina_fx_row(raw["fx_susdcny"])
    except HttpError as exc:
        log.warning("sina fx failed: %s", exc)
    return None


# --------------------------------------------------------------------------- helpers
def _parse_tencent_quotes(body):
    out = {}
    for match in re.finditer(r'v_([a-zA-Z0-9_]+)="([^"]*)"', body or ""):
        key, payload = match.group(1), match.group(2)
        if payload:
            out[key] = payload.split("~")
    return out


def _secid(code):
    code = str(code)
    if code.startswith("sh"):
        return "1." + code[2:]
    if code.startswith("sz"):
        return "0." + code[2:]
    if code.upper() == "NDX":
        return "100.NDX"
    if code.isdigit():
        return ("1." if code.startswith("6") else "0.") + code
    return ""
