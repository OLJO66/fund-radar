"""Configuration loading, defaults and path helpers for FundRadar."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
REPORTS_DIR = ROOT / "reports"
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
CACHE_DIR = DATA_DIR / "cache"

DEFAULTS = {
    "timezone": "Asia/Shanghai",
    "funds": [],
    "indices": [
        {"code": "sh000001", "name": "上证指数"},
        {"code": "sh000300", "name": "沪深300"},
        {"code": "sz399001", "name": "深证成指"},
        {"code": "sz399006", "name": "创业板指"},
        {"code": "sh000688", "name": "科创50"},
    ],
    "push": {
        "wecom": {"enabled": True, "webhook": ""},
        "email": {
            "enabled": True,
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
            "user": "",
            "password": "",
            "to": "",
        },
    },
    "schedule": {"time": "14:30", "weekdays": [1, 2, 3, 4, 5]},
    "risk": {"max_add_pct": 0.2, "take_profit_pct": 0.2, "fee_guard_days": 7,
             "min_trade_amount": 50},
    "portfolio": {
        "max_single_weight": 0.65,
        "rebalance_band": 0.05,
        "target_model": {
            "tech": 0.40, "dividend": 0.10, "defensive": 0.20,
            "gold": 0.08, "emerging": 0.10, "balanced": 0.12,
        },
    },
    "http": {"timeout": 15, "min_interval": 0.35, "retries": 3},
}


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path=None):
    path = Path(path) if path else CONFIG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    cfg = _deep_merge(DEFAULTS, raw)
    _validate(cfg)
    cfg["_path"] = str(path)
    return cfg


def _validate(cfg):
    funds = cfg.get("funds") or []
    if not funds:
        raise ValueError("config.json 里没有配置任何基金（funds 为空）")
    seen = set()
    for fund in funds:
        code = str(fund.get("code", "")).strip()
        if not code or not code.isdigit():
            raise ValueError("基金代码不合法: %r" % (fund.get("code"),))
        if code in seen:
            raise ValueError("基金代码重复: %s" % code)
        seen.add(code)
        fund["code"] = code
        fund.setdefault("name", code)
        fund.setdefault("amount_mode", "market_value")
        fund.setdefault("kind", "a_share")
        fund.setdefault("benchmark", "")
        fund.setdefault("add_budget", 200.0)
        if fund["amount_mode"] not in ("market_value", "principal"):
            raise ValueError("amount_mode 只能是 market_value 或 principal: %s" % code)
        for key in ("cost", "amount"):
            try:
                fund[key] = float(fund[key])
            except (TypeError, ValueError):
                raise ValueError("基金 %s 的 %s 必须是数字" % (code, key))
            if fund[key] <= 0:
                raise ValueError("基金 %s 的 %s 必须大于 0" % (code, key))
        if float(fund["add_budget"]) < 0:
            raise ValueError("基金 %s 的 add_budget 不能为负" % code)
        fund["add_budget"] = float(fund["add_budget"])
        if fund.get("shares") is not None:
            try:
                fund["shares"] = float(fund["shares"])
            except (TypeError, ValueError):
                raise ValueError("基金 %s 的 shares 必须是数字" % code)
            if fund["shares"] <= 0:
                raise ValueError("基金 %s 的 shares 必须大于 0" % code)
        if fund.get("target_weight") is not None:
            try:
                fund["target_weight"] = float(fund["target_weight"])
            except (TypeError, ValueError):
                raise ValueError("基金 %s 的 target_weight 必须是数字" % code)
            if not 0.0 <= fund["target_weight"] <= 1.0:
                raise ValueError("基金 %s 的 target_weight 必须在 0~1 之间" % code)
    cfg["funds"] = funds
    portfolio = cfg.get("portfolio") or {}
    try:
        if not 0.05 <= float(portfolio.get("max_single_weight", 0.65)) <= 1.0:
            raise ValueError("portfolio.max_single_weight 需在 0.05~1 之间")
        if not 0.0 <= float(portfolio.get("rebalance_band", 0.05)) <= 0.5:
            raise ValueError("portfolio.rebalance_band 需在 0~0.5 之间")
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc))


def ensure_dirs():
    for path in (REPORTS_DIR, DATA_DIR, LOGS_DIR, CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def save_config(cfg, path=None):
    """Atomically persist config (secrets stay local)."""
    path = Path(path) if path else Path(cfg.get("_path") or CONFIG_PATH)
    data = {key: value for key, value in cfg.items() if not key.startswith("_")}
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)
    return path


def redact(cfg):
    """Return a copy safe to log / embed in reports."""
    out = copy.deepcopy(cfg)
    push = out.get("push", {})
    if push.get("wecom", {}).get("webhook"):
        push["wecom"]["webhook"] = "***"
    email = push.get("email", {})
    if email.get("password"):
        email["password"] = "***"
    return out


def display_path(path):
    """Path relative to the project root when possible (for reports/logs)."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except Exception:
        return str(path)
