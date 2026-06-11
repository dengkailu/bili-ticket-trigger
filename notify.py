"""
B站会员购抢票工具 - 通知模块
支持 Telegram Bot / 飞书 Webhook 发送抢票成功通知
"""

import json
import time
from typing import Optional

import requests


class Notifier:
    """通知器, 支持 Telegram 和飞书"""

    def __init__(self, config: dict):
        """
        config 应包含:
          tg_token: Telegram Bot token
          tg_chat_id: Telegram Chat ID
          feishu_webhook: 飞书 Webhook URL
        """
        self.tg_token = config.get("tg_token", "")
        self.tg_chat_id = config.get("tg_chat_id", "")
        self.feishu_webhook = config.get("feishu_webhook", "")
        self.enabled = bool(self.tg_token or self.feishu_webhook)

    def send(self, title: str, content: str = "", fields: Optional[dict] = None) -> None:
        """发送通知到所有已配置的渠道"""
        if self.tg_token and self.tg_chat_id:
            self._send_telegram(title, content, fields)
        if self.feishu_webhook:
            self._send_feishu(title, content, fields)

    def _send_telegram(self, title: str, content: str = "",
                        fields: Optional[dict] = None) -> None:
        """通过 Telegram Bot 发送通知"""
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        lines = [f"*{title}*", ""]
        if content:
            lines.append(content)
        if fields:
            for k, v in fields.items():
                lines.append(f"  {k}: `{v}`")

        text = "\n".join(lines)
        payload = {
            "chat_id": self.tg_chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                print(f"[通知] TG 发送失败: {resp.text}")
            else:
                print(f"[通知] TG 已发送")
        except Exception as e:
            print(f"[通知] TG 异常: {e}")

    def _send_telegram_raw(self, title: str, text: str) -> None:
        """TG 富文本发送"""
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        msg = f"*{title}*\n\n{text}"
        try:
            resp = requests.post(url, json={
                "chat_id": self.tg_chat_id, "text": msg,
                "parse_mode": "Markdown",
            }, timeout=10)
            if resp.status_code == 200:
                print(f"[通知] TG 已发送")
            else:
                print(f"[通知] TG 发送失败: {resp.text}")
        except Exception as e:
            print(f"[通知] TG 异常: {e}")

    def _send_feishu_card(self, title: str, lines: list) -> None:
        """飞书卡片富文本"""
        elements = []
        for i, line in enumerate(lines):
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": line}
            })
        if self.feishu_webhook:
            pass  # keep webhook
        try:
            resp = requests.post(self.feishu_webhook, json={
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": title},
                        "template": "red",
                    },
                    "elements": elements,
                },
            }, timeout=10)
            if resp.status_code == 200:
                print(f"[通知] 飞书已发送")
            else:
                print(f"[通知] 飞书发送失败: {resp.text}")
        except Exception as e:
            print(f"[通知] 飞书异常: {e}")


def send_order_success(order_info: dict, project_name: str = "",
                        ticket_desc: str = "", buyer_name: str = "",
                        count: int = 1, total_price: int = 0,
                        pay_url: str = "",
                        config: Optional[dict] = None) -> None:
    """发送抢票成功通知"""
    if config is None:
        from config import load_config
        config = load_config()

    notifier = Notifier(config)
    if not notifier.enabled:
        return

    oid = order_info.get("order_id", order_info.get("orderId", "-"))

    # 飞书卡片富文本
    title = "🎫 抢票成功！"
    deadline = time.strftime("%H:%M", time.localtime(time.time() + 600))
    lines = [
        f"**项目**: {project_name or '未知'}",
        f"**票档**: {ticket_desc or '未知'}  ×{count}张",
        f"**购票人**: {buyer_name}",
        f"**金额**: ¥{total_price / 100:.2f}",
        f"**订单号**: {oid}",
        f"**下单时间**: {time.strftime('%H:%M:%S')}",
        f"**⚠️ 请在 {deadline} 前支付，超时自动取消**",
    ]
    if pay_url:
        lines.append(f"**[点击支付]({pay_url})**")

    content = "\n".join(lines)
    tg_content = "\n".join(l.replace("**[", "[").replace("](", "](") for l in lines)

    if notifier.tg_token and notifier.tg_chat_id:
        notifier._send_telegram_raw(title, tg_content)
    if notifier.feishu_webhook:
        notifier._send_feishu_card(title, lines)
