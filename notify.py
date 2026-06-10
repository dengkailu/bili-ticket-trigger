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

    def _send_feishu(self, title: str, content: str = "",
                      fields: Optional[dict] = None) -> None:
        """通过飞书 Webhook 发送通知"""
        elements = [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**{title}**"}}
        ]
        if content:
            elements.append(
                {"tag": "div", "text": {"tag": "lark_md", "content": content}}
            )
        if fields:
            field_lines = []
            for k, v in fields.items():
                field_lines.append(f"  {k}: **{v}**")
            elements.append(
                {"tag": "div",
                 "text": {"tag": "lark_md", "content": "\n".join(field_lines)}}
            )

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "red",
                },
                "elements": elements,
            },
        }
        try:
            resp = requests.post(self.feishu_webhook, json=payload, timeout=10)
            if resp.status_code != 200:
                print(f"[通知] 飞书发送失败: {resp.text}")
            else:
                print(f"[通知] 飞书已发送")
        except Exception as e:
            print(f"[通知] 飞书异常: {e}")


def send_order_success(order_info: dict, project_name: str = "",
                        ticket_desc: str = "", buyer_name: str = "",
                        config: Optional[dict] = None) -> None:
    """发送抢票成功通知的便捷函数"""
    if config is None:
        from config import load_config
        config = load_config()

    notifier = Notifier(config)
    if not notifier.enabled:
        return

    title = "🎫 抢票成功!"
    fields = {
        "项目": project_name,
        "票档": ticket_desc,
        "购票人": buyer_name,
        "订单号": order_info.get("order_id", "-"),
        "时间": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    notifier.send(title, fields=fields)
