"""Portfolio weights and allocation-aware position sizing.

The trend signal decides *direction*; this module decides whether the trade
makes sense given the current portfolio mix (single-fund concentration,
over/under-weight versus the target weight).
"""
from .util import fmt_money


def default_target_weight(count):
    return round(1.0 / count, 4) if count else 0.0


def build_portfolio(funds, cfg):
    """Attach weights and return a portfolio summary.

    `funds` are the rows produced by fundwatch.funds.build_funds (they already
    contain shares / market_value / target_weight).
    """
    total_value = sum((fund.get("market_value") or 0.0) for fund in funds)
    total_cost = sum((fund.get("cost_total") or 0.0) for fund in funds)
    total_profit = total_value - total_cost
    profit_pct = (total_profit / total_cost * 100.0) if total_cost else None

    count = len(funds) or 1
    raw_targets = []
    for fund in funds:
        raw = fund.get("target_weight")
        raw_targets.append(float(raw) if raw is not None else 1.0 / count)
    target_sum = sum(raw_targets)
    if target_sum <= 0:
        raw_targets = [1.0 / count] * len(funds)
        target_sum = 1.0
    for fund, raw in zip(funds, raw_targets):
        fund["target_weight_raw"] = round(raw, 4)
        fund["target_weight"] = round(raw / target_sum, 4)
        value = fund.get("market_value") or 0.0
        fund["weight"] = round(value / total_value, 4) if total_value else 0.0

    max_weight = max((fund.get("weight") or 0.0) for fund in funds) if funds else 0.0
    limit = float((cfg.get("portfolio") or {}).get("max_single_weight", 0.65))
    warnings = []
    if max_weight > limit and funds:
        top = max(funds, key=lambda item: item.get("weight") or 0.0)
        warnings.append("集中度偏高：%s 占 %.1f%%，超过单只基金上限 %.0f%%，建议逐步分散" % (
            top.get("name") or top.get("code"), max_weight * 100.0, limit * 100.0))
    if abs(target_sum - 1.0) > 0.02:
        warnings.append("目标占比合计 %.0f%%，已按比例归一化；建议在面板里调整到约 100%%" % (
            target_sum * 100.0))
    return {
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_profit": round(total_profit, 2),
        "profit_pct": round(profit_pct, 2) if profit_pct is not None else None,
        "max_weight": round(max_weight, 4),
        "max_single_weight": limit,
        "target_sum": round(target_sum, 4),
        "targets_valid": abs(target_sum - 1.0) <= 0.02,
        "warning": "；".join(warnings),
    }


def allocation_advice(fund, action, amount, portfolio, cfg, hard_exit=False):
    """Adjust a trend action/amount for the fund's portfolio weight.

    Returns (action, amount, notes).  Never increases risk beyond the limit.
    """
    notes = []
    total = float(portfolio.get("total_value") or 0.0)
    weight = float(fund.get("weight") or 0.0)
    target = float(fund.get("target_weight") or 0.0)
    score = fund.get("score") or 0.0
    limit = float((cfg.get("portfolio") or {}).get("max_single_weight", 0.65))
    band = float((cfg.get("portfolio") or {}).get("rebalance_band", 0.05))

    if not total:
        return action, amount, notes

    # 1) hard concentration cap: trim at least back to the limit
    if weight > limit:
        need = (weight - limit) * total
        if action != "减仓" or amount < need:
            action = "减仓"
            amount = need
        notes.append("单只占比 %.1f%% 超过上限 %.0f%%，建议至少减仓 %s 回到上限" % (
            weight * 100.0, limit * 100.0, fmt_money(need)))
        return action, round(amount, 2), notes

    # When the target weights do not add up to 100%, only the hard cap above is
    # reliable; do not rebalance against arbitrary normalized targets.
    if not bool(portfolio.get("targets_valid", True)):
        if action == "加仓":
            room = (limit - weight) * total
            if amount > room:
                amount = room
                notes.append("按单只上限 %.0f%% 控制，本次加仓上限约 %s" % (
                    limit * 100.0, fmt_money(room)))
            notes.append("目标占比合计未统一到 100%%，暂不按目标调仓")
        return action, round(amount, 2), notes

    # 2) adding: do not push the weight above target + band
    if action == "加仓":
        room = (target + band - weight) * total
        if room <= 50:
            notes.append("占比 %.1f%% 已接近目标 %.1f%%，暂不加仓" % (
                weight * 100.0, target * 100.0))
            return "持有", 0.0, notes
        if amount > room:
            amount = room
            notes.append("按目标占比 %.0f%% 控制，本次加仓上限约 %s" % (
                target * 100.0, fmt_money(room)))
        return action, round(amount, 2), notes

    # 3) trimming: do not cut an already underweight fund (unless it is a hard risk exit)
    if action == "减仓" and weight <= target - band and not hard_exit:
        notes.append("占比 %.1f%% 已低于目标 %.1f%%，暂不减仓" % (
            weight * 100.0, target * 100.0))
        return "观望", 0.0, notes

    # 4) rebalance: underweight + decent trend -> small add
    if action in ("持有", "观望") and weight < target - band and score >= 55:
        room = (target - band - weight) * total
        amount = min(float(fund.get("add_budget") or 0.0) * 0.3, room)
        if amount >= 50:
            notes.append("占比 %.1f%% 低于目标 %.1f%%，按再平衡小幅补仓" % (
                weight * 100.0, target * 100.0))
            return "加仓", round(amount, 2), notes
    return action, amount, notes
