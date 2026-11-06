"""Per-fund snapshot: latest NAV, intraday estimate, holding P/L and indicators."""
import logging

from . import sources
from .analysis import indicators
from .util import now_cn

log = logging.getLogger("fundwatch.funds")


def build_funds(http, cfg, market, today):
    codes = [fund["code"] for fund in cfg["funds"]]
    try:
        quotes = sources.fetch_fund_quotes(http, codes)
    except Exception as exc:  # noqa: BLE001
        log.warning("fund quotes failed: %s", exc)
        quotes = {}

    out = []
    default_target = round(1.0 / len(cfg["funds"]), 4) if cfg["funds"] else 0.0
    for fund in cfg["funds"]:
        code = fund["code"]
        quote = quotes.get(code) or {}
        history = sources.fetch_fund_history(http, code, pages=1, page_size=300)
        if not history and quote.get("nav"):
            history = [{"date": quote.get("nav_date") or today,
                        "nav": quote["nav"],
                        "chg_pct": quote.get("nav_chg")}]
        series = [row["nav"] for row in history if row.get("nav")]

        estimate = _estimate(fund, quote, market, today, series)
        if estimate["is_estimate"]:
            series = series + [estimate["nav"]]
        elif not series and estimate["nav"]:
            series = [estimate["nav"]]

        ind = indicators(series) if series else {}

        latest_nav = quote.get("nav") or estimate["nav"]
        if latest_nav and estimate["nav"]:
            shares = _shares(fund, latest_nav)
            cost_total = shares * fund["cost"]
            market_value = shares * estimate["nav"]
            pnl = market_value - cost_total
            pnl_pct = (estimate["nav"] / fund["cost"] - 1.0) * 100.0
        else:
            shares = _shares(fund, latest_nav)
            cost_total = shares * fund["cost"]
            market_value = None
            pnl = None
            pnl_pct = None
        benchmark = _benchmark_row(fund, market)
        target_weight = fund.get("target_weight")
        if target_weight is None:
            target_weight = default_target
        out.append({
            "code": code,
            "name": fund.get("name") or code,
            "type": "",
            "kind": fund.get("kind", "a_share"),
            "cost": fund["cost"],
            "amount": fund["amount"],
            "amount_mode": fund["amount_mode"],
            "shares": round(shares, 4),
            "target_weight": round(float(target_weight), 4),
            "cost_total": round(cost_total, 2),
            "market_value": round(market_value, 2) if market_value is not None else None,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "latest_nav": latest_nav,
            "latest_nav_date": quote.get("nav_date") or "",
            "nav": estimate["nav"],
            "nav_date": estimate["nav_date"],
            "nav_chg": quote.get("nav_chg"),
            "est_nav": estimate["nav"],
            "est_chg": estimate["chg_pct"],
            "est_source": estimate["source"],
            "is_estimate": estimate["is_estimate"],
            "est_as_of": estimate["as_of"],
            "history": [{"date": row["date"], "nav": row["nav"]} for row in history[-60:]],
            "indicators": ind,
            "benchmark": benchmark,
            "last_buy_date": fund.get("last_buy_date"),
        })
    return out


def _shares(fund, nav):
    if fund.get("shares"):
        return float(fund["shares"])
    if fund["amount_mode"] == "principal":
        return fund["amount"] / fund["cost"]
    if nav:
        return fund["amount"] / nav
    return fund["amount"] / fund["cost"]


def _benchmark_row(fund, market):
    bench_code = fund.get("benchmark") or ""
    if bench_code:
        for row in market.get("indices", []):
            if row["code"] == bench_code:
                return row
    return None


def _is_us_fund(fund):
    bench = str(fund.get("benchmark") or "").upper()
    name = str(fund.get("name") or "")
    return (bench == "NDX" or "纳斯达克" in name or "纳指" in name
            or "美国" in name or "标普" in name)


def _estimate(fund, quote, market, today, series):
    nav = quote.get("nav")
    if not nav and series:
        nav = series[-1]
    nav_date = quote.get("nav_date") or ""
    if nav and nav_date == today:
        return {"nav": nav, "chg_pct": quote.get("nav_chg"),
                "source": "实际净值（今日已更新）", "is_estimate": False,
                "nav_date": nav_date, "as_of": today}

    if quote.get("est_nav"):
        est_nav = quote["est_nav"]
        est_chg = quote.get("est_chg")
        if est_chg is None and nav:
            est_chg = (est_nav / nav - 1.0) * 100.0
        return {"nav": est_nav, "chg_pct": est_chg,
                "source": "腾讯盘中估值" if quote.get("source") == "tencent" else "东财盘中估值",
                "is_estimate": True, "nav_date": nav_date,
                "as_of": quote.get("est_time") or today}

    if not nav:
        return {"nav": None, "chg_pct": None, "source": "无数据", "is_estimate": False,
                "nav_date": nav_date, "as_of": today}

    # proxy: index tracking for A-share funds, Nasdaq futures for US QDII
    if fund.get("kind") == "qdii":
        if not _is_us_fund(fund):
            return {"nav": nav, "chg_pct": None,
                    "source": "最近净值（该 QDII 暂无指数代理）", "is_estimate": False,
                    "nav_date": nav_date, "as_of": today}
        nq = market.get("nq") or {}
        ndx = market.get("ndx") or {}
        base_pct = nq.get("change_pct")
        base_name = "纳指期货"
        if base_pct is None:
            base_pct = ndx.get("change_pct")
            base_name = "隔夜纳指"
        fx = market.get("fx") or {}
        fx_pct = fx.get("change_pct") or 0.0
        if base_pct is None:
            return {"nav": nav, "chg_pct": None, "source": "估值不可用（使用最新净值）",
                    "is_estimate": True, "nav_date": nav_date, "as_of": today}
        # 95% tracking + currency effect; marked as proxy in the report
        est_chg = base_pct * 0.95 + fx_pct * 0.95
        est_nav = nav * (1.0 + est_chg / 100.0)
        return {"nav": round(est_nav, 4), "chg_pct": round(est_chg, 2),
                "source": "%s代理（%+.2f%%，汇率 %+.2f%%）" % (base_name, base_pct, fx_pct),
                "is_estimate": True, "nav_date": nav_date,
                "as_of": (nq.get("quote_time") or today)}

    bench = _benchmark_row(fund, market)
    bench_pct = bench.get("change_pct") if bench else None
    if bench_pct is None:
        return {"nav": nav, "chg_pct": None, "source": "估值不可用（使用最新净值）",
                "is_estimate": True, "nav_date": nav_date, "as_of": today}
    est_chg = bench_pct * 0.95
    est_nav = nav * (1.0 + est_chg / 100.0)
    return {"nav": round(est_nav, 4), "chg_pct": round(est_chg, 2),
            "source": "%s代理（指数 %+.2f%%）" % (bench.get("name") or "标的指数", bench_pct),
            "is_estimate": True, "nav_date": nav_date,
            "as_of": now_cn().strftime("%H:%M")}
