"""Push channels: WeCom group bot and SMTP email.

Both channels are optional. Missing credentials are treated as " skipped",
not as a failure, so a fresh install still produces reports.
"""
import json
import logging
import smtplib
import ssl
import urllib.error
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

log = logging.getLogger("fundwatch.notify")

WECOM_LIMIT = 4000  # bytes, the API limit is 4096


def _truncate(text, limit=WECOM_LIMIT):
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    clipped = raw[: limit - 3].decode("utf-8", "ignore")
    return clipped + "..."


def send_wecom(http, webhook, markdown_text):
    if not webhook:
        return {"channel": "wecom", "status": "skipped", "detail": "未配置 webhook"}
    content = _truncate(markdown_text)
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    try:
        body = http.post_json(webhook, payload)
        data = json.loads(body)
        if data.get("errcode") == 0:
            return {"channel": "wecom", "status": "ok", "detail": "已发送"}
        return {"channel": "wecom", "status": "failed",
                "detail": "errcode=%s %s" % (data.get("errcode"), data.get("errmsg"))}
    except (urllib.error.URLError, ValueError, OSError) as exc:
        log.warning("wecom push failed: %s", exc)
        return {"channel": "wecom", "status": "failed", "detail": str(exc)}


def send_email(email_cfg, subject, text, html_text):
    if not email_cfg or not email_cfg.get("user"):
        return {"channel": "email", "status": "skipped", "detail": "未配置邮箱账号"}
    if not email_cfg.get("password"):
        return {"channel": "email", "status": "skipped",
                "detail": "已填写邮箱 %s，缺少 SMTP 授权码（运行 configure.py 填写）" %
                          email_cfg["user"]}
    host = email_cfg.get("smtp_host") or "smtp.qq.com"
    port = int(email_cfg.get("smtp_port") or 465)
    user = email_cfg["user"]
    password = email_cfg["password"]
    to_addr = email_cfg.get("to") or user
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header("基金雷达", "utf-8")), user))
    msg["To"] = to_addr
    msg.attach(MIMEText(text, "plain", "utf-8"))
    if html_text:
        msg.attach(MIMEText(html_text, "html", "utf-8"))
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as server:
            server.login(user, password)
            server.sendmail(user, [addr.strip() for addr in to_addr.split(",") if addr.strip()],
                            msg.as_string())
        return {"channel": "email", "status": "ok", "detail": "已发送至 %s" % to_addr}
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        log.warning("email push failed: %s", exc)
        return {"channel": "email", "status": "failed", "detail": str(exc)}


def notify_all(http, cfg, subject, text, html_text):
    push = cfg.get("push") or {}
    results = []
    wecom = push.get("wecom") or {}
    if wecom.get("enabled", True):
        results.append(send_wecom(http, wecom.get("webhook", ""), text))
    email_cfg = push.get("email") or {}
    if email_cfg.get("enabled", True):
        results.append(send_email(email_cfg, subject, text, html_text))
    return results
