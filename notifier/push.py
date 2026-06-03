"""
通知推送模块 - 企业微信 / 邮件 / Telegram 多渠道
"""
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

import requests

from strategies.base import Signal

logger = logging.getLogger(__name__)


class Notifier:
    """统一推送接口，自动按配置启用对应渠道"""

    def __init__(self, config: dict):
        self.cfg = config
        self.channels = config.get("channels", [])

    def send_signals(self, signals: List[Signal], title: str = "Alpha量化选股信号"):
        """推送选股信号（I1：失败自动重试一次）"""
        if not signals:
            logger.info("无信号，跳过推送")
            return

        text = self._format_signals(signals)
        html = self._format_signals_html(signals)

        for channel in self.channels:
            if channel == "wecom":
                self._send_fallback(channel, self._send_wecom, title, text)
            elif channel == "email":
                self._send_fallback(channel, self._send_email, title, html)
            elif channel == "telegram":
                self._send_fallback(channel, self._send_telegram, title, text)
            else:
                logger.warning(f"未知推送渠道: {channel}")

    def send_text(self, title: str, content: str):
        """推送纯文本消息"""
        for channel in self.channels:
            try:
                if channel == "wecom":
                    self._send_wecom(title, content)
                elif channel == "email":
                    self._send_email(title, f"<pre>{content}</pre>")
                elif channel == "telegram":
                    self._send_telegram(title, content)
            except Exception as e:
                logger.error(f"推送渠道 {channel} 失败: {e}")

    def _send_fallback(self, channel: str, method: callable,
                       title: str, content: str):
        """I1：推送重试一次，记录日志"""
        try:
            method(title, content)
            return True
        except Exception as e:
            logger.error(f"推送渠道 {channel} 首次失败: {e}")
            try:
                method(title, content)
                logger.info(f"推送渠道 {channel} 重试成功")
                return True
            except Exception as e2:
                logger.error(f"推送渠道 {channel} 重试也失败: {e2}")
                return False

    def send_alert(self, level: str, message: str):
        """I1：独立告警推送，告警级别 ERROR/WARNING"""
        title = f"[Alpha量化 {level}] {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        for channel in self.channels:
            self._send_fallback(
                channel,
                lambda t, c: self._dispatch(channel, t, c),
                title, message,
            )

    def _dispatch(self, channel: str, title: str, content: str):
        if channel == "wecom":
            self._send_wecom(title, content)
        elif channel == "email":
            self._send_email(title, f"<pre>{content}</pre>")
        elif channel == "telegram":
            self._send_telegram(title, content)

    # ------------------------------------------------------------------
    # 格式化
    # ------------------------------------------------------------------

    MAX_PUSH_CHARS = 2000  # I1：推送内容 ≤ 2000 字符

    def _format_signals(self, signals: List[Signal]) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"【Alpha量化】 {now}  共 {len(signals)} 个信号\n"]
        for i, s in enumerate(signals, 1):
            emoji = "BUY" if s.signal == "BUY" else "SELL" if s.signal == "SELL" else "WATCH"
            line = (
                f"{i}. [{emoji}] {s.name}({s.code})  ¥{s.price:.2f}  "
                f"评分:{s.score:.0f}\n"
                f"   {s.reason}"
            )
            lines.append(line)
        result = "\n".join(lines)
        if len(result) > self.MAX_PUSH_CHARS:
            excess = 0
            while len("\n".join(lines)) > self.MAX_PUSH_CHARS and len(lines) > 2:
                excess += 1
                lines.pop(-1)
            lines.append(f"\n... 还有 {excess} 条信号未显示，请查看 Web 看板")
            result = "\n".join(lines)
        return result

    def _format_signals_html(self, signals: List[Signal]) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        rows = ""
        for s in signals:
            color = "#d9534f" if s.signal == "BUY" else "#5cb85c" if s.signal == "SELL" else "#5bc0de"
            rows += (
                f"<tr>"
                f"<td>{s.code}</td><td>{s.name}</td>"
                f"<td style='color:{color};font-weight:bold'>{s.signal}</td>"
                f"<td>¥{s.price:.2f}</td>"
                f"<td>{s.score:.0f}</td>"
                f"<td>{s.reason}</td>"
                f"</tr>"
            )
        return f"""
        <html><body>
        <h2>Alpha量化选股信号 — {now}</h2>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%">
          <thead style="background:#333;color:#fff">
            <tr><th>代码</th><th>名称</th><th>信号</th><th>价格</th><th>评分</th><th>原因</th></tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        <p style="color:#888;font-size:12px">本信号仅供参考，不构成投资建议。</p>
        </body></html>
        """

    # ------------------------------------------------------------------
    # 企业微信机器人
    # ------------------------------------------------------------------

    def _send_wecom(self, title: str, content: str):
        webhook = self.cfg.get("wecom_webhook")
        if not webhook:
            logger.warning("企业微信 Webhook 未配置")
            return
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"**{title}**\n\n{content}"
            }
        }
        resp = requests.post(webhook, json=payload, timeout=10)
        if resp.status_code != 200 or resp.json().get("errcode") != 0:
            raise RuntimeError(f"企业微信推送失败: {resp.text}")
        logger.info("企业微信推送成功")

    # ------------------------------------------------------------------
    # 邮件
    # ------------------------------------------------------------------

    def _send_email(self, subject: str, html_content: str):
        email_cfg = self.cfg.get("email", {})
        username = email_cfg.get("username")
        password = email_cfg.get("password")
        recipients = [r for r in email_cfg.get("recipients", []) if r]

        if not username or not password or not recipients:
            logger.warning("邮件配置不完整，跳过邮件推送")
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = username
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        host = email_cfg.get("smtp_host", "smtp.qq.com")
        port = email_cfg.get("smtp_port", 465)
        use_ssl = email_cfg.get("use_ssl", True)

        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                server.login(username, password)
                server.sendmail(username, recipients, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                server.login(username, password)
                server.sendmail(username, recipients, msg.as_string())

        logger.info(f"邮件推送成功 -> {recipients}")

    # ------------------------------------------------------------------
    # Telegram Bot
    # ------------------------------------------------------------------

    def _send_telegram(self, title: str, content: str):
        tg = self.cfg.get("telegram", {})
        token = tg.get("bot_token")
        chat_id = tg.get("chat_id")
        if not token or not chat_id:
            logger.warning("Telegram 配置不完整，跳过推送")
            return
        text = f"*{title}*\n\n{content}"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=10)
        if not resp.json().get("ok"):
            raise RuntimeError(f"Telegram 推送失败: {resp.text}")
        logger.info("Telegram 推送成功")
