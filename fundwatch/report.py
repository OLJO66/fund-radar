"""Report rendering: JSON snapshot, Markdown summary and a standalone HTML page."""
import html
import json
import logging
from pathlib import Path

from .diagnosis import diagnose
from .targets import suggest_targets
from .util import fmt_money, fmt_nav, fmt_pct, now_cn

log = logging.getLogger("fundwatch.report")

DISCLAIMER = (
    "本报告由本地规则程序自动生成，仅用于个人记录与辅助决策，不构成投资建议。"
    "基金净值与盘中估值可能存在偏差，实际以基金公司公布净值为准；请结合自身风险承受能力操作。"
)

ACTION_COLOR = {
    "加仓": "#c0392b",
    "减仓": "#1e8449",
    "持有": "#2471a3",
    "观望": "#7f8c8d",
}


def build(market, funds, signals, cfg, today, portfolio=None):
    market["commentary"] = market_commentary(market)
    rows = []
    for fund in funds:
        sig = signals.get(fund["code"], {})
        merged = dict(fund)
        merged.update({
            "score": sig.get("score"),
            "components": sig.get("components"),
            "action": sig.get("action"),
            "trend_action": sig.get("trend_action"),
            "trend_amount": sig.get("trend_amount"),
            "trend_reasons": sig.get("trend_reasons", []),
            "raw_action": sig.get("raw_action"),
            "suggest_amount": sig.get("amount", 0.0),
            "reasons": sig.get("reasons", []),
            "hysteresis_note": sig.get("hysteresis_note", ""),
            "hard_exit": sig.get("hard_exit", False),
        })
        rows.append(merged)
    report = {
        "date": today,
        "generated_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "market": market,
        "portfolio": portfolio or {},
        "funds": rows,
        "risk": cfg.get("risk", {}),
        "disclaimer": DISCLAIMER,
    }
    report["diagnosis"] = diagnose(rows, portfolio or {}, cfg) if portfolio else {}
    report["target_suggestion"] = suggest_targets(rows, portfolio or {}, cfg) if portfolio else {}
    report["summary"] = summary_text(report)
    return report


def market_commentary(market):
    rows = [row for row in market.get("indices", [])
            if row.get("ma20") and row.get("ma60") and row.get("ret20") is not None]
    if not rows:
        return "指数数据不足，暂不给出大盘判断。"
    total = len(rows)
    above20 = sum(1 for row in rows if row["price"] > row["ma20"])
    above60 = sum(1 for row in rows if row["price"] > row["ma60"])
    avg20 = sum(row["ret20"] for row in rows) / total
    avg5 = sum(row["ret5"] for row in rows if row["ret5"] is not None) / max(
        1, sum(1 for row in rows if row["ret5"] is not None))
    if above20 * 2 <= total and above60 * 2 <= total:
        trend = "主要指数仍在 MA20/MA60 下方，中期趋势偏弱"
    elif above20 * 2 >= total:
        trend = "多数指数站上 MA20，短期趋势转强"
    else:
        trend = "指数分化，趋势方向不明"
    if avg5 > 1.0 and avg20 < 0:
        momentum = "近 5 日出现反弹，但 20 日收益仍为负，暂按弱势反弹对待，追高需谨慎"
    elif avg5 < -1.0 and avg20 < 0:
        momentum = "近 5 日继续走弱，20 日趋势向下，防守优先"
    elif avg20 > 0 and avg5 > 0:
        momentum = "短中期动量同向向上，可顺势持有"
    else:
        momentum = "短期动量与中期趋势不一致，控制仓位、等待确认"
    return "%s；%d/%d 个指数站上 MA20，%d/%d 站上 MA60；20 日平均 %+.1f%%，5 日平均 %+.1f%%。%s。" % (
        trend, above20, total, above60, total, avg20, avg5, momentum)


def summary_text(report):
    market = report["market"]
    hs300 = _index(report, "sh000300")
    star50 = _index(report, "sh000688")
    nq = market.get("nq") or {}
    parts = ["【基金雷达 %s】" % report["date"][5:].replace("-", "-")]
    headline = "大盘%s（%.0f 分）" % (market.get("regime", "--"), market.get("score") or 0)
    extras = []
    if hs300:
        extras.append("沪深300 %s" % fmt_pct(hs300.get("change_pct")))
    if star50:
        extras.append("科创50 %s" % fmt_pct(star50.get("change_pct")))
    if nq:
        extras.append("纳指期货 %s" % fmt_pct(nq.get("change_pct")))
    if extras:
        headline += "：" + "，".join(extras)
    parts.append(headline)
    for fund in report["funds"]:
        action = fund.get("action") or "持有"
        amount = fund.get("suggest_amount") or 0
        suffix = ("，约 %s" % fmt_money(amount)) if amount else ""
        label = "估算" if fund.get("is_estimate") else "净值"
        weight = fund.get("weight")
        weight_text = ("（占比 %.0f%%）" % (weight * 100)) if weight is not None else ""
        if fund.get("est_chg") is None:
            value_text = "净值 %s（最近）" % fmt_nav(fund.get("est_nav"))
        else:
            value_text = "%s %s" % (label, fmt_pct(fund.get("est_chg")))
        parts.append("%s%s %s，建议%s%s" % (
            fund["code"], weight_text, value_text, action, suffix))
    diag = report.get("diagnosis") or {}
    if diag:
        parts.append("配置诊断 %.0f 分（%s）" % (diag.get("score", 0), diag.get("level", "")))
    parts.append("请在 14:55 前操作")
    return "｜".join(parts)


def write(report, reports_dir, date_prefix=None):
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    day = date_prefix or report["date"]
    paths = {
        "json": reports_dir / ("%s.json" % day),
        "md": reports_dir / ("%s.md" % day),
        "html": reports_dir / ("%s.html" % day),
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    paths["json"].write_text(payload, encoding="utf-8")
    paths["md"].write_text(to_markdown(report), encoding="utf-8")
    paths["html"].write_text(to_html(report), encoding="utf-8")
    latest_json = reports_dir / "latest.json"
    latest_md = reports_dir / "latest.md"
    latest_json.write_text(payload, encoding="utf-8")
    latest_md.write_text(to_markdown(report), encoding="utf-8")
    paths["latest_json"] = latest_json
    paths["latest_md"] = latest_md
    return paths


def to_markdown(report):
    lines = []
    market = report["market"]
    lines.append("# 基金雷达 · %s" % report["date"])
    lines.append("")
    lines.append("生成时间：%s ｜ 大盘状态：**%s**（%.0f/100）" % (
        report["generated_at"], market.get("regime", "--"), market.get("score") or 0))
    lines.append("")
    lines.append("## 大盘")
    lines.append("")
    lines.append("| 指数 | 点位 | 涨跌 | MA20 | MA60 | 20日 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for row in market.get("indices", []):
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            row.get("name"), _num(row.get("price"), 2), fmt_pct(row.get("change_pct")),
            _num(row.get("ma20"), 2), _num(row.get("ma60"), 2), fmt_pct(row.get("ret20"))))
    lines.append("")
    if market.get("ndx") or market.get("nq") or market.get("fx"):
        lines.append("纳指 %s（%s）｜纳指期货 %s（%s）｜USD/CNY %s（%s）" % (
            _num((market.get("ndx") or {}).get("price"), 2),
            fmt_pct((market.get("ndx") or {}).get("change_pct")),
            _num((market.get("nq") or {}).get("price"), 2),
            fmt_pct((market.get("nq") or {}).get("change_pct")),
            _num((market.get("fx") or {}).get("price"), 4),
            fmt_pct((market.get("fx") or {}).get("change_pct"))))
        lines.append("")
    if market.get("commentary"):
        lines.append("**大盘解读**：%s" % market["commentary"])
        lines.append("")
    portfolio = report.get("portfolio") or {}
    if portfolio:
        lines.append("## 组合")
        lines.append("")
        lines.append("总市值：%s ｜ 总成本：%s ｜ 总收益：**%s**（%s）" % (
            fmt_money(portfolio.get("total_value")), fmt_money(portfolio.get("total_cost")),
            fmt_money(portfolio.get("total_profit")), fmt_pct(portfolio.get("profit_pct"))))
        for fund in report["funds"]:
            if fund.get("weight") is None:
                continue
            lines.append("- %s %s：占比 %.1f%%，目标 %.1f%%" % (
                fund["code"], fund.get("name", ""), (fund.get("weight") or 0) * 100,
                (fund.get("target_weight") or 0) * 100))
        if portfolio.get("warning"):
            lines.append("")
            lines.append("> ⚠️ %s" % portfolio["warning"])
        lines.append("")
    diag = report.get("diagnosis") or {}
    if diag:
        lines.append("## 配置诊断：%.0f/100（%s）" % (
            diag.get("score", 0), diag.get("level", "")))
        lines.append("")
        lines.append(diag.get("summary", ""))
        lines.append("")
        lines.append("| 维度 | 得分 | 说明 |")
        lines.append("|---|---:|---|")
        for dim in diag.get("dimensions", []):
            lines.append("| %s | %.0f | %s |" % (
                dim.get("name"), dim.get("score", 0), dim.get("comment", "")))
        lines.append("")
        severity = {"high": "高", "medium": "中", "low": "低"}
        if diag.get("issues"):
            lines.append("**主要问题**")
            for issue in diag["issues"]:
                lines.append("- 【%s】%s：%s" % (
                    severity.get(issue.get("severity"), issue.get("severity")),
                    issue.get("title"), issue.get("detail")))
            lines.append("")
        if diag.get("suggestions"):
            lines.append("**配置建议**")
            for suggestion in diag["suggestions"]:
                lines.append("- %s" % suggestion)
            lines.append("")
        if diag.get("positives"):
            lines.append("**做得好的地方**")
            for positive in diag["positives"]:
                lines.append("- %s" % positive)
            lines.append("")
    target_sugg = report.get("target_suggestion") or {}
    if target_sugg:
        lines.append("## 建议目标占比（%s）" % target_sugg.get("model", "均衡型"))
        lines.append("")
        lines.append(target_sugg.get("summary", ""))
        lines.append("")
        lines.append("| 基金 | 当前 | 当前目标 | 建议目标 | 调整理由 |")
        lines.append("|---|---:|---:|---:|---|")
        for row in target_sugg.get("rows", []):
            name = str(row.get("name", "")).replace("|", "/")
            lines.append("| %s %s | %.1f%% | %.1f%% | %.1f%% | %s |" % (
                row.get("code"), name, (row.get("weight") or 0) * 100,
                (row.get("current_target") or 0) * 100,
                (row.get("suggested") or 0) * 100, row.get("reason", "")))
        lines.append("")
    for fund in report["funds"]:
        lines.append("## %s %s" % (fund["code"], fund["name"]))
        lines.append("")
        lines.append("- 估值：**%s**（%s），来源：%s" % (
            fmt_nav(fund.get("est_nav")), fmt_pct(fund.get("est_chg")),
            fund.get("est_source", "--")))
        lines.append("- 持仓：市值 %s，成本价 %s，盈亏 **%s**（%s）" % (
            fmt_money(fund.get("market_value")), fmt_nav(fund.get("cost")),
            fmt_money(fund.get("pnl")), fmt_pct(fund.get("pnl_pct"))))
        if fund.get("weight") is not None:
            lines.append("- 占比：%.1f%%（目标 %.1f%%）" % (
                fund["weight"] * 100, (fund.get("target_weight") or 0) * 100))
        lines.append("- 评分：%.0f/100 ｜ 建议：**%s** %s" % (
            fund.get("score") or 0, fund.get("action", "--"),
            ("约 %s" % fmt_money(fund.get("suggest_amount"))) if fund.get("suggest_amount") else ""))
        if fund.get("hysteresis_note"):
            lines.append("- 提示：%s" % fund["hysteresis_note"])
        for reason in fund.get("reasons", []):
            lines.append("- %s" % reason)
        lines.append("")
    lines.append("---")
    lines.append(report.get("disclaimer", ""))
    lines.append("")
    lines.append("管理面板：http://127.0.0.1:8765（添加/删除基金、修改持仓和收益）")
    return "\n".join(lines) + "\n"


def to_html(report, include_amounts=True):
    market = report["market"]
    fund_cards = "\n".join(_fund_card(fund, include_amounts) for fund in report["funds"])
    index_cards = "\n".join(_index_card(row) for row in market.get("indices", []))
    ndx = market.get("ndx") or {}
    nq = market.get("nq") or {}
    fx = market.get("fx") or {}
    ndx_line = "纳指 %s（%s） ｜ 纳指期货 %s（%s） ｜ USD/CNY %s（%s）" % (
        _num(ndx.get("price"), 2), fmt_pct(ndx.get("change_pct")),
        _num(nq.get("price"), 2), fmt_pct(nq.get("change_pct")),
        _num(fx.get("price"), 4), fmt_pct(fx.get("change_pct")))
    regime = market.get("regime", "--")
    regime_color = {"强势": "#c0392b", "偏强": "#d35400", "震荡": "#b7950b",
                    "偏弱": "#2e86c1", "弱势": "#1e8449"}.get(regime, "#7f8c8d")
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>基金雷达 · {date}</title>
<style>
body {{ margin:0; padding:24px; background:#f4f6f8; color:#1c2833;
  font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif; }}
* {{ box-sizing:border-box; }}
html, body {{ max-width:100%; overflow-x:hidden; }}
.wrap {{ width:100%; max-width:980px; margin:0 auto; }}
h1 {{ font-size:22px; margin:0 0 6px; }}
.meta {{ color:#6b7c8c; font-size:13px; margin-bottom:18px; }}
.regime {{ display:inline-block; padding:2px 10px; border-radius:12px; color:#fff;
  background:{regime_color}; font-size:13px; margin-left:8px; vertical-align:middle; }}
.panel {{ background:#fff; border-radius:10px; padding:18px 20px; margin-bottom:18px;
  box-shadow:0 1px 3px rgba(0,0,0,.06); }}
.panel h2 {{ font-size:15px; margin:0 0 12px; color:#34495e; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; }}
.grid > *, .kv > *, .diag-grid > *, .panel {{ min-width:0; }}
.meta, .comment, .legend, .reasons, .foot, .warn, .idx .sub,
.diag-issue, .diag-sugg li {{ overflow-wrap:anywhere; word-break:break-word; }}
.idx {{ background:#fafbfc; border:1px solid #eef1f4; border-radius:8px; padding:10px 12px; }}
.idx .name {{ font-size:13px; color:#5d6d7e; }}
.idx .price {{ font-size:18px; font-weight:600; margin:2px 0; }}
.idx .sub {{ font-size:12px; color:#7f8c8d; }}
.up {{ color:#c0392b; }} .down {{ color:#1e8449; }} .flat {{ color:#7f8c8d; }}
.fund {{ border-left:4px solid #ccc; }}
.fund .head {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; }}
.fund .title {{ font-size:17px; font-weight:600; }}
.fund .code {{ color:#7f8c8d; font-weight:400; font-size:13px; margin-left:6px; }}
.badge {{ color:#fff; padding:3px 12px; border-radius:12px; font-size:14px; }}
.kv {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:8px; margin:14px 0; }}
.kv div {{ background:#fafbfc; border-radius:8px; padding:8px 12px; font-size:13px; }}
.kv b {{ display:block; font-size:16px; margin-top:3px; }}
ul {{ margin:8px 0 0 18px; padding:0; font-size:13px; line-height:1.8; color:#34495e; }}
.note {{ color:#b7950b; font-size:12px; }}
.comment {{ color:#2c3e50; font-size:13px; line-height:1.8; margin:12px 0 0;
  padding:10px 12px; background:#f8f9fa; border-radius:6px; }}
.allocbar {{ display:flex; height:14px; border-radius:7px; overflow:hidden;
  background:#eef1f4; margin:12px 0 8px; }}
.allocbar span {{ display:block; height:100%; }}
.legend {{ font-size:12px; color:#5d6d7e; line-height:1.9; }}
.legend i {{ display:inline-block; width:9px; height:9px; border-radius:2px;
  margin-right:5px; vertical-align:middle; }}
.warn {{ color:#b9770e; font-size:13px; margin-top:8px; }}
.diag-head {{ display:flex; justify-content:space-between; align-items:center; gap:12px; }}
.diag-head h2 {{ margin:0; font-size:15px; }}
.diag-level {{ padding:2px 10px; border-radius:12px; color:#fff; font-size:12px;
  margin-left:6px; vertical-align:middle; }}
.diag-score {{ font-size:34px; font-weight:700; line-height:1; }}
.diag-score span {{ font-size:13px; color:#95a5a6; font-weight:400; }}
.diag-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:14px; }}
.diag-dims .row {{ margin-bottom:8px; }}
.diag-dims .top {{ display:flex; justify-content:space-between; font-size:12px; color:#5d6d7e; }}
.bar {{ height:6px; background:#eef1f4; border-radius:3px; margin-top:3px; overflow:hidden; }}
.bar i {{ display:block; height:100%; border-radius:3px; background:#2e86c1; }}
.issue {{ font-size:12px; line-height:1.7; margin-bottom:8px; color:#34495e; }}
.issue b {{ display:block; }}
.issue.high b {{ color:#b03a2e; }}
.issue.medium b {{ color:#b9770e; }}
.issue.low b {{ color:#5d6d7e; }}
.diag-sugg {{ margin-top:12px; background:#f8f9fa; border-radius:8px; padding:10px 14px; }}
.diag-sugg h3 {{ font-size:12px; margin:0 0 6px; color:#5d6d7e; }}
.diag-sugg ul {{ margin:0 0 0 16px; font-size:12px; line-height:1.8; }}
.pos {{ font-size:12px; color:#1e8449; margin-top:8px; }}
.target-table {{ width:100%; min-width:560px; border-collapse:collapse; font-size:12px; margin-top:10px; }}
.target-table th, .target-table td {{ padding:6px 8px; border-bottom:1px solid #eef1f4; text-align:right; }}
.target-table th:first-child, .target-table td:first-child {{ text-align:left; }}
.target-table th {{ color:#6b7c8c; font-weight:500; background:#fafbfc; }}
.target-table .zero {{ color:#b03a2e; }}
.target-table .up {{ color:#c0392b; }} .target-table .down {{ color:#1e8449; }}
.table-wrap {{ overflow-x:auto; -webkit-overflow-scrolling:touch; }}
.foot {{ color:#95a5a6; font-size:12px; line-height:1.7; }}
.foot a {{ color:#2e86c1; text-decoration:none; }}
.spark {{ margin-top:8px; }}
@media (max-width:640px) {{
  body {{ padding:12px 10px 24px; }}
  .grid {{ grid-template-columns:repeat(2, minmax(0, 1fr)); gap:8px; }}
  .idx {{ padding:8px 9px; }}
  .idx .price {{ font-size:16px; }}
  .diag-grid {{ grid-template-columns:1fr; gap:10px; }}
  .diag-head {{ align-items:flex-start; }}
  .diag-score {{ font-size:28px; }}
  .kv {{ grid-template-columns:repeat(2, minmax(0, 1fr)); gap:6px; }}
  .meta, .comment, .legend, .reasons, .foot, .warn {{ overflow-wrap:anywhere; }}
  .target-table {{ font-size:11px; }}
  .target-table th, .target-table td {{ padding:5px 6px; }}
}}
@media (max-width:480px) {{
  .grid {{ grid-template-columns:1fr; }}
  .kv {{ grid-template-columns:1fr; }}
}}
</style></head><body><div class="wrap">
<h1>基金雷达 <span class="regime">{regime}</span></h1>
<div class="meta">{date} ｜ 生成于 {generated} ｜ 大盘评分 {score:.0f}/100</div>
<div class="panel"><h2>大盘</h2><div class="grid">{index_cards}</div>
<div class="meta" style="margin:12px 0 0">{ndx_line}</div>
<div class="comment">{commentary}</div></div>
{portfolio_panel}
{diagnosis_panel}
{target_panel}
{fund_cards}
<div class="panel"><h2>说明</h2><div class="foot">{disclaimer}<br>
盘中估值来自腾讯/天天基金，或用标的指数、纳指期货与汇率代理计算，仅供 15:00 前决策参考；
QDII 基金 15:00 前下单按当晚美股收盘净值确认。<br>
<a href="http://127.0.0.1:8765" target="_blank" rel="noopener">打开管理面板 → 添加/删除基金、修改持仓和收益</a></div></div>
</div></body></html>""".format(
        date=report["date"], generated=report["generated_at"], regime=regime,
        regime_color=regime_color, score=market.get("score") or 0,
        index_cards=index_cards, ndx_line=html.escape(ndx_line),
        commentary=html.escape(market.get("commentary", "")),
        portfolio_panel=_portfolio_panel(report, include_amounts),
        diagnosis_panel=_diagnosis_panel(report),
        target_panel=_target_panel(report),
        fund_cards=fund_cards, disclaimer=html.escape(report.get("disclaimer", "")))


def _target_panel(report):
    suggestion = report.get("target_suggestion") or {}
    if not suggestion:
        return ""
    rows = []
    for row in suggestion.get("rows", []):
        suggested = float(row.get("suggested") or 0)
        cls = "zero" if suggested <= 0 else ""
        rows.append(
            "<tr><td>%s %s</td><td>%.1f%%</td><td>%.1f%%</td>"
            '<td class="%s">%.1f%%</td><td>%s</td></tr>' % (
                html.escape(str(row.get("code", ""))),
                html.escape(str(row.get("name", ""))),
                float(row.get("weight") or 0) * 100,
                float(row.get("current_target") or 0) * 100,
                cls, suggested * 100,
                html.escape(str(row.get("reason", "")))))
    return """<div class="panel"><h2>建议目标占比
 <span class="meta">%s</span></h2>
<div class="comment">%s</div>
<div class="table-wrap"><table class="target-table"><thead><tr><th>基金</th><th>当前占比</th>
<th>当前目标</th><th>建议目标</th><th>调整理由</th></tr></thead>
<tbody>%s</tbody></table></div>
<div class="meta" style="margin-top:8px">可在管理面板中一键应用；应用后每日建议会按新目标做再平衡。</div>
</div>""" % (
        html.escape(str(suggestion.get("model", "均衡型"))),
        html.escape(str(suggestion.get("summary", ""))), "".join(rows))


def _diagnosis_panel(report):
    diag = report.get("diagnosis") or {}
    if not diag:
        return ""
    level = diag.get("level", "")
    colors = {"稳健": "#1e8449", "较均衡": "#2e86c1", "偏集中": "#b9770e",
              "需调整": "#d35400", "高风险": "#c0392b"}
    color = colors.get(level, "#7f8c8d")
    dims = []
    for dim in diag.get("dimensions", []):
        score = float(dim.get("score") or 0)
        dims.append(
            '<div class="row"><div class="top"><span>%s</span><span>%.0f/100</span></div>'
            '<div class="bar"><i style="width:%.0f%%"></i></div>'
            '<div class="meta">%s</div></div>' % (
                html.escape(str(dim.get("name", ""))), score, score,
                html.escape(str(dim.get("comment", "")))))
    issues = []
    for issue in diag.get("issues", []):
        issues.append('<div class="issue %s"><b>%s</b>%s</div>' % (
            html.escape(str(issue.get("severity", ""))),
            html.escape(str(issue.get("title", ""))),
            html.escape(str(issue.get("detail", "")))))
    suggestions = "".join("<li>%s</li>" % html.escape(str(item))
                          for item in diag.get("suggestions", []))
    positives = "".join("<div>✓ %s</div>" % html.escape(str(item))
                        for item in diag.get("positives", []))
    positives_html = '<div class="pos">%s</div>' % positives if positives else ""
    return """<div class="panel">
<div class="diag-head"><div><h2>配置诊断 <span class="diag-level"
 style="background:%s">%s</span></h2><div class="meta">%s</div></div>
<div class="diag-score" style="color:%s">%.0f<span>/100</span></div></div>
<div class="diag-grid"><div class="diag-dims">%s</div>
<div class="diag-issues">%s</div></div>
<div class="diag-sugg"><h3>配置建议</h3><ul>%s</ul>%s</div></div>""" % (
        color, html.escape(level), html.escape(str(diag.get("summary", ""))),
        color, float(diag.get("score") or 0), "".join(dims), "".join(issues),
        suggestions, positives_html)


def _portfolio_panel(report, include_amounts=True):
    p = report.get("portfolio") or {}
    funds = [fund for fund in report["funds"] if fund.get("weight") is not None]
    if not p or not funds:
        return ""
    palette = ["#c0392b", "#2e86c1", "#8e44ad", "#d35400", "#16a085", "#7f8c8d"]
    segments = []
    legend = []
    for idx, fund in enumerate(funds):
        color = palette[idx % len(palette)]
        weight = (fund.get("weight") or 0) * 100
        segments.append('<span style="width:%.2f%%;background:%s" title="%s %.1f%%"></span>' % (
            weight, color, html.escape(str(fund.get("name", ""))), weight))
        legend.append('<div><i style="background:%s"></i>%s %s：%.1f%%（目标 %.1f%%）</div>' % (
            color, html.escape(str(fund.get("code", ""))),
            html.escape(str(fund.get("name", ""))), weight,
            (fund.get("target_weight") or 0) * 100))
    warning = ""
    if p.get("warning"):
        warning = '<div class="warn">⚠️ %s</div>' % html.escape(p["warning"])
    if include_amounts:
        head = '<div class="meta">总市值 <b>%s</b> ｜ 总成本 %s ｜ 总收益 <b>%s</b>（%s）</div>' % (
            fmt_money(p.get("total_value")), fmt_money(p.get("total_cost")),
            fmt_money(p.get("total_profit")), fmt_pct(p.get("profit_pct")))
    else:
        head = ('<div class="meta">公开页面：金额 / 份额 / 成本已隐藏，'
                '仅显示占比、建议和诊断。</div>')
    return """<div class="panel"><h2>组合</h2>
%s
<div class="allocbar">%s</div>
<div class="legend">%s</div>%s</div>""" % (
        head, "".join(segments), "".join(legend), warning)


def _index_card(row):
    chg = row.get("change_pct")
    cls = "up" if (chg or 0) > 0 else ("down" if (chg or 0) < 0 else "flat")
    return ('<div class="idx"><div class="name">%s</div>'
            '<div class="price %s">%s</div>'
            '<div class="sub">%s ｜ MA20 %s ｜ 20日 %s</div></div>') % (
        html.escape(str(row.get("name", ""))), cls, fmt_pct(chg),
        _num(row.get("price"), 2), _num(row.get("ma20"), 2), fmt_pct(row.get("ret20")))


def _fund_card(fund, include_amounts=True):
    action = fund.get("action") or "持有"
    color = ACTION_COLOR.get(action, "#7f8c8d")
    reasons = "".join("<li>%s</li>" % html.escape(str(r)) for r in fund.get("reasons", []))
    note = ""
    if fund.get("hysteresis_note"):
        note = '<div class="note">%s</div>' % html.escape(fund["hysteresis_note"])
    if include_amounts and fund.get("suggest_amount"):
        amount = "约 %s" % fmt_money(fund.get("suggest_amount"))
    else:
        amount = ""
    badge = (action + (" " + amount if amount else "")).strip()
    chg = fund.get("est_chg")
    cls = "up" if (chg or 0) > 0 else ("down" if (chg or 0) < 0 else "flat")
    spark = _sparkline(fund.get("history") or [])

    kv = ['<div>盘中估算<b class="%s">%s（%s）</b></div>' % (
        cls, fmt_nav(fund.get("est_nav")), fmt_pct(chg))]
    if include_amounts:
        pnl = fund.get("pnl")
        pnlcls = "up" if (pnl or 0) > 0 else ("down" if (pnl or 0) < 0 else "flat")
        kv.append('<div>当前市值<b>%s</b></div>' % fmt_money(fund.get("market_value")))
        kv.append('<div>成本价 / 盈亏<b>%s / <span class="%s">%s（%s）</span></b></div>' % (
            fmt_nav(fund.get("cost")), pnlcls, fmt_money(pnl),
            fmt_pct(fund.get("pnl_pct"))))
    kv.append('<div>组合占比<b>%.1f%%（目标 %.1f%%）</b></div>' % (
        (fund.get("weight") or 0) * 100, (fund.get("target_weight") or 0) * 100))
    kv.append('<div>趋势评分<b>%.0f/100</b></div>' % (fund.get("score") or 0))

    return """<div class="panel fund" style="border-left-color:%s">
<div class="head"><div class="title">%s<span class="code">%s</span></div>
<span class="badge" style="background:%s">%s</span></div>
<div class="kv">%s</div>
<div class="spark">%s</div>
<div class="meta" style="margin-top:6px">估值来源：%s%s</div>
<ul>%s</ul></div>""" % (
        color, html.escape(str(fund.get("name", ""))),
        html.escape(str(fund.get("code", ""))), color, html.escape(badge),
        "".join(kv), spark, html.escape(str(fund.get("est_source", "--"))),
        note, reasons)


def _sparkline(history, width=300, height=56):
    values = [item["nav"] for item in history if item.get("nav")]
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = float(width) / (len(values) - 1)
    points = []
    for idx, value in enumerate(values):
        x = idx * step
        y = height - (value - lo) / span * (height - 8) - 4
        points.append("%.1f,%.1f" % (x, y))
    color = "#c0392b" if values[-1] >= values[0] else "#1e8449"
    return ('<svg width="%d" height="%d" viewBox="0 0 %d %d" '
            'xmlns="http://www.w3.org/2000/svg">'
            '<polyline fill="none" stroke="%s" stroke-width="1.6" points="%s"/>'
            '</svg>') % (width, height, width, height, color, " ".join(points))


def _index(report, code):
    for row in report["market"].get("indices", []):
        if row.get("code") == code:
            return row
    return None


def _num(value, digits=2):
    if value is None:
        return "--"
    return ("%." + str(digits) + "f") % value
