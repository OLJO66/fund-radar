#!/usr/bin/env python
"""Local dashboard for FundRadar.

Serves a small single-page app on 127.0.0.1 that lets you add / edit / delete
funds, change your shares and cost basis, and see allocation-aware advice.
All state is stored in the local config.json + reports/latest.json.
"""
import argparse
import json
import logging
import mimetypes
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fundwatch import report as report_mod
from fundwatch import sources
from fundwatch.config import (CACHE_DIR, CONFIG_PATH, LOGS_DIR, REPORTS_DIR, ROOT,
                              ensure_dirs, load_config, save_config)
from fundwatch.diagnosis import diagnose
from fundwatch.http import HttpClient
from fundwatch.portfolio import allocation_advice, build_portfolio
from fundwatch.targets import suggest_targets
from fundwatch.util import now_cn, today_str

log = logging.getLogger("fundwatch.dashboard")
WEB_DIR = ROOT / "web"
STATE_LOCK = threading.Lock()
REFRESH_LOCK = threading.Lock()
HTTP = HttpClient(CACHE_DIR)

INDEX_OPTIONS = {
    "sh000688": "科创50",
    "sh000300": "沪深300",
    "sh000001": "上证指数",
    "sz399006": "创业板指",
    "sh000905": "中证500",
    "NDX": "纳斯达克100",
    "": "无（用平台估值）",
}


# --------------------------------------------------------------------------- helpers
def _read_latest():
    path = REPORTS_DIR / "latest.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _python_exe():
    exe = sys.executable or ""
    if exe.lower().endswith("pythonw.exe"):
        candidate = exe[:-11] + "python.exe"
        if os.path.exists(candidate):
            return candidate
    return exe or "python"


def _derive_shares(fund, nav):
    if fund.get("shares"):
        return float(fund["shares"])
    if fund.get("amount_mode") == "principal":
        return float(fund["amount"]) / float(fund["cost"])
    if nav:
        return float(fund["amount"]) / nav
    return float(fund["amount"]) / float(fund["cost"])


def build_state(cfg=None):
    cfg = cfg or load_config()
    report = _read_latest()
    report_funds = {str(item.get("code")): item for item in report.get("funds", [])}
    default_target = round(1.0 / len(cfg["funds"]), 4) if cfg["funds"] else 0.0
    rows = []
    for fund in cfg["funds"]:
        code = fund["code"]
        rf = report_funds.get(code, {})
        nav = rf.get("est_nav") or rf.get("nav")
        nav_source = rf.get("est_source") or ""
        if nav is None:
            nav = fund.get("last_nav") or fund.get("cost")
            nav_source = "最近保存的净值（待刷新）" if fund.get("last_nav") else "待刷新（使用成本价）"
        shares = _derive_shares(fund, nav)
        value = shares * nav
        cost_total = shares * float(fund["cost"])
        profit = value - cost_total
        profit_pct = (nav / float(fund["cost"]) - 1.0) * 100.0 if fund["cost"] else None
        target = fund.get("target_weight")
        if target is None:
            target = default_target
        rows.append({
            "code": code,
            "name": fund.get("name") or code,
            "kind": fund.get("kind", "a_share"),
            "benchmark": fund.get("benchmark") or "",
            "cost": float(fund["cost"]),
            "shares": round(shares, 4),
            "market_value": round(value, 2),
            "cost_total": round(cost_total, 2),
            "profit": round(profit, 2),
            "profit_pct": round(profit_pct, 2) if profit_pct is not None else None,
            "nav": nav,
            "nav_date": rf.get("latest_nav_date") or rf.get("nav_date") or "",
            "nav_source": nav_source,
            "est_chg": rf.get("est_chg"),
            "is_estimate": rf.get("is_estimate"),
            "score": rf.get("score"),
            "trend_action": rf.get("trend_action") or rf.get("action") or "待刷新",
            "trend_amount": rf.get("trend_amount") or 0.0,
            "hard_exit": bool(rf.get("hard_exit")),
            "target_weight": target,
            "add_budget": float(fund.get("add_budget") or 0.0),
            "last_buy_date": fund.get("last_buy_date"),
            "reasons": list(rf.get("trend_reasons") or rf.get("reasons") or []),
            "pending": not bool(rf),
        })

    portfolio = build_portfolio(rows, cfg)
    for row in rows:
        base_action = row.get("trend_action") or "持有"
        base_amount = row.get("trend_amount") or 0.0
        if row.get("pending"):
            row["action"] = "待刷新"
            row["suggest_amount"] = 0.0
            continue
        action, amount, notes = allocation_advice(
            row, base_action, base_amount, portfolio, cfg,
            hard_exit=bool(row.get("hard_exit")))
        min_trade = float((cfg.get("risk") or {}).get("min_trade_amount", 50) or 0)
        if action in ("加仓", "减仓") and min_trade and amount < min_trade:
            notes.append("预计金额低于 %.0f 元，本次忽略" % min_trade)
            action = "观望"
            amount = 0.0
        row["action"] = action
        row["suggest_amount"] = round(amount, 2)
        row["reasons"] = list(row.get("reasons") or []) + notes

    market = report.get("market") or {}
    diagnosis = diagnose(rows, portfolio, cfg)
    target_suggestion = suggest_targets(rows, portfolio, cfg)
    summary = report_mod.summary_text({
        "date": report.get("date") or today_str(),
        "market": market,
        "funds": [dict(row, action=row.get("action")) for row in rows],
    }) if market else ""
    report_date = report.get("date") or ""
    report_url = ""
    if report_date:
        html_path = REPORTS_DIR / ("%s.html" % report_date)
        if html_path.exists():
            report_url = "/reports/%s.html" % report_date
    return {
        "date": report_date or today_str(),
        "generated_at": report.get("generated_at") or "",
        "stale": report_date != today_str(),
        "market": market,
        "portfolio": portfolio,
        "diagnosis": diagnosis,
        "target_suggestion": target_suggestion,
        "funds": rows,
        "summary": summary,
        "report_url": report_url,
        "index_options": [{"code": key, "name": value} for key, value in INDEX_OPTIONS.items()],
    }


def lookup_fund(code):
    code = str(code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise ValueError("基金代码应为 6 位数字")
    report = _read_latest()
    for item in report.get("funds", []):
        if str(item.get("code")) == code:
            return {
                "code": code,
                "name": item.get("name") or code,
                "nav": item.get("est_nav") or item.get("nav"),
                "nav_date": item.get("latest_nav_date") or item.get("nav_date") or "",
                "kind": item.get("kind") or "a_share",
                "benchmark": item.get("benchmark", {}).get("code") if isinstance(
                    item.get("benchmark"), dict) else (item.get("benchmark") or ""),
                "exists": True,
            }
    quotes = {}
    try:
        quotes = sources.fetch_fund_quotes(HTTP, [code])
    except Exception as exc:  # noqa: BLE001
        log.warning("lookup quote failed for %s: %s", code, exc)
    row = quotes.get(code) or {}
    name = row.get("name") or ""
    is_us = ("纳斯达克" in name or "纳指" in name or "美国" in name or "标普" in name)
    kind = "qdii" if ("QDII" in name or is_us) else "a_share"
    return {
        "code": code,
        "name": name or code,
        "nav": row.get("est_nav") or row.get("nav"),
        "nav_date": row.get("nav_date") or "",
        "kind": kind,
        "benchmark": "NDX" if is_us else "",
        "exists": False,
    }


def _validate_payload(body):
    code = str(body.get("code") or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise ValueError("基金代码应为 6 位数字")
    cost_raw = body.get("cost")
    try:
        cost = float(cost_raw) if cost_raw not in ("", None) else 0.0
    except (TypeError, ValueError):
        raise ValueError("成本价必须是数字")
    if cost < 0:
        raise ValueError("成本价不能为负")
    add_budget = float(body.get("add_budget") or 200)
    if add_budget < 0:
        raise ValueError("加仓预算不能为负")
    target = body.get("target_weight")
    if target in ("", None):
        target = None
    else:
        target = float(target)
        if not 0.0 <= target <= 1.0:
            raise ValueError("目标占比需在 0~1 之间")
    return code, cost, add_budget, target


def upsert_fund(body, editing_code=None):
    with STATE_LOCK:
        cfg = load_config()
        code, cost, add_budget, target = _validate_payload(body)
        if editing_code and str(editing_code) != code:
            raise ValueError("不支持修改基金代码，请删除后重新添加")
        existing = next((f for f in cfg["funds"] if f["code"] == code), None)
        if not editing_code and existing:
            raise ValueError("该基金已在持仓中，请直接修改持仓")
        if target is None:
            target = (existing or {}).get("target_weight")
        lookup = {}
        if not body.get("name") or body.get("value") not in (None, ""):
            lookup = lookup_fund(code)
        name = (body.get("name") or (existing or {}).get("name")
                or lookup.get("name") or code)
        kind = body.get("kind") or (existing or {}).get("kind") or lookup.get("kind") or "a_share"
        benchmark = body.get("benchmark")
        if benchmark is None:
            benchmark = (existing or {}).get("benchmark") or lookup.get("benchmark") or ""

        nav = lookup.get("nav")
        if nav is None:
            report = _read_latest()
            rf = next((item for item in report.get("funds", [])
                       if str(item.get("code")) == code), None)
            if rf:
                nav = rf.get("est_nav") or rf.get("nav")
        nav = nav or cost
        if not nav or nav <= 0:
            raise ValueError("无法获取该基金净值，请改填持有份额和成本价")

        shares = body.get("shares")
        if shares in ("", None):
            value = body.get("value")
            profit = body.get("profit")
            if value in ("", None):
                raise ValueError("请填写持有份额，或填写当前市值与累计收益")
            value = float(value)
            profit = float(profit or 0)
            if value <= 0:
                raise ValueError("当前市值必须大于 0")
            shares = value / nav
            cost_total = value - profit
            cost = cost_total / shares if shares else cost
        else:
            shares = float(shares)
            if shares <= 0:
                raise ValueError("持有份额必须大于 0")
            if cost <= 0:
                raise ValueError("成本价必须大于 0")

        fund = {
            "code": code,
            "name": name,
            "cost": round(cost, 6),
            "shares": round(shares, 4),
            "amount": round(shares * nav, 2),
            "amount_mode": "market_value",
            "benchmark": benchmark,
            "kind": kind,
            "add_budget": add_budget,
            "target_weight": target,
            "last_nav": round(nav, 4),
            "last_nav_date": lookup.get("nav_date") or (existing or {}).get("last_nav_date") or "",
            "last_buy_date": (existing or {}).get("last_buy_date"),
        }
        if existing:
            existing.update(fund)
        else:
            cfg["funds"].append(fund)
        _save_config_checked(cfg)
    return build_state(load_config())


def _save_config_checked(cfg):
    path = Path(cfg.get("_path") or CONFIG_PATH)
    backup = path.read_text(encoding="utf-8") if path.exists() else None
    save_config(cfg, path)
    try:
        load_config(path)
    except Exception:
        if backup is not None:
            path.write_text(backup, encoding="utf-8")
        raise


def delete_fund(code):
    with STATE_LOCK:
        cfg = load_config()
        before = len(cfg["funds"])
        cfg["funds"] = [f for f in cfg["funds"] if f["code"] != str(code)]
        if len(cfg["funds"]) == before:
            raise ValueError("未找到基金 %s" % code)
        if not cfg["funds"]:
            raise ValueError("至少保留一只基金；如需清空请直接编辑 config.json")
        _save_config_checked(cfg)
    return build_state(load_config())


def equalize_targets():
    with STATE_LOCK:
        cfg = load_config()
        count = len(cfg["funds"])
        if not count:
            raise ValueError("还没有基金")
        equal = round(1.0 / count, 4)
        for fund in cfg["funds"]:
            fund["target_weight"] = equal
        _save_config_checked(cfg)
    return build_state(load_config())


def apply_suggested_targets():
    with STATE_LOCK:
        cfg = load_config()
        state = build_state(cfg)
        suggestion = state.get("target_suggestion") or {}
        targets = {row["code"]: float(row["suggested"])
                   for row in suggestion.get("rows", [])}
        if not targets:
            raise ValueError("暂时无法生成建议目标")
        if abs(sum(targets.values()) - 1.0) > 0.02:
            raise ValueError("建议目标合计异常，未应用")
        for fund in cfg["funds"]:
            if fund["code"] in targets:
                fund["target_weight"] = round(targets[fund["code"]], 4)
        _save_config_checked(cfg)
    return build_state(load_config())


def refresh_data():
    with REFRESH_LOCK:
        proc = subprocess.run(
            [_python_exe(), "run.py", "--dry-run"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180)
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, output.strip()


# --------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "FundRadar/1.0"

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------------ output
    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload, ensure_ascii=False))

    def _error(self, message, status=400):
        self._json({"ok": False, "error": str(message)}, status)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _file(self, path, fallback_type="application/octet-stream"):
        if not path.exists() or not path.is_file():
            self._error("文件不存在", 404)
            return
        content_type = mimetypes.guess_type(str(path))[0] or fallback_type
        if path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif path.suffix in (".js", ".css"):
            content_type += "; charset=utf-8"
        self._send(200, path.read_bytes(), content_type)

    # -------------------------------------------------------------------- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._file(WEB_DIR / "index.html")
                return
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                target = (WEB_DIR / name).resolve()
                if WEB_DIR.resolve() not in target.parents:
                    self._error("非法路径", 403)
                    return
                self._file(target)
                return
            if path.startswith("/reports/"):
                name = path[len("/reports/"):]
                target = (REPORTS_DIR / name).resolve()
                if REPORTS_DIR.resolve() not in target.parents:
                    self._error("非法路径", 403)
                    return
                self._file(target)
                return
            if path == "/api/state":
                self._json({"ok": True, "state": build_state()})
                return
            if path == "/api/lookup":
                query = parse_qs(parsed.query)
                code = (query.get("code") or [""])[0]
                self._json({"ok": True, "fund": lookup_fund(code)})
                return
            self._error("未知路径", 404)
        except ValueError as exc:
            self._error(exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("GET failed")
            self._error(exc, 500)

    # ------------------------------------------------------------------- POST
    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/funds":
                body = self._body()
                self._json({"ok": True, "state": upsert_fund(body)})
                return
            if path == "/api/refresh":
                code, output = refresh_data()
                self._json({"ok": code == 0, "output": output[-4000:],
                            "state": build_state()}, 200 if code == 0 else 500)
                return
            if path == "/api/portfolio/equal-targets":
                self._json({"ok": True, "state": equalize_targets()})
                return
            if path == "/api/portfolio/apply-suggested-targets":
                self._json({"ok": True, "state": apply_suggested_targets()})
                return
            self._error("未知路径", 404)
        except ValueError as exc:
            self._error(exc)
        except subprocess.TimeoutExpired:
            self._error("刷新超时，请稍后重试", 504)
        except Exception as exc:  # noqa: BLE001
            log.exception("POST failed")
            self._error(exc, 500)

    # -------------------------------------------------------------------- PUT
    def do_PUT(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/funds/"):
                code = path[len("/api/funds/"):]
                body = self._body()
                self._json({"ok": True, "state": upsert_fund(body, editing_code=code)})
                return
            self._error("未知路径", 404)
        except ValueError as exc:
            self._error(exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("PUT failed")
            self._error(exc, 500)

    # ----------------------------------------------------------------- DELETE
    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/funds/"):
                code = path[len("/api/funds/"):]
                self._json({"ok": True, "state": delete_fund(code)})
                return
            self._error("未知路径", 404)
        except ValueError as exc:
            self._error(exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("DELETE failed")
            self._error(exc, 500)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(str(LOGS_DIR / "dashboard.log"), encoding="utf-8")])
    parser = argparse.ArgumentParser(description="FundRadar 本地管理面板")
    parser.add_argument("--port", type=int, default=int(os.environ.get("FUNDRADAR_PORT", "8765")))
    args = parser.parse_args(argv)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        server.daemon_threads = True
    except OSError as exc:
        print("端口 %s 已被占用，可能面板已在运行：http://127.0.0.1:%s" % (args.port, args.port))
        log.warning("port busy: %s", exc)
        return 0
    print("基金雷达面板：http://127.0.0.1:%s" % args.port)
    log.info("dashboard listening on %s", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
