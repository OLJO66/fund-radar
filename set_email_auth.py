#!/usr/bin/env python
"""只设置 QQ 邮箱的 SMTP 授权码（输入不回显，适合已经填好邮箱地址的情况）。"""
import getpass
import sys

from fundwatch.config import load_config, save_config


def main():
    cfg = load_config()
    email = cfg["push"]["email"]
    user = email.get("user") or ""
    if not user:
        print("config.json 里还没有发件邮箱，请先运行 configure.py。")
        return 2
    print("当前发件邮箱：%s" % user)
    print("提示：授权码不是 QQ 登录密码，请在 QQ 邮箱 设置 → 账户 → POP3/SMTP 中生成。")
    code = getpass.getpass("SMTP 授权码（输入不回显）: ").strip()
    if not code:
        print("未输入，配置未修改。")
        return 1
    email["password"] = code
    email["enabled"] = True
    save_config(cfg)
    print("已保存到 config.json。下一步验证：")
    print("  D:\\python\\python.exe run.py --test-push")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
