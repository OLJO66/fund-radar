#!/usr/bin/env python
"""Interactive setup wizard for FundRadar.

Secrets are read with getpass and written only to the local config.json.
"""
import getpass
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
EXAMPLE = ROOT / "config.example.json"


def load():
    path = CONFIG if CONFIG.exists() else EXAMPLE
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def ask(prompt, default=""):
    suffix = (" [%s]" % default) if default else ""
    value = input(prompt + suffix + ": ").strip()
    return value or default


def main():
    cfg = load()
    print("=== 基金雷达配置 ===")
    print("当前基金：")
    for fund in cfg["funds"]:
        print("  %s  %s  成本 %.4f  市值 %.2f" % (
            fund["code"], fund.get("name", ""), float(fund["cost"]), float(fund["amount"])))
    if ask("是否重新录入基金列表？(y/N)", "N").lower() == "y":
        funds = []
        while True:
            code = ask("基金代码（留空结束）")
            if not code:
                break
            old = next((item for item in cfg["funds"] if item["code"] == code), {})
            name = ask("基金名称", old.get("name", code))
            cost = ask("持仓成本价", str(old.get("cost", "")))
            amount = ask("当前持有金额（元）", str(old.get("amount", "")))
            kind = ask("类型 a_share / qdii", old.get("kind", "a_share"))
            benchmark = ask("跟踪指数代码（如 sh000688；QDII 填 NDX）",
                            old.get("benchmark", "sh000688" if kind == "a_share" else "NDX"))
            budget = ask("单次加仓预算（元）", str(old.get("add_budget", 200)))
            funds.append({
                "code": code, "name": name, "cost": float(cost), "amount": float(amount),
                "amount_mode": old.get("amount_mode", "market_value"),
                "shares": old.get("shares"),
                "benchmark": benchmark, "kind": kind, "add_budget": float(budget),
                "target_weight": old.get("target_weight"),
                "last_nav": old.get("last_nav"),
                "last_buy_date": old.get("last_buy_date"),
            })
        if funds:
            cfg["funds"] = funds

    print("\n--- 企业微信群机器人 ---")
    wecom = cfg["push"].setdefault("wecom", {})
    hook = getpass.getpass("Webhook 地址（输入不回显，留空保留原值）: ").strip()
    if hook:
        wecom["webhook"] = hook
    wecom["enabled"] = bool(wecom.get("webhook"))

    print("\n--- QQ 邮箱 ---")
    email = cfg["push"].setdefault("email", {})
    user = ask("发件邮箱", email.get("user", ""))
    if user:
        email["user"] = user
        email["to"] = ask("收件邮箱", email.get("to") or user)
        pwd = getpass.getpass("SMTP 授权码（输入不回显，留空保留原值）: ").strip()
        if pwd:
            email["password"] = pwd
        email["enabled"] = bool(email.get("password"))
    else:
        email["enabled"] = False

    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    print("\n已写入 %s" % CONFIG)
    print("提示：授权码不是 QQ 密码，请在 QQ 邮箱设置中开启 SMTP 并生成授权码。")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
