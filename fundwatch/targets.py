"""Suggested target weights based on the user's actual holdings.

The model keeps the user's current style (proportions inside each role) but
corrects the structural issues: duplicate A/C share classes, tiny positions,
over-concentration in one style, and a missing defensive buffer.

Roles and their default budgets can be overridden via
`portfolio.target_model` in config.json.
"""
from .diagnosis import _base_name, infer_profile

DEFAULT_MODEL = {
    "tech": 0.40,
    "dividend": 0.10,
    "defensive": 0.20,
    "gold": 0.08,
    "emerging": 0.10,
    "balanced": 0.12,
}
ROLE_LABELS = {
    "tech": "科技成长",
    "dividend": "红利/价值",
    "defensive": "防守（债/货币）",
    "gold": "黄金",
    "emerging": "海外新兴",
    "balanced": "均衡/其他",
}
MIN_POSITION = 0.015


def role_for(profile, fund):
    asset = profile.get("asset_class")
    style = profile.get("style")
    name = str(fund.get("name") or "")
    if asset in ("bond", "cash"):
        return "defensive"
    if asset == "gold":
        return "gold"
    if style == "tech":
        return "tech"
    if style == "value" or "红利" in name:
        return "dividend"
    if asset == "mixed" and profile.get("region") == "overseas":
        return "emerging"
    return "balanced"


def suggest_targets(funds, portfolio, cfg):
    if not funds:
        return {"model": "均衡型", "summary": "还没有基金，无法生成目标。",
                "rows": [], "role_targets": {}, "total": 0.0}

    total = float(portfolio.get("total_value") or 0.0)
    rows = []
    for fund in funds:
        weight = float(fund.get("weight") or 0.0)
        if not weight and total:
            weight = float(fund.get("market_value") or 0.0) / total
        profile = infer_profile(fund)
        role = role_for(profile, fund)
        rows.append({
            "code": str(fund.get("code") or ""),
            "name": str(fund.get("name") or ""),
            "weight": weight,
            "current_target": float(fund.get("target_weight_raw") or
                                    fund.get("target_weight") or 0.0),
            "role": role,
            "role_label": ROLE_LABELS.get(role, role),
            "profile": profile,
            "duplicate_of": "",
            "tiny": False,
        })

    weight_sum = sum(row["weight"] for row in rows) or 1.0
    for row in rows:
        row["weight"] = row["weight"] / weight_sum

    groups = {}
    for row in rows:
        groups.setdefault(_base_name(row["name"]), []).append(row)
    for group in groups.values():
        if len(group) < 2:
            continue
        keeper = max(group, key=lambda item: item["weight"])
        for row in group:
            if row is not keeper:
                row["duplicate_of"] = keeper["code"]

    for row in rows:
        row["tiny"] = (not row["duplicate_of"]) and row["weight"] < MIN_POSITION

    active = [row for row in rows if not row["duplicate_of"] and not row["tiny"]]
    if not active:
        active = rows

    model = dict(DEFAULT_MODEL)
    override = (cfg.get("portfolio") or {}).get("target_model") or {}
    for role, value in override.items():
        try:
            model[role] = float(value)
        except (TypeError, ValueError):
            continue

    active_roles = []
    for row in active:
        if row["role"] not in active_roles:
            active_roles.append(row["role"])
    role_budget_sum = sum(max(0.0, model.get(role, 0.10)) for role in active_roles) or 1.0
    budgets = {role: max(0.0, model.get(role, 0.10)) / role_budget_sum
               for role in active_roles}

    for role in active_roles:
        role_rows = [row for row in active if row["role"] == role]
        role_weight = sum(row["weight"] for row in role_rows) or 1.0
        for row in role_rows:
            row["suggested"] = budgets[role] * row["weight"] / role_weight

    # cap single fund at the portfolio limit (default 65%), not a fixed 25%
    try:
        max_single = float((cfg.get("portfolio") or {}).get("max_single_weight", 0.65))
    except (TypeError, ValueError):
        max_single = 0.65
    max_single = min(1.0, max(0.20, max_single))
    for _ in range(4):
        excess = 0.0
        for row in active:
            if row["suggested"] > max_single:
                excess += row["suggested"] - max_single
                row["suggested"] = max_single
        if excess <= 1e-9:
            break
        receivers = [row for row in active if row["suggested"] < max_single]
        if not receivers:
            break
        room = sum(max_single - row["suggested"] for row in receivers)
        for row in receivers:
            row["suggested"] += excess * (max_single - row["suggested"]) / room

    # normalize + round
    for row in rows:
        row.setdefault("suggested", 0.0)
    suggested_sum = sum(row["suggested"] for row in active) or 1.0
    for row in active:
        row["suggested"] = row["suggested"] / suggested_sum
    for row in active:
        row["suggested"] = round(row["suggested"], 4)
    residual = round(1.0 - sum(row["suggested"] for row in active), 4)
    if active and residual:
        largest = max(active, key=lambda item: item["suggested"])
        largest["suggested"] = round(largest["suggested"] + residual, 4)

    # reasons
    role_targets = {role: round(budgets[role], 4) for role in active_roles}
    for row in rows:
        if row["duplicate_of"]:
            row["reason"] = "与 %s 为同一基金的 A/C 份额，建议合并" % row["duplicate_of"]
        elif row["tiny"]:
            row["reason"] = "当前仓位 %.1f%% 过小，建议清仓或并入同类" % (row["weight"] * 100)
        elif row["suggested"] <= 0:
            row["reason"] = "建议清仓"
        elif row["role"] == "defensive":
            row["reason"] = "补足防守资产到 %.0f%%" % (role_targets.get("defensive", 0) * 100)
        elif row["role"] == "gold":
            row["reason"] = "黄金目标 %.0f%%，控制单一避险资产比例" % (
                role_targets.get("gold", 0) * 100)
        elif row["role"] == "tech":
            row["reason"] = "科技成长目标 %.0f%%，降低波动" % (
                role_targets.get("tech", 0) * 100)
        elif row["role"] == "dividend":
            row["reason"] = "红利/价值目标 %.0f%%，增加现金流与低波资产" % (
                role_targets.get("dividend", 0) * 100)
        elif row["role"] == "emerging":
            row["reason"] = "海外新兴目标 %.0f%%，控制单一市场风险" % (
                role_targets.get("emerging", 0) * 100)
        else:
            row["reason"] = "按同类内当前比例分配"

    order = []
    for row in rows:
        row["change_pp"] = round((row["suggested"] - row["weight"]) * 100, 2)
        order.append(row)
    order.sort(key=lambda item: (-item["suggested"], item["code"]))

    summary_parts = []
    for role in active_roles:
        summary_parts.append("%s %.0f%%" % (
            ROLE_LABELS.get(role, role), role_targets.get(role, 0) * 100))
    summary = "建议采用均衡型目标：" + "、".join(summary_parts) + "。"

    return {
        "model": "均衡型",
        "summary": summary,
        "rows": [{
            "code": row["code"], "name": row["name"],
            "role": row["role"], "role_label": row["role_label"],
            "weight": round(row["weight"], 4),
            "current_target": round(row["current_target"], 4),
            "suggested": round(row["suggested"], 4),
            "change_pp": row["change_pp"],
            "reason": row["reason"],
        } for row in order],
        "role_targets": role_targets,
        "total": round(sum(row["suggested"] for row in active), 4),
    }
