"""
B站会员购抢票工具 - 配置模块
"""

import json
import os

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "cookie": "",
    "version": "134",
    "base_url": "https://show.bilibili.com",
    "request_timeout": 10,
    "poll_interval": 0.5,
    "max_retries": 3,
    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "proxy": {
        "enabled": False,
        "http": "",
        "https": "",
    },
    "notification": {
        "enabled": False,
        "tg_token": "",
        "tg_chat_id": "",
        "feishu_webhook": "",
    },
    "auth": {
        "uid": 0,
        "uname": "",
        "verified": False,
    },
}


def load_config(config_path: str = CONFIG_FILE) -> dict:
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
    else:
        saved = {}
    return _deep_merge(DEFAULT_CONFIG, saved)


def save_config(cfg: dict, config_path: str = CONFIG_FILE) -> None:
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def get_proxy(config: dict = None) -> dict:
    """构建 requests 可用的代理字典"""
    if config is None:
        config = load_config()
    proxy_cfg = config.get("proxy", {})
    if not proxy_cfg.get("enabled"):
        return None
    proxies = {}
    if proxy_cfg.get("http"):
        proxies["http"] = proxy_cfg["http"]
        proxies["https"] = proxy_cfg.get("https") or proxy_cfg["http"]
    return proxies or None
