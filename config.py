"""
B站会员购抢票工具 - 配置模块
"""

import json
import os
import random

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
        "mode": "single",
        "url": "",
        "pool": [],
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


class ProxyRotator:
    """代理池轮转器 — 每次请求随机选一个"""

    def __init__(self, config: dict = None):
        if config is None:
            config = load_config()
        self._cfg = config.get("proxy", {})
        self._pool = list(self._cfg.get("pool", []))
        self._idx = 0
        self._single = self._cfg.get("url", "")
        self._mode = self._cfg.get("mode", "single")
        self._enabled = self._cfg.get("enabled", False)

    def next(self) -> dict:
        """返回 requests 兼容的代理字典, 每次随机选"""
        if not self._enabled:
            return None
        url = None
        if self._mode == "pool" and self._pool:
            url = random.choice(self._pool)
        elif self._single:
            url = self._single
        if url:
            return {"http": url, "https": url}
        return None

    @property
    def active(self) -> bool:
        return self._enabled and bool(self._single or self._pool)

    def current_url(self) -> str:
        if self._mode == "pool" and self._pool:
            return f"pool ({len(self._pool)} nodes)"
        return self._single or "(未设置)"


def get_proxy(config: dict = None) -> dict:
    """兼容旧接口"""
    rotator = ProxyRotator(config)
    return rotator.next()

