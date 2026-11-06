#!/usr/bin/env python
"""FundRadar entry point.

Usage:
  python run.py --once             # fetch, analyse, write report and push
  python run.py --scheduled        # same, but skip holidays / already-run days
  python run.py --print-latest     # print reports/latest.md (used by DSH reminder)
  python run.py --dry-run          # no push, no DB write
"""
import argparse
import json
import logging
import os
import sys

from fundwatch import market as market_mod
from fundwatch import notify as notify_mod
from fundwatch import report as report_mod
from fundwatch.analysis import decide, score_fund, size_amount
from fundwatch.config import (CACHE_DIR, CONFIG_PATH, DATA_DIR, LOGS_DIR, REPORTS_DIR,
                              ensure_dirs, load_config)
from fundwatch.funds import build_funds
from fundwatch.http import HttpClient
from fundwatch.portfolio import allocation_advice, build_portfolio
from fundwatch.store import Store
from fundwatch.util import now_cn, today_str

log = logging.getLogger("fundwatch")


def setup_logging():
    ensure_dirs()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.FileHandler(str(LOGS_DIR / "fundwatch.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def _fee_note(fund, last_buy_date, today):
    kind = fund.get("kind", "a_share")
    if kind == "qdii":
        base = "A 类 QDII：持有不满 7 天赎回费 1.5%，7 天后费率以基金合同为准，建议中长线持有"
    else:
        base = "C 类：持有不满 7 天赎回费 1.5%，建议满 7 天再减仓"
    if last_buy_date:
        try:
            from datetime import date

            y, m, d = [int(part) for part in str(last_buy_date).split("-")[:3]]
            ty, tm, td = [int(part) for part in today.split("-")[:3]]
            held = (date(ty, tm, td) - date(y, m, d)).days
            if held < 7:
                return "费率提示：上次买入 %s，仅持有 %d 天，现在减仓赎回费 1.5%%；%s" % (
                    last_buy_date, held, base)
        except (TypeError, ValueError):
            pass
    return "费率提示：" + base


def build_signals(store, funds, market, cfg, today, portfolio, use_store=True):
    signals = {}
    for fund in funds:
        ind = fund.get("indicators") or {}
        bench = fund.get("benchmark")
        score, components = score_fund(ind, bench)
        qdii_ctx = {"nq_pct": (market.get("nq") or {}).get("change_pct")}
        decision = decide(fund, ind, score, market.get("regime", ""),
                          fund.get("pnl_pct"), qdii_ctx)
        trend_action = decision["action"]
        base_amount = size_amount(fund, fund.get("market_value") or 0.0,
                                  decision, cfg.get("risk", {}))
        action, amount, alloc_notes = allocation_advice(
            fund, trend_action, base_amount, portfolio, cfg,
            hard_exit=decision.get("hard_exit", False))
        min_trade = float((cfg.get("risk") or {}).get("min_trade_amount", 50) or 0)
        if action in ("加仓", "减仓") and min_trade and amount < min_trade:
            alloc_notes.append("预计金额低于 %.0f 元，本次忽略" % min_trade)
            action = "观望"
            amount = 0.0

        final_action = action
        note = ""
        prev = store.last_signal(fund["code"], today) if use_store else None
        if prev:
            if action == prev.get("final_action"):
                pass
            elif action == prev.get("raw_action"):
                pass
            elif abs(score - (prev.get("score") or 0)) >= 15:
                pass
            else:
                final_action = prev.get("final_action") or action
                note = ("信号正从「%s」切向「%s」，按滞后规则需再确认一个交易日"
                        "（或评分变化 ≥15），今日暂按「%s」执行" % (
                            prev.get("final_action"), action, final_action))

        if final_action != action:
            amount = 0.0

        reasons = list(decision["reasons"]) + list(alloc_notes)
        if final_action == "减仓":
            reasons.append(_fee_note(fund, fund.get("last_buy_date"), today))

        signals[fund["code"]] = {
            "score": score,
            "components": components,
            "action": final_action,
            "trend_action": trend_action,
            "trend_amount": base_amount,
            "trend_reasons": list(decision["reasons"]),
            "raw_action": action,
            "amount": amount,
            "reasons": reasons,
            "hard_exit": decision.get("hard_exit", False),
            "hysteresis_note": note,
        }
        if use_store:
            store.save_signal(today, fund["code"], score, action, final_action,
                              amount, reasons)
    return signals


def run(args):
    today = today_str()
    report_date = args.date or today
    cfg = load_config(args.config)
    http = HttpClient(CACHE_DIR, timeout=cfg["http"]["timeout"],
                      min_interval=cfg["http"]["min_interval"],
                      retries=cfg["http"]["retries"])
    store = Store(DATA_DIR / "fundwatch.db")
    try:
        if args.test_push:
            results = notify_mod.notify_all(
                http, cfg, "基金雷达推送测试",
                "这是一条测试消息：如果你收到它，说明基金雷达的推送配置成功。", None)
            for item in results:
                print("%s: %s（%s）" % (item["channel"], item["status"], item["detail"]))
            return 0 if all(item["status"] in ("ok", "skipped") for item in results) else 1
        if args.print_latest:
            path = REPORTS_DIR / "latest.md"
            if not path.exists():
                print("暂无报告：%s 不存在" % path)
                return 2
            print(path.read_text(encoding="utf-8"))
            return 0

        trading = market_mod.is_trading_day(http, today)
        if args.scheduled and not trading:
            log.info("今天是休市日，跳过推送")
            print("今天休市，跳过推送")
            store.save_run(today, "holiday", now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                           "non-trading day")
            return 0
        if args.scheduled and not args.force and store.has_success(today):
            log.info("今天已经成功推送过，跳过重复运行")
            print("今天已经推送过，跳过重复运行")
            return 0

        log.info("开始抓取：%s（交易日=%s，报告日期=%s）", today, trading, report_date)
        market = market_mod.build_market(http, cfg, report_date)
        funds = build_funds(http, cfg, market, report_date)
        portfolio = build_portfolio(funds, cfg)
        signals = build_signals(store, funds, market, cfg, today, portfolio,
                                use_store=not args.dry_run)
        report = report_mod.build(market, funds, signals, cfg, report_date,
                                  portfolio=portfolio)
        paths = report_mod.write(report, REPORTS_DIR, date_prefix=report_date)

        push_results = []
        if not args.dry_run and not args.no_push and not args.print_latest \
                and report_date == today:
            subject = "基金雷达 %s 买卖建议（%s）" % (report_date, report["generated_at"][11:16])
            html_text = paths["html"].read_text(encoding="utf-8")
            push_results = notify_mod.notify_all(
                http, cfg, subject, report["summary"] + "\n\n报告文件：" + str(paths["html"]),
                html_text)
            for item in push_results:
                log.info("推送 %s：%s（%s）", item["channel"], item["status"], item["detail"])
        report["push"] = push_results
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        paths["json"].write_text(payload, encoding="utf-8")
        (REPORTS_DIR / "latest.json").write_text(payload, encoding="utf-8")

        if not args.dry_run:
            # Report generation is the source of truth; a failed push is logged but
            # does not mark the day as failed (prevents duplicate sends on retry).
            status = "ok"
            store.save_run(today, status, now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                           "channels=%s" % ",".join(
                               "%s:%s" % (i["channel"], i["status"]) for i in push_results))
            for fund in report["funds"]:
                store.save_quote(today, fund["code"], fund.get("nav"), fund.get("est_nav"),
                                 fund.get("est_chg"), fund.get("est_source"))

        print("")
        print(report["summary"])
        print("")
        for fund in report["funds"]:
            chg = fund.get("est_chg")
            chg_text = ("%+.2f%%" % chg) if chg is not None else "--"
            print("- %s %s：%s，涨跌 %s，建议 %s，金额 %.2f 元" % (
                fund["code"], fund["name"], fund.get("est_source"), chg_text,
                fund.get("action"), fund.get("suggest_amount") or 0))
        print("报告：%s" % paths["html"])
        print("Markdown：%s" % paths["md"])
        return 0
    finally:
        store.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="基金雷达：每日大盘+持仓基金买卖建议")
    parser.add_argument("--once", action="store_true", help="立即运行一次并推送")
    parser.add_argument("--scheduled", action="store_true", help="计划任务模式（跳过休市/已运行）")
    parser.add_argument("--print-latest", action="store_true", help="打印最近一期 Markdown 报告")
    parser.add_argument("--test-push", action="store_true", help="发送一条测试消息并退出")
    parser.add_argument("--dry-run", action="store_true", help="不推送、不写数据库")
    parser.add_argument("--no-push", action="store_true", help="只更新报告和数据库，不发送推送")
    parser.add_argument("--force", action="store_true", help="忽略当天已运行标记")
    parser.add_argument("--date", default=None, help="报告日期前缀（测试用，YYYY-MM-DD）")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="配置文件路径")
    return parser.parse_args(argv)


def main(argv=None):
    # pythonw.exe (used by the scheduled task) has no console streams.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - older consoles / devnull
        pass
    setup_logging()
    args = parse_args(argv)
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001 - scheduled task must log, not crash silently
        log.exception("运行失败: %s", exc)
        print("运行失败：%s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
