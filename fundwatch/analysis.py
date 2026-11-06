"""Indicators, market regime scoring and rule-based fund signals.

All functions are deterministic and side-effect free so they can be unit
tested with synthetic price series.
"""
from typing import Dict, List, Optional

STRONG = "强势"
MILD_STRONG = "偏强"
NEUTRAL = "震荡"
MILD_WEAK = "偏弱"
WEAK = "弱势"


# --------------------------------------------------------------------------- indicators
def ma(series, window):
    if not series or len(series) < window:
        return None
    chunk = series[-window:]
    return sum(chunk) / float(window)


def pct_return(series, window):
    if not series or len(series) <= window:
        return None
    base = series[-window - 1]
    if not base:
        return None
    return (series[-1] / base - 1.0) * 100.0


def rsi(series, window=14):
    if not series or len(series) <= window:
        return None
    gains = 0.0
    losses = 0.0
    for idx in range(-window, 0):
        diff = series[idx] - series[idx - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = (gains / window) / (losses / window)
    return 100.0 - 100.0 / (1.0 + rs)


def drawdown_from_high(series, window=120):
    if not series:
        return None
    chunk = series[-window:]
    high = max(chunk)
    if not high:
        return None
    return (series[-1] / high - 1.0) * 100.0


def deviation_from_ma(series, window=20):
    avg = ma(series, window)
    if not avg:
        return None
    return (series[-1] / avg - 1.0) * 100.0


def indicators(series):
    return {
        "last": series[-1] if series else None,
        "ma5": ma(series, 5),
        "ma20": ma(series, 20),
        "ma60": ma(series, 60),
        "rsi14": rsi(series, 14),
        "ret5": pct_return(series, 5),
        "ret20": pct_return(series, 20),
        "drawdown": drawdown_from_high(series, 120),
        "dev_ma20": deviation_from_ma(series, 20),
        "n": len(series),
    }


# --------------------------------------------------------------------------- market
def score_market(rows):
    """rows: list of dicts with price, ma20, ma60, ret5, ret20."""
    usable = [row for row in rows if row.get("ma20") and row.get("ma60")
              and row.get("ret5") is not None and row.get("ret20") is not None]
    if not usable:
        return 50.0, NEUTRAL
    total = 0.0
    for row in usable:
        score = 0.0
        if row["price"] > row["ma20"]:
            score += 25.0
        if row["ma20"] > row["ma60"]:
            score += 25.0
        if row["ret20"] > 0:
            score += 25.0
        if row["ret5"] > 0:
            score += 25.0
        total += score
    score = total / len(usable)
    # A sharp 5-day drop in the benchmark caps the regime.
    hs300 = next((row for row in usable if str(row.get("code", "")).endswith("000300")), None)
    if hs300 and hs300.get("ret5") is not None and hs300["ret5"] < -3.0:
        score = min(score, 44.0)
    return round(score, 1), regime_for(score)


def regime_for(score):
    if score >= 75:
        return STRONG
    if score >= 60:
        return MILD_STRONG
    if score >= 40:
        return NEUTRAL
    if score >= 25:
        return MILD_WEAK
    return WEAK


# --------------------------------------------------------------------------- fund
def score_fund(ind, benchmark_ind=None):
    """Return (score 0-100, components dict)."""
    last = ind.get("last")
    ma20 = ind.get("ma20")
    ma60 = ind.get("ma60")
    ret20 = ind.get("ret20")
    macd_ok = last is not None and ma20 is not None and ma60 is not None

    trend = 0.0
    if macd_ok:
        if last > ma20:
            trend += 15.0
        if ma20 > ma60:
            trend += 15.0
        if ret20 is not None and ret20 > 0:
            trend += 15.0
    else:
        trend = 22.5

    rsi_val = ind.get("rsi14")
    momentum = 0.0
    if rsi_val is None:
        momentum = 10.0
    elif 40 <= rsi_val <= 70:
        momentum = 20.0
    elif 30 <= rsi_val < 40 or 70 < rsi_val <= 80:
        momentum = 10.0

    dd = ind.get("drawdown")
    if dd is None:
        position = 10.0
    elif -15.0 <= dd < -5.0:
        position = 20.0
    elif dd > -5.0:
        position = 12.0
    elif -25.0 <= dd < -15.0:
        position = 10.0
    else:
        position = 0.0

    relative = 8.0
    if benchmark_ind and benchmark_ind.get("ret20") is not None and ret20 is not None:
        diff = ret20 - benchmark_ind["ret20"]
        if diff > 0:
            relative = 15.0
        elif diff > -3.0:
            relative = 8.0
        else:
            relative = 0.0

    score = trend + momentum + position + relative
    components = {
        "trend": round(trend, 1),
        "momentum": round(momentum, 1),
        "position": round(position, 1),
        "relative": round(relative, 1),
        "total": round(score, 1),
    }
    return round(score, 1), components


# --------------------------------------------------------------------------- decision
def decide(fund, ind, score, regime, pnl_pct, qdii_ctx=None):
    """Return dict: action, add_fraction, reasons, risk_flag.

    action: 加仓 / 持有 / 减仓 / 观望
    add_fraction: fraction of the fund's add_budget for 加仓
    """
    reasons: List[str] = []
    action = "持有"
    add_fraction = 0.0
    risk_flag = ""
    hard_exit = False

    last = ind.get("last")
    ma20 = ind.get("ma20")
    ma60 = ind.get("ma60")
    rsi_val = ind.get("rsi14")
    dd = ind.get("drawdown")
    dev = ind.get("dev_ma20")

    is_qdii = fund.get("kind") == "qdii"
    is_us_qdii = is_qdii and _is_us_fund(fund)
    qdii = qdii_ctx or {}
    nq_pct = qdii.get("nq_pct")

    # QDII is driven by the Nasdaq trend, not by the A-share regime.
    effective_regime = regime
    if is_qdii and last is not None and ma20 is not None and ma60 is not None:
        if last > ma20 and ma20 > ma60:
            effective_regime = MILD_STRONG
        elif last > ma60:
            effective_regime = NEUTRAL
        else:
            effective_regime = MILD_WEAK

    # 1) hard risk exit
    if pnl_pct is not None and pnl_pct < -15.0 and score < 40:
        action = "减仓"
        add_fraction = 0.0
        hard_exit = True
        reasons.append("持仓亏损 %.1f%%，趋势评分仅 %.0f 分，先降低风险敞口" % (pnl_pct, score))
    # 2) overheat: do not chase, trim only winners
    elif (rsi_val is not None and rsi_val > 75) or (dev is not None and dev > 10.0):
        if pnl_pct is not None and pnl_pct > 10.0:
            action = "减仓"
            add_fraction = 0.0
            reasons.append("短期涨幅过快（RSI %.0f，偏离 MA20 %.1f%%），部分止盈" % (rsi_val or 0, dev or 0))
        else:
            action = "持有"
            reasons.append("短期涨幅过快（RSI %.0f，偏离 MA20 %.1f%%），不追高" % (rsi_val or 0, dev or 0))
    # 3) QDII: tonight's US session matters for orders before 15:00
    elif is_us_qdii and nq_pct is not None and nq_pct <= -1.0 and last is not None and ma20 and last < ma20:
        action = "观望"
        reasons.append("纳指期货 %.1f%%，纳指/基金净值在 MA20 下方，暂缓加仓" % nq_pct)
    # 4) deep pullback with washed-out momentum
    elif (dd is not None and dd <= -15.0) and (rsi_val is not None and rsi_val < 35) \
            and effective_regime in (STRONG, MILD_STRONG, NEUTRAL):
        action = "加仓"
        add_fraction = 0.5
        reasons.append("距 120 日高点回撤 %.1f%%，RSI 仅 %.0f，大盘未走弱，适合分批接回" % (abs(dd), rsi_val))
    # 5) broken trend.  Do not sell into a crash that is already deep.
    elif score < 40 and last is not None and ma60 is not None and last < ma60 \
            and effective_regime in (NEUTRAL, MILD_WEAK, WEAK):
        if (dd is not None and dd <= -25.0) and (pnl_pct is None or pnl_pct <= 0):
            action = "观望"
            reasons.append("趋势仍弱，但距高点已回撤 %.1f%%，不在低位追杀；"
                           "等反弹到 MA20 或跌破前低再做决定" % abs(dd))
        else:
            action = "减仓"
            add_fraction = 0.0
            if is_us_qdii:
                trend_word = "纳指趋势转弱"
            elif is_qdii:
                trend_word = "基金趋势转弱"
            else:
                trend_word = "大盘%s" % effective_regime
            reasons.append("净值跌破 MA60，趋势评分 %.0f，%s，先减仓防守" % (score, trend_word))
    # 6) trend intact and tape supportive
    elif score >= 70 and effective_regime in (STRONG, MILD_STRONG):
        action = "加仓"
        add_fraction = 0.3
        if is_us_qdii:
            trend_word = "纳指趋势偏强"
        elif is_qdii:
            trend_word = "基金趋势偏强"
        else:
            trend_word = "大盘" + effective_regime
        reasons.append("趋势评分 %.0f，%s，顺势小幅加仓" % (score, trend_word))
    elif is_us_qdii and nq_pct is not None and nq_pct >= 0 and last is not None and ma60 and last > ma60:
        action = "加仓"
        add_fraction = 0.2
        reasons.append("纳指期货 %.1f%%，基金净值在 MA60 上方，顺势加仓" % nq_pct)
    elif score >= 55:
        action = "持有"
        reasons.append("趋势评分 %.0f，未触发加减仓条件" % score)
    elif score >= 40:
        action = "观望"
        reasons.append("趋势评分 %.0f，多空不明，等待更清晰的信号" % score)
    else:
        action = "观望"
        reasons.append("趋势评分 %.0f，偏弱但未到减仓条件，暂不操作" % score)

    # supporting context for the report
    if ma20 and last:
        reasons.append("净值 %.4f，MA20 %.4f，MA60 %s" % (
            last, ma20, ("%.4f" % ma60) if ma60 else "--"))
    if is_us_qdii and nq_pct is not None:
        reasons.append("纳指期货 %.2f%%，15:00 前下单按今晚美股收盘定价" % nq_pct)

    return {"action": action, "add_fraction": add_fraction,
            "reasons": reasons, "risk_flag": risk_flag, "hard_exit": hard_exit}


def size_amount(fund, value, decision, risk_cfg):
    """Translate an action into a yuan amount."""
    action = decision["action"]
    budget = float(fund.get("add_budget") or 0)
    max_add = float(risk_cfg.get("max_add_pct", 0.2))
    if action == "加仓":
        amount = min(budget * float(decision.get("add_fraction") or 0.3), value * max_add)
        return round(max(0.0, amount), 2)
    if action == "减仓":
        # trim 10% by default, 20% when the risk flag is a hard exit
        pct = 0.2 if decision.get("hard_exit") else 0.1
        return round(max(0.0, value * pct), 2)
    return 0.0


def _is_us_fund(fund):
    bench = str(fund.get("benchmark") or "").upper()
    name = str(fund.get("name") or "")
    return (bench == "NDX" or "纳斯达克" in name or "纳指" in name
            or "美国" in name or "标普" in name)
