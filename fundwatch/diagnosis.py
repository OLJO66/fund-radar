"""Portfolio configuration diagnosis.

This is a *structural* review of how the holdings are allocated, independent of
the day-to-day trend signals: concentration, target weights, asset classes,
regions, style overlap and defensive buffer.

The output is explanatory rather than prescriptive and is shown in the report
and the local dashboard.
"""
from .util import fmt_money
import re

ASSET_LABELS = {
    "equity": "权益",
    "mixed": "混合",
    "bond": "债券",
    "cash": "货币",
    "gold": "黄金",
}
REGION_LABELS = {"cn": "A股", "us": "美股", "overseas": "海外"}
STYLE_LABELS = {
    "tech": "科技成长",
    "consumer": "消费",
    "health": "医药",
    "broad": "宽基",
    "value": "红利价值",
    "fixed": "固收",
    "mixed": "混合",
    "other": "其他",
}


def infer_profile(fund):
    """Infer asset class / region / style from the fund name and benchmark."""
    name = str(fund.get("name") or "")
    bench = str(fund.get("benchmark") or "").upper()
    text = name + " " + bench
    kind = str(fund.get("kind") or "a_share")

    if "货币" in name or "现金" in name or "理财" in name:
        asset = "cash"
    elif any(key in name for key in ("纯债", "债券", "短债", "中短债", "固收")):
        asset = "bond"
    elif any(key in name for key in ("黄金", "贵金属")):
        asset = "gold"
    elif any(key in name for key in ("混合", "平衡", "灵活配置")):
        asset = "mixed"
    else:
        asset = "equity"

    if bench == "NDX" or any(key in name for key in ("纳斯达克", "纳指", "标普", "美国")):
        region = "us"
    elif any(key in name for key in ("新兴市场", "印度", "越南", "日本", "欧洲",
                                      "亚太", "全球", "海外", "港股", "恒生")):
        region = "overseas"
    elif kind == "qdii":
        region = "overseas"
    else:
        region = "cn"

    if asset in ("bond", "cash", "gold"):
        style = "fixed"
    elif any(key in text for key in ("科创", "科技", "半导体", "芯片", "人工智能",
                                      "纳斯达克", "纳指", "成长")):
        style = "tech"
    elif any(key in text for key in ("消费", "白酒", "食品")):
        style = "consumer"
    elif any(key in text for key in ("医药", "医疗", "健康")):
        style = "health"
    elif any(key in text for key in ("沪深300", "中证500", "上证50", "中证1000",
                                      "中证A500", "宽基", "全指")):
        style = "broad"
    elif any(key in text for key in ("红利", "价值")):
        style = "value"
    elif asset == "mixed":
        style = "mixed"
    else:
        style = "other"

    currency = "USD" if region == "us" else ("海外" if region == "overseas" else "CNY")
    return {"asset_class": asset, "region": region, "style": style,
            "currency": currency}


def diagnose(funds, portfolio, cfg):
    if not funds:
        return {
            "score": 0, "level": "无持仓",
            "summary": "还没有基金，无法做配置诊断。",
            "dimensions": [], "issues": [],
            "suggestions": ["先在管理面板添加基金，再查看配置诊断。"],
            "positives": [], "metrics": {}, "profiles": [],
        }

    total = float(portfolio.get("total_value") or 0.0)
    rows = []
    for fund in funds:
        weight = float(fund.get("weight") or 0.0)
        if not weight and total:
            weight = float(fund.get("market_value") or 0.0) / total
        profile = infer_profile(fund)
        rows.append({
            "code": fund.get("code", ""),
            "name": fund.get("name", ""),
            "weight": weight,
            "target_weight": float(fund.get("target_weight") or 0.0),
            "target_raw": float(fund.get("target_weight_raw") or
                                fund.get("target_weight") or 0.0),
            "pnl_pct": (fund.get("pnl_pct") if fund.get("pnl_pct") is not None
                        else fund.get("profit_pct")),
            "score": fund.get("score"),
            **profile,
        })

    weight_sum = sum(row["weight"] for row in rows) or 1.0
    for row in rows:
        row["weight"] = row["weight"] / weight_sum

    max_row = max(rows, key=lambda item: item["weight"])
    max_weight = max_row["weight"]
    hhi = sum(row["weight"] ** 2 for row in rows)
    tech_weight = sum(row["weight"] for row in rows if row["style"] == "tech")
    defensive_weight = sum(row["weight"] for row in rows
                           if row["asset_class"] in ("bond", "cash"))
    equity_weight = sum(row["weight"] for row in rows
                        if row["asset_class"] in ("equity", "mixed"))
    overseas_weight = sum(row["weight"] for row in rows if row["region"] != "cn")
    category_weights = _group_weights(rows, "asset_class")
    region_weights = _group_weights(rows, "region")
    target_sum = float(portfolio.get("target_sum") or
                       sum(row["target_raw"] for row in rows) or 1.0)
    targets_valid = abs(target_sum - 1.0) <= 0.02
    target_deviation = 0.5 * sum(abs(row["weight"] - row["target_weight"]) for row in rows)

    dims = []
    conc_score = _clamp(100 - max(0.0, max_weight - 0.40) * 300
                        - max(0.0, hhi - 0.34) * 150)
    dims.append(_dim("concentration", "集中度", 25, conc_score,
                     "最大单只 %s 占 %.1f%%，HHI %.2f" % (
                         max_row["code"], max_weight * 100, hhi)))

    if not targets_valid:
        target_score = 20
        target_comment = "目标占比合计 %.0f%%，已按比例归一化" % (target_sum * 100)
    else:
        target_score = _clamp(100 - target_deviation * 250)
        target_comment = "实际与目标平均偏离 %.1f%%" % (target_deviation * 100)
    dims.append(_dim("target", "目标一致性", 20, target_score, target_comment))

    category_count = len([value for value in category_weights.values() if value > 0.02])
    max_category = max(category_weights.values()) if category_weights else 1.0
    if category_count >= 3:
        asset_score = _clamp(100 - max(0.0, max_category - 0.50) * 100)
    elif category_count == 2:
        asset_score = _clamp(65 - max(0.0, max_category - 0.60) * 100)
    else:
        asset_score = 30
    asset_comment = "%d 类资产，最大类别 %.0f%%" % (category_count, max_category * 100)
    dims.append(_dim("asset", "资产类别", 20, asset_score, asset_comment))

    region_count = len([value for value in region_weights.values() if value > 0.02])
    if 0.20 <= overseas_weight <= 0.50:
        region_score = 100
    elif 0.10 <= overseas_weight < 0.20 or 0.50 < overseas_weight <= 0.60:
        region_score = 80
    elif 0.60 < overseas_weight <= 0.70:
        region_score = 60
    elif overseas_weight > 0.70:
        region_score = 40
    else:
        region_score = 50
    dims.append(_dim("region", "区域/币种", 15, region_score,
                     "海外资产 %.1f%%，覆盖 %d 个区域" % (overseas_weight * 100, region_count)))

    if tech_weight <= 0.40:
        style_score = 100
    elif tech_weight <= 0.60:
        style_score = 70
    elif tech_weight <= 0.75:
        style_score = 45
    else:
        style_score = 20
    dims.append(_dim("style", "风格重叠", 10, style_score,
                     "科技成长合计 %.1f%%" % (tech_weight * 100)))

    if defensive_weight >= 0.20:
        defense_score = 100
    elif defensive_weight >= 0.10:
        defense_score = 75
    elif defensive_weight > 0.02:
        defense_score = 50
    else:
        defense_score = 0
    dims.append(_dim("defense", "防守资产", 10, defense_score,
                     "货币/债券合计 %.1f%%" % (defensive_weight * 100)))

    score = round(sum(dim["score"] * dim["weight"] for dim in dims) / 100.0)

    issues = []
    suggestions = []
    positives = []

    if not targets_valid:
        issues.append(_issue("high", "目标占比合计 %.0f%%" % (target_sum * 100),
                             "目标占比之和应为 100%%，当前为 %.0f%%，系统只能按比例归一化使用。"
                             % (target_sum * 100)))
        shown = rows[:4]
        targets_text = " / ".join("%s %.0f%%" % (row["code"], row["target_raw"] * 100)
                                  for row in shown)
        if len(rows) > len(shown):
            targets_text += " 等 %d 只" % len(rows)
        suggestions.append("先把目标占比调到合计 100%%：当前 %s（合计 %.0f%%），"
                           "可参考均分或 40/30/30。" % (targets_text, target_sum * 100))

    for row in rows:
        if row["target_raw"] > 0.60:
            issues.append(_issue("low", "%s 目标占比 %.0f%% 偏高" % (
                row["code"], row["target_raw"] * 100),
                "单只基金目标超过 60%%，组合会过度依赖一只基金的表现。"))

    if tech_weight > 0.70:
        issues.append(_issue("high", "科技成长暴露 %.1f%%" % (tech_weight * 100),
                             "科技成长类基金合计占比过高，净值波动和回撤可能放大。"))
        suggestions.append("把科技成长暴露从 %.1f%% 降到 60%% 以下：可将 011609 或 016452 "
                           "的一部分换成沪深300/中证500等宽基或债券。" % (tech_weight * 100))
    elif tech_weight > 0.55:
        issues.append(_issue("medium", "科技成长暴露 %.1f%%" % (tech_weight * 100),
                             "科技成长占比偏高，注意波动。"))

    if defensive_weight < 0.05:
        issues.append(_issue("medium", "没有防守资产",
                             "货币/债券仓位为 0，遇到系统性下跌时缺少缓冲和补仓弹药。"))
        suggestions.append("保留 10–20% 的货币基金或短债作为机动仓位，降低组合整体回撤。")

    if category_count < 3 and max_category > 0.75:
        issues.append(_issue("medium", "权益类占比 %.1f%%" % (max_category * 100),
                             "资产类别偏单一，组合主要跟随权益市场波动。"))

    if max_weight > 0.60:
        issues.append(_issue("high", "最大单只 %.1f%%" % (max_weight * 100),
                             "%s 占比过高，单一基金风险集中。" % max_row["code"]))
    elif max_weight > 0.50:
        issues.append(_issue("medium", "最大单只 %.1f%%" % (max_weight * 100),
                             "%s 占比偏高，建议单只上限 50%%。" % max_row["code"]))

    loss_rows = [row for row in rows
                 if row["pnl_pct"] is not None and row["pnl_pct"] < -15.0
                 and row["weight"] >= 0.10]
    if loss_rows:
        loss = loss_rows[0]
        issues.append(_issue("medium", "%s 亏损 %.1f%%" % (
            loss["code"], loss["pnl_pct"]),
            "亏损超过 15%% 且仍占 %.1f%%，先确认是否执行止损/减仓纪律。"
            % (loss["weight"] * 100)))
        suggestions.append("%s 当前亏损 %.1f%%，先按纪律减仓/止损，再决定是否补足这类资产。"
                           % (loss["code"], loss["pnl_pct"]))

    if overseas_weight > 0.70:
        issues.append(_issue("medium", "海外资产 %.1f%%" % (overseas_weight * 100),
                             "海外占比偏高，需注意汇率、QDII 额度和申赎时滞。"))
        suggestions.append("海外资产占比 %.1f%%，可适当回流一部分到 A 股宽基，降低汇率和单一市场风险。"
                           % (overseas_weight * 100))
    elif 0.20 <= overseas_weight <= 0.60:
        positives.append("区域分散尚可：A股 %.1f%%、海外 %.1f%%。"
                         % ((1 - overseas_weight) * 100, overseas_weight * 100))

    if targets_valid:
        overweight = [row for row in rows
                      if row["target_weight"] and row["weight"] - row["target_weight"] > 0.05]
        for row in overweight:
            gap = row["weight"] - row["target_weight"]
            amount = gap * total if total else 0.0
            issues.append(_issue("low", "%s 高于目标 %.1f 个百分点" % (row["code"], gap * 100),
                                 "当前 %.1f%%，归一化目标 %.1f%%。" % (
                                     row["weight"] * 100, row["target_weight"] * 100)))
            if amount >= 100:
                suggestions.append("%s 当前 %.1f%%，高于目标 %.1f%%，可考虑减约 %s 向目标靠拢。"
                                   % (row["code"], row["weight"] * 100,
                                      row["target_weight"] * 100, fmt_money(amount)))

    duplicate_groups = []
    name_groups = {}
    for row in rows:
        name_groups.setdefault(_base_name(row["name"]), []).append(row)
    for group in name_groups.values():
        if len(group) > 1:
            duplicate_groups.append(group)
            codes = "、".join(row["code"] for row in group)
            issues.append(_issue("medium", "同一基金重复持有：%s" % codes,
                                 "这些份额属于同一只基金（A/C 类），同时持有会重复暴露、"
                                 "增加跟踪成本，建议合并到费率更合适的一份。"))
            suggestions.append("%s 是同一基金的 A/C 份额，建议合并，只保留费率和持有期更合适的一份。"
                               % codes)

    if len(rows) > 8:
        issues.append(_issue("medium", "基金数量 %d 只偏多" % len(rows),
                             "持有过多基金增加跟踪成本，也容易风格重叠；建议保留 5–8 只核心基金。"))
        suggestions.append("当前 %d 只基金，建议合并/清理到 5–8 只，并给每只写清定位"
                           "（宽基、行业、海外、债券、黄金）。" % len(rows))

    if not positives and region_count >= 2:
        positives.append("已经覆盖 %d 个区域，区域分散是加分项。" % region_count)

    if not issues:
        issues.append(_issue("low", "配置整体均衡", "没有发现明显的集中或偏离问题。"))

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda item: severity_rank.get(item["severity"], 9))
    suggestions = _dedupe(suggestions)[:6]

    high_count = sum(1 for item in issues if item["severity"] == "high")
    if high_count:
        score = min(score, 62)
    if not targets_valid:
        score = min(score, 55)
    if duplicate_groups:
        score = min(score, 65)
    score = int(round(score))
    level = _level(score)
    if high_count:
        summary = "配置有 %d 个高优先级问题：%s。" % (
            high_count, "、".join(item["title"] for item in issues
                                  if item["severity"] == "high"))
    elif score < 65:
        summary = "配置整体%s，还有 %d 项可优化。" % (level, len(issues))
    else:
        summary = "配置整体%s，结构基本合理。" % level

    return {
        "score": score,
        "level": level,
        "summary": summary,
        "dimensions": dims,
        "issues": issues,
        "suggestions": suggestions,
        "positives": positives,
        "metrics": {
            "max_weight": round(max_weight, 4),
            "max_weight_code": max_row["code"],
            "hhi": round(hhi, 4),
            "tech_weight": round(tech_weight, 4),
            "defensive_weight": round(defensive_weight, 4),
            "equity_weight": round(equity_weight, 4),
            "overseas_weight": round(overseas_weight, 4),
            "region_count": region_count,
            "category_count": category_count,
            "fund_count": len(rows),
            "duplicate_groups": [[row["code"] for row in group] for group in duplicate_groups],
            "target_sum": round(target_sum, 4),
            "target_deviation": round(target_deviation, 4),
        },
        "profiles": [{
            "code": row["code"], "name": row["name"],
            "weight": round(row["weight"], 4),
            "asset_class": row["asset_class"],
            "asset_label": ASSET_LABELS.get(row["asset_class"], row["asset_class"]),
            "region": row["region"],
            "region_label": REGION_LABELS.get(row["region"], row["region"]),
            "style": row["style"],
            "style_label": STYLE_LABELS.get(row["style"], row["style"]),
            "currency": row["currency"],
        } for row in rows],
    }


def _group_weights(rows, field):
    weights = {}
    for row in rows:
        key = row[field]
        weights[key] = weights.get(key, 0.0) + row["weight"]
    return weights


def _dim(key, name, weight, score, comment):
    return {"key": key, "name": name, "weight": weight,
            "score": round(_clamp(score), 1), "comment": comment}


def _issue(severity, title, detail):
    return {"severity": severity, "title": title, "detail": detail}


def _clamp(value):
    return max(0.0, min(100.0, float(value)))


def _level(score):
    if score >= 80:
        return "稳健"
    if score >= 65:
        return "较均衡"
    if score >= 50:
        return "偏集中"
    if score >= 35:
        return "需调整"
    return "高风险"


def _dedupe(items):
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _base_name(name):
    text = re.sub(r"[（(]QDII[）)]", "", str(name))
    text = re.sub(r"(发起式|联接|六个月持有期|灵活配置|混合|股票|指数)", "", text)
    text = re.sub(r"[AC]$", "", text.strip())
    return re.sub(r"\s+", "", text)
