"""Small shared helpers."""
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))


def now_cn():
    return datetime.now(CN_TZ)


def today_str():
    return now_cn().strftime("%Y-%m-%d")


def fmt_pct(value, digits=2, signed=True):
    if value is None:
        return "--"
    if signed:
        return ("%+." + str(digits) + "f%%") % value
    return ("%." + str(digits) + "f%%") % value


def fmt_money(value):
    if value is None:
        return "--"
    return "%.2f 元" % value


def fmt_nav(value):
    if value is None:
        return "--"
    return "%.4f" % value
