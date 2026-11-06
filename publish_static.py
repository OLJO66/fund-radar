#!/usr/bin/env python
"""Build a privacy-safe static page for free hosting (GitHub Pages).

Amounts, shares and cost basis are removed; weights, advice, diagnosis and
target suggestions stay.  The local report/email keeps the full details.
"""
import copy
import json
import re
import sys
from pathlib import Path

from fundwatch import report
from fundwatch.util import today_str

ROOT = Path(__file__).resolve().parent
LATEST = ROOT / "reports" / "latest.json"
PUBLIC = ROOT / "public"

SENSITIVE_FUND_KEYS = (
    "market_value", "cost", "cost_total", "pnl", "pnl_pct",
    "shares", "amount", "suggest_amount",
)
MONEY_RE = re.compile(r"[\d,]+\.\d{2}\s*元")


def scrub(text):
    return MONEY_RE.sub("XX 元", str(text))


def main():
    if not LATEST.exists():
        print("没有找到 reports/latest.json，跳过发布（可能是休市日或数据抓取失败）。")
        return 0
    data = json.loads(LATEST.read_text(encoding="utf-8"))
    if data.get("date") != today_str():
        print("最新报告不是今天的（%s），跳过发布。" % data.get("date"))
        return 0

    public = copy.deepcopy(data)
    for fund in public.get("funds", []):
        for key in SENSITIVE_FUND_KEYS:
            if key in fund:
                fund[key] = 0 if key == "suggest_amount" else None
        fund["reasons"] = [scrub(item) for item in fund.get("reasons", [])]
        fund["hysteresis_note"] = scrub(fund.get("hysteresis_note", ""))
    portfolio = public.get("portfolio") or {}
    for key in ("total_value", "total_cost", "total_profit", "profit_pct"):
        if key in portfolio:
            portfolio[key] = None
    portfolio["warning"] = scrub(portfolio.get("warning", ""))
    diagnosis = public.get("diagnosis") or {}
    for issue in diagnosis.get("issues", []):
        issue["detail"] = scrub(issue.get("detail", ""))
    diagnosis["suggestions"] = [scrub(item) for item in diagnosis.get("suggestions", [])]
    diagnosis["positives"] = [scrub(item) for item in diagnosis.get("positives", [])]
    suggestion = public.get("target_suggestion") or {}
    for row in suggestion.get("rows", []):
        row["reason"] = scrub(row.get("reason", ""))
    public["push"] = []
    public["summary"] = scrub(report.summary_text(public))

    page = report.to_html(public, include_amounts=False)
    banner = ('<div style="background:#eaf2f8;color:#21618c;padding:10px 14px;'
              'border-radius:8px;margin-bottom:14px;font-size:13px">'
              "公开静态页 · 金额/份额/成本已隐藏 · 每个交易日 14:20 自动更新"
              "</div>")
    page = page.replace('<div class="wrap">', '<div class="wrap">' + banner, 1)
    page = page.replace('href="http://127.0.0.1:8765"', 'href="#"')
    page = page.replace("打开管理面板 → 添加/删除基金、修改持仓和收益",
                        "公开页仅查看；编辑持仓请使用本地/云端管理面板")
    page = page.replace("管理面板：http://127.0.0.1:8765（添加/删除基金、修改持仓和收益）", "")
    page = page.replace("可在管理面板中一键应用；应用后每日建议会按新目标做再平衡。",
                        "公开页仅查看；编辑请使用本地/云端管理面板。")

    PUBLIC.mkdir(exist_ok=True)
    (PUBLIC / "index.html").write_text(page, encoding="utf-8")
    (PUBLIC / ".nojekyll").write_text("", encoding="utf-8")
    print("已生成 public/index.html（金额已隐藏）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
