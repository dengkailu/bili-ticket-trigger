"""
B站会员购 API 客户端
处理项目查询、场次/票档获取、下单全流程

包含:
  - 二维码扫码登录 (无需手动复制 Cookie)
  - 项目/票档查询
  - 下单抢票
  - 购票人校验

售罄状态码:
  sale_flag_number 含义:
    2   - 预售中/可售
    3   - 已停售
    5   - 不可售
    102 - 已结束

实名要求 (buyer_info):
  "2,1" - 需要身份证信息(2) + 手机号(1)
  "1"   - 仅手机号
  ""    - 无需买家信息
"""

import hashlib
import json
import os
import random
import re
import uuid
import time
import base64
from datetime import datetime, timedelta
from typing import Optional, Tuple

import requests

from config import load_config, save_config, ProxyRotator

BUYERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "buyers.json")

SALE_FLAG_MAP = {
    1: "未开售",
    2: "预售中",
    3: "已停售",
    4: "已售罄",
    5: "不可售",
    102: "已结束",
}

PROJECT_SALE_FLAG_MAP = SALE_FLAG_MAP

PROJECT_TYPE_MAP = {
    1: "演出",
    2: "本地生活",
    6: "其他演出",
    7: "虚拟直播",
    10: "漫展",
    12: "演唱会",
    27: "主题餐饮",
}

SCREEN_TICKET_TYPE_MAP = {
    1: "单日票",
    2: "活动票",
    12: "演出票",
}

DELIVERY_TYPE_MAP = {
    1: "电子票",
    4: "纸质票",
}


def validate_buyer_name(name: str) -> Tuple[bool, str]:
    if not name or not name.strip():
        return False, "姓名不能为空"
    stripped = name.strip()
    if len(stripped) < 2:
        return False, "姓名至少 2 个字符"
    if re.search(r"[0-9@#$%^&*()]", stripped):
        return False, "姓名包含非法字符"
    return True, stripped


def validate_id_card(id_card: str) -> Tuple[bool, str]:
    if not id_card or not id_card.strip():
        return False, "身份证号不能为空"
    card = id_card.strip().upper()
    if not re.match(r"^\d{17}[\dX]$", card):
        return False, "身份证号格式错误 (应为18位)"
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_map = "10X98765432"
    try:
        digits = [int(c) for c in card[:17]]
        checksum = sum(w * d for w, d in zip(weights, digits)) % 11
        if check_map[checksum] != card[17]:
            return False, "身份证号校验位不正确"
    except (ValueError, IndexError):
        return False, "身份证号格式错误"
    return True, card


def validate_phone(phone: str) -> Tuple[bool, str]:
    if not phone or not phone.strip():
        return True, ""
    p = phone.strip()
    if not re.match(r"^1[3-9]\d{9}$", p):
        return False, "手机号格式错误 (11位, 1开头)"
    return True, p


def validate_buyer(name: str, id_card: str, phone: str = "",
                    id_bind: int = 0, buyer_info: str = "") -> Tuple[bool, list]:
    """
    校验购票人信息
    返回 (通过, [错误列表])
    id_bind: 0=不实名, 1=可选, 2=强制
    buyer_info: "2,1" 格式表明需要身份证+手机
    """
    errors = []

    ok, result = validate_buyer_name(name)
    if not ok:
        errors.append(result)
    else:
        name = result

    need_id = id_bind > 0 or "2" in (buyer_info or "")
    need_phone = "1" in (buyer_info or "")

    if need_id:
        ok, result = validate_id_card(id_card)
        if not ok:
            errors.append(result)
        else:
            id_card = result

    if need_phone:
        ok, result = validate_phone(phone)
        if not ok:
            errors.append(result)
        else:
            phone = result

    return len(errors) == 0, errors


def generate_ctoken(ticket_collection_t=None, stay_time=0,
                     is_create_v2=False, time_offset=0):
    """生成 ctoken 浏览器指纹 (16字节定长, 参考 biliTickerBuy)

    格式: 16字节 buffer → to_binary 二次编码 (byte→uint16→uint8)
    参数来源于手机端抓包。

    is_create_v2=False: prepare 阶段 (touch随机, timer=stay)
    is_create_v2=True:  createV2 阶段 (page_unload=25, timer=delta+stay)
    """
    tc = ticket_collection_t or time.time()

    buf = bytearray(16)

    if is_create_v2:
        time_diff = int(time.time() + time_offset - tc)
        timer_val = int(time_diff + stay_time)
        buf[0] = 255 & 0xFF           # touch_event
        buf[2] = 2 & 0xFF             # visibility_change
        buf[4] = 255 & 0xFF           # inner_width
        buf[5] = 25 & 0xFF            # page_unload
        buf[6] = 255 & 0xFF           # inner_height
        buf[7] = 255 & 0xFF           # outer_width
        buf[8] = (timer_val >> 8) & 0xFF
        buf[9] = timer_val & 0xFF
        buf[10] = (time_diff >> 8) & 0xFF
        buf[11] = time_diff & 0xFF
        buf[12] = 255 & 0xFF          # outer_height
        buf[13] = 0 & 0xFF            # screen_x
        buf[14] = 0 & 0xFF            # screen_y
        buf[15] = 255 & 0xFF          # screen_width
    else:
        import random
        timer_val = int(stay_time)
        r = random.randint(1000, 3000)
        buf[0] = random.randint(3, 10) & 0xFF  # touch_event (随机)
        buf[2] = 2 & 0xFF
        buf[4] = 255 & 0xFF
        buf[6] = 255 & 0xFF
        buf[7] = 255 & 0xFF
        buf[8] = (timer_val >> 8) & 0xFF
        buf[9] = timer_val & 0xFF
        buf[12] = 255 & 0xFF
        buf[15] = 255 & 0xFF
        # 条件值: scroll_y 或 screen_avail_width
        condition = r & 4  # screen_height 模拟
        buf[1] = (r if condition else random.randint(1, 100)) & 0xFF
        buf[3] = (r if condition else random.randint(1, 100)) & 0xFF

    return _to_binary(buf)


def _to_binary(data: bytearray) -> str:
    """二次编码: byte → uint16 → uint8 (参考 biliTickerBuy CTokenGenerator.to_binary)

    每个原始字节扩展为2字节: [val & 0xFF, (val >> 8) & 0xFF]
    效果: 16字节输入 → 32字节 → base64
    """
    result = bytearray()
    for b in data:
        result.append(b & 0xFF)
        result.append(0)  # upper byte always 0 (input bytes < 256)
    return base64.b64encode(bytes(result)).decode()


APP_KEY = "1d8b6e7d45233436"
APP_SECRET = "560c52ccd288fed045859ed18bffd973"


def _app_sign(params: dict) -> dict:
    """B站 App 请求签名 (HMAC-MD5)"""
    import hashlib
    p = {"appkey": APP_KEY, **params}
    p = dict(sorted(p.items()))
    qs = "&".join(f"{k}={v}" for k, v in p.items())
    sign = hashlib.md5((qs + APP_SECRET).encode()).hexdigest()
    p["sign"] = sign
    return p


class BiliTicketAPI:
    """B站会员购票务 API 客户端"""

    # Android App 设备型号池 (来自 BHYG)
    _DEVICE_MODELS = {
        "OnePlus": ["PKR110","PJD110","PJZ110","PKU110","PJA110","PJF110","PJX110"],
        "IQOO": ["V2329A", "V2408A", "V2307A", "V2304A", "V2254A"],
        "HONOR": ["DVD-AN00", "PTP-AN20", "ROD2-W69", "ROD2-W09", "ROL-W00"],
        "Vivo": ["V2324A", "V2229A", "V2241A", "V2359A", "V2454A"],
        "OPPO": ["PFFM20", "PJJ110", "PJW110", "PKM110", "PHU110"],
        "Realme": ["RMX5060", "RMX3946", "RMX3948", "RMX5010"],
    }

    def __init__(self, cookie: str = "", config: Optional[dict] = None, app_mode: bool = True):
        cfg = config or load_config()
        self.base_url = cfg["base_url"]
        self.version = cfg["version"]
        self.timeout = cfg["request_timeout"]
        self.max_retries = cfg["max_retries"]
        self.cookie = cookie or cfg.get("cookie", "")
        self.config = cfg
        self.app_mode = app_mode

        # App 模式: 随机选一台真机型号
        if app_mode:
            brand = random.choice(list(self._DEVICE_MODELS.keys()))
            model = random.choice(self._DEVICE_MODELS[brand])
            self._app_brand = brand
            self._app_model = model
            self._app_version = "8350200"
            self._app_version_name = "8.35.0"
            self._screen_info = "362*795*24"
            self._build_id = f"{random.choice('AB')}P{random.randint(1,4)}A.240{random.randint(1,9)}{random.randint(1,2)}{random.randint(1,9)}.0{random.randint(1,2)}{random.randint(1,9)}"

            self.ua = (
                f"Mozilla/5.0 (Linux; Android 15; {model} Build/{self._build_id}; wv) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                f"Chrome/135.0.7049.{random.randint(1,150)} Mobile Safari/537.36 "
                f"BiliApp/{self._app_version} mobi_app/android "
                f"isNotchWindow/1 NotchHeight={random.randint(20,40)} "
                f"mallVersion/{self._app_version} mVersion/296 "
                f"disable_rcmd/0 "
                f"magent/BILI_H5_ANDROID_15_{self._app_version_name}_{self._app_version}"
            )
        else:
            self.ua = cfg["user_agent"]
            self._app_brand = ""
            self._app_model = ""
            self._app_version = ""

        self._proxy_rotator = ProxyRotator(cfg)

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.ua,
            "Referer": "https://show.bilibili.com/",
            "Origin": "https://show.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "close",
        })

        if self.cookie:
            self._set_cookie(self.cookie)

        # App 模式: 注入设备指纹 Cookie
        if app_mode:
            self._init_app_cookies()
            self._update_app_sign()

        self.csrf = self._extract_csrf()
        self.user_info = {}
        self.auth_verified = cfg.get("auth", {}).get("verified", False)

    def _update_app_sign(self):
        """注入 App 签名 Cookie"""
        from urllib.parse import urlencode, quote
        ts = int(time.time() * 1000)
        signed = _app_sign({"ts": ts})
        identify = quote(urlencode(signed))
        self.session.cookies.set("identify", identify)

    def _init_app_cookies(self):
        """注入 Android App 设备指纹 Cookie (BHYG 方案)"""
        fps = {
            "canvasFp": hashlib.md5(str(random.random()).encode()).hexdigest(),
            "webglFp": hashlib.md5(str(random.random()).encode()).hexdigest(),
            "feSign": hashlib.md5(str(random.random()).encode()).hexdigest(),
        }
        for name, val in fps.items():
            self.session.cookies.set(name, val)

        self.session.cookies.set("msource", "bilibiliapp")
        self.session.cookies.set("kfcSource", "bilibiliapp")
        self.session.cookies.set("deviceFingerprint", self._extract_device_id())
        self.session.cookies.set("screenInfo", self._screen_info)

    def _gen_risk_header(self) -> str:
        """生成 x-risk-header (App 模式)"""
        if not self.app_mode:
            uid = self.config.get("auth", {}).get("uid", 0)
            return f"platform/pc uid/{uid} deviceId/{self._extract_device_id()}"

        uid = self.config.get("auth", {}).get("uid", 0)
        buvid = self.session.cookies.get("buvid3", "") or self._extract_device_id()
        parts = [
            f"appkey/1d8b6e7d45233436",
            f"brand/{self._app_brand}",
            f"localBuvid/{buvid}",
            f"model/{self._app_model}",
            f"osver/15",
            f"platform/h5",
            f"uid/{uid}",
            f"channel/1",
            f"deviceId/{self._extract_device_id()}",
            f"sLocale/zh_CN",
            f"cLocale/zh_CN",
            f"mallVersion/{self._app_version}",
            f"mVersion/296",
        ]
        return " ".join(parts)

    def _set_cookie(self, cookie_str: str) -> None:
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                self.session.cookies.set(k.strip(), v.strip())

    def _extract_csrf(self) -> str:
        for cookie in self.session.cookies:
            if cookie.name == "bili_jct":
                return cookie.value
        return ""

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout)
        # 仅下单接口使用代理 (避免非下单请求浪费代理池)
        if "order/" in path or "createV2" in path:
            self._update_app_sign()
            proxy = self._proxy_rotator.next()
            if proxy:
                kwargs["proxies"] = proxy
        for attempt in range(self.max_retries):
            try:
                resp = self.session.request(method, url, **kwargs)
                ct = resp.headers.get("content-type", "")
                if "json" not in ct:
                    return {"errno": -1, "msg": f"非JSON响应 HTTP {resp.status_code}"}
                data = resp.json()
                return data
            except requests.Timeout:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(0.5)
            except requests.ConnectionError:
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(1)
            except json.JSONDecodeError:
                if attempt == self.max_retries - 1:
                    return {"errno": -1, "msg": f"JSON解析失败 HTTP {resp.status_code}"}
                time.sleep(0.5)
        return {}

    # ── 鉴权 ─────────────────────────────────────────────────

    @staticmethod
    def qrcode_login(show_func=None) -> Tuple[bool, str]:
        """扫码登录 — 使用 App UA 获取 App 类型 Cookie"""
        session = requests.Session()
        # 用 Android App UA (和主 session 一致)
        ua = (
            "Mozilla/5.0 (Linux; Android 15; PKR110 Build/AQ3A.240912.001; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/131.0.6778.260 Mobile Safari/537.36 "
            "BiliApp/8350200 mobi_app/android"
        )
        session.headers.update({
            "User-Agent": ua,
            "Referer": "https://www.bilibili.com",
        })

        r = session.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
            timeout=10)
        try:
            data = r.json()
        except Exception:
            return False, "B站接口异常"
        if data.get("code") != 0:
            return False, f"生成二维码失败: {data.get('message')}"

        qr_url = data["data"]["url"]
        qrcode_key = data["data"]["qrcode_key"]

        if show_func:
            show_func(qr_url)

        print(f"  [扫码] 请用 B站 App 扫描二维码")
        last_status = None

        while True:
            time.sleep(1.5)
            try:
                resp = session.get(
                    "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                    params={"qrcode_key": qrcode_key}, timeout=10)
                inner = resp.json().get("data", {})
            except Exception as e:
                return False, f"轮询异常: {e}"

            code = inner.get("code", -1)
            if code != last_status:
                last_status = code
                status_map = {86101: "等待扫码...", 86090: "已扫码, 请在手机上确认",
                              86038: "二维码已过期", 0: "登录成功!"}
                print(f"  [{time.strftime('%H:%M:%S')}] "
                      f"{status_map.get(code, inner.get('message', '未知'))}")

            if code == 0:
                cookies = dict(session.cookies)
                # 从确认 URL 提取额外 Cookie
                if inner.get("url"):
                    from urllib.parse import urlparse, parse_qs
                    qs = parse_qs(urlparse(inner["url"]).query)
                    for k, v in qs.items():
                        if k not in cookies:
                            cookies[k] = v[0]

                cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
                cfg = load_config()
                cfg["cookie"] = cookie_str
                save_config(cfg)
                return True, cookie_str

            if code == 86038:
                return False, "二维码已过期"

    @staticmethod
    def playwright_login(show_func=None) -> Tuple[bool, str]:
        """Playwright 登录 (备用)"""
        return BiliTicketAPI.qrcode_login(show_func)

    def verify_auth(self) -> Tuple[bool, str, dict]:
        """验证 Cookie 是否有效, 返回 (是否成功, 用户名, 用户信息)"""
        if not self.cookie:
            return False, "未设置 Cookie", {}

        info = self.get_user_info()
        if info.get("code") != 0 and info.get("errno") != 0:
            return False, info.get("message", info.get("msg", "登录验证失败")), {}

        user = info.get("data", {})
        uname = user.get("uname", user.get("name", ""))
        uid = user.get("mid", 0)

        self.user_info = user
        self.auth_verified = True

        auth_data = {"uid": uid, "uname": uname, "verified": True}
        saved_cfg = load_config()
        saved_cfg["auth"] = auth_data
        save_config(saved_cfg)

        return True, uname, user

    def is_authenticated(self) -> bool:
        return self.auth_verified and bool(self.csrf)

    # ── 项目查询 ─────────────────────────────────────────────

    def get_project_detail(self, project_id: int) -> dict:
        return self._request("GET",
            f"/api/ticket/project/getV2?version={self.version}&id={project_id}")

    def get_project_summary(self, project_id: int) -> Optional[dict]:
        detail = self.get_project_detail(project_id)
        if detail.get("code") != 0 and detail.get("errno") not in (0, None):
            return None
        return detail.get("data", {})

    def get_project_skus(self, project_id: int) -> list:
        detail = self.get_project_detail(project_id)
        if detail.get("code") != 0:
            print(f"[错误] 获取项目详情失败: {detail.get('message', detail)}")
            return []

        data = detail.get("data", {})
        if not data:
            return []

        project_type = PROJECT_TYPE_MAP.get(data.get("type", 0), "未知")
        skus = []
        for screen in data.get("screen_list", []):
            screen_ticket_type = SCREEN_TICKET_TYPE_MAP.get(
                screen.get("ticket_type", 0), "未知")
            screen_info = {
                "screen_id": screen["id"],
                "screen_name": screen["name"],
                "screen_sale_flag": SALE_FLAG_MAP.get(
                    screen.get("saleFlag", {}).get("number", 0), "未知"),
                "screen_sale_number": screen.get("saleFlag", {}).get("number", 0),
                "screen_clickable": screen.get("clickable", False),
                "screen_start_time": screen.get("start_time_str", ""),
                "screen_start_ts": screen.get("start_time", 0),
                "delivery_type": screen.get("delivery_type", 1),
                "delivery_name": DELIVERY_TYPE_MAP.get(
                    screen.get("delivery_type", 1), "未知"),
                "project_type": project_type,
                "ticket_type": screen_ticket_type,
            }
            for ticket in screen.get("ticket_list", []):
                sfn = ticket.get("sale_flag_number", 0)
                skus.append({
                    **screen_info,
                    "sku_id": ticket["id"],
                    "price": ticket["price"],
                    "price_yuan": ticket["price"] / 100,
                    "desc": ticket["desc"],
                    "sale_flag": SALE_FLAG_MAP.get(sfn, "未知"),
                    "sale_flag_number": sfn,
                    "clickable": ticket.get("clickable", False),
                    "num": ticket.get("num", 0),
                    "sale_start": ticket.get("sale_start", ""),
                    "sale_end": ticket.get("sale_end", ""),
                    "limit_num": ticket.get("static_limit", {}).get("num", 0),
                    "white_sku": ticket.get("whiteSku", None),
                })
        return skus

    def check_ticket_available(self, project_id: int,
                                sku_id: int = 0,
                                screen_id: int = 0,
                                min_price: int = 0,
                                max_price: int = 99999999) -> tuple:
        detail = self.get_project_detail(project_id)
        if detail.get("code") != 0:
            print(f"[错误] {detail.get('message', detail)}")
            return {}, []

        proj = detail.get("data", {})
        project_type = PROJECT_TYPE_MAP.get(proj.get("type", 0), "未知")
        project_status = {
            "name": proj.get("name", ""),
            "sale_flag": proj.get("sale_flag", ""),
            "sale_flag_number": proj.get("sale_flag_number", 0),
            "is_sale": proj.get("is_sale", 0),
            "can_click": proj.get("canClick", False),
            "default_button": proj.get("default_button", 0),
            "id_bind": proj.get("id_bind", 0),
            "buyer_info": proj.get("buyer_info", ""),
            "project_type": project_type,
            "venue": proj.get("venue_info", {}).get("name", ""),
            "price_low": proj.get("price_low", 0),
            "price_high": proj.get("price_high", 0),
        }

        available = []
        for screen in proj.get("screen_list", []):
            for ticket in screen.get("ticket_list", []):
                sn = ticket.get("sale_flag_number", 0)
                if sn != 2:
                    continue
                if sku_id and ticket["id"] != sku_id:
                    continue
                if screen_id and screen["id"] != screen_id:
                    continue
                if ticket["price"] < min_price or ticket["price"] > max_price:
                    continue
                available.append({
                    "sku_id": ticket["id"],
                    "screen_id": screen["id"],
                    "screen_name": screen["name"],
                    "screen_type": SCREEN_TICKET_TYPE_MAP.get(
                        screen.get("ticket_type", 0), "未知"),
                    "desc": ticket["desc"],
                    "price": ticket["price"],
                    "price_yuan": ticket["price"] / 100,
                    "num": ticket.get("num", 0),
                    "sale_start": ticket.get("sale_start", ""),
                    "sale_end": ticket.get("sale_end", ""),
                    "ticket_type": project_type,
                })
        return project_status, available

    def get_user_info(self) -> dict:
        return self._request("GET",
            f"/api/ticket/user/info?version={self.version}")

    def get_buyers_list(self) -> list:
        """获取B站已绑定的实名观演人"""
        data = self._request("GET", "/api/ticket/buyer/list")
        if data.get("code") != 0 and data.get("errno") != 0:
            return []
        result = []
        for buyer in data.get("data", {}).get("list", []) or []:
            bid = buyer.get("id", "")
            name = buyer.get("name", "")
            tel = buyer.get("tel", buyer.get("phone", ""))
            id_card = buyer.get("id_card", buyer.get("personal_id", ""))
            if bid and name:
                result.append({"id": str(bid), "name": name,
                                "tel": str(tel), "id_card": str(id_card)})
        return result

    def add_buyer(self, name: str, id_card: str, phone: str = "") -> dict:
        """添加B站实名观演人"""
        return self._request("POST", "/api/ticket/buyer/create",
            json={"name": name, "id_card": id_card, "phone": phone})

    def delete_buyer(self, buyer_id: str) -> dict:
        """删除B站实名观演人"""
        return self._request("POST", "/api/ticket/buyer/delete",
            json={"id": buyer_id})

    # ── 验证码处理 ────────────────────────────────────────────

    def check_captcha(self, project_id: int, screen_id: int) -> bool:
        """检查是否需要验证码, 返回 True 表示需要"""
        ts = int(time.time() * 1000)
        data = self._request("GET",
            f"/api/ticket/graph/prepare?project_id={project_id}"
            f"&screen_id={screen_id}&timestamp={ts}")
        return bool(data.get("data")) and data.get("code") == 0

    def solve_captcha(self, project_id: int, screen_id: int) -> Optional[str]:
        """求解验证码, 返回 voucher (失败返回 None)

        注意: 完整验证码求解需要集成 GeeTest 自动滑块库。
        目前返回 None 表示需要手动处理。
        """
        ts = int(time.time() * 1000)
        data = self._request("GET",
            f"/api/ticket/graph/prepare?project_id={project_id}"
            f"&screen_id={screen_id}&timestamp={ts}")
        if data.get("code") != 0:
            return None

        cap_data = data.get("data", {})
        if not cap_data:
            return None

        print(f"[验证码] 检测到验证码: {json.dumps(cap_data, ensure_ascii=False)[:200]}")
        print(f"[验证码] 请在浏览器中手动完成验证码后重试")
        return None

    CREATE_ORDER_URL = "https://show.bilibili.com/api/ticket/order/createV2"

    def _extract_device_id(self) -> str:
        for cookie in self.session.cookies:
            if cookie.name == "deviceFingerprint":
                return cookie.value
        for cookie in self.session.cookies:
            if cookie.name == "buvid_fp":
                return cookie.value
        return str(uuid.uuid4()).replace("-", "")

    def _generate_click_position(self) -> str:
        now_ms = int(time.time() * 1000)
        origin = now_ms - 7230
        return json.dumps({"x": 255, "y": 730, "origin": origin, "now": now_ms},
                          separators=(",", ":"))

    def create_order_v2(self, project_id: int, screen_id: int,
                         sku_id: int, buy_num: int = 1,
                         device_id: str = "", buyer_name: str = "",
                         buyer_phone: str = "", buyer_id_card: str = "",
                         pay_money: int = 0, token: str = "",
                         ptoken: str = "", id_bind: int = 0,
                         order_type: int = 1,
                         ctoken: str = "",
                         captcha_voucher: str = "",
                         with_ptoken: bool = True) -> dict:
        """createV2 下单 (完整版, 参考 BHYG)

        token/ptoken: 从 prepare 接口获取
        ctoken: 浏览器指纹 (用于热门项目)
        captcha_voucher: 验证码凭证 (如果触发)
        """
        if not device_id:
            device_id = self._extract_device_id()
        if not ctoken:
            ctoken = generate_ctoken(ticket_collection_t=time.time(),
                                      stay_time=random.randint(2000, 10000),
                                      is_create_v2=True)

        payload = {
            "project_id": project_id,
            "screen_id": screen_id,
            "sku_id": sku_id,
            "count": buy_num,
            "pay_money": pay_money,
            "order_type": order_type,
            "timestamp": int(time.time() * 1000),
            "id_bind": id_bind,
            "need_contact": 1 if id_bind == 0 else 0,
            "is_package": 0,
            "package_num": 1,
            "token": token,
            "deviceId": device_id,
            "version": "1.1.0",
            "coupon_code": "",
            "again": 0,
            "clickPosition": {
                "x": random.randint(200, 400),
                "y": random.randint(750, 800),
                "origin": int(time.time() * 1000) - random.randint(5000, 8000),
                "now": int(time.time() * 1000),
            },
            "ctoken": ctoken,
            "requestSource": "neul-next",
            "newRisk": True,
        }

        if id_bind == 0:
            payload["contactInfo"] = {
                "uid": int(self.config.get("auth", {}).get("uid", 0)),
                "username": buyer_name,
                "tel": buyer_phone,
            }
            payload["buyer"] = buyer_name
            payload["tel"] = buyer_phone
        else:
            # id_bind=2: 需要 B站 注册的 buyer ID
            # buyer_info 格式: [{"id": "12720803", "name": "邓恺璐", ...}]
            buyers = self.get_buyers_list()
            matched = [b for b in buyers if b.get("id_card") == buyer_id_card or b.get("name") == buyer_name]
            if matched:
                payload["buyer_info"] = json.dumps(matched[:buy_num], ensure_ascii=False)
            elif buyer_name and buyer_id_card:
                payload["buyer_info"] = json.dumps([{
                    "name": buyer_name,
                    "id_card": buyer_id_card,
                    "phone": buyer_phone,
                }], ensure_ascii=False)
            else:
                payload["buyer_info"] = ""

        if ptoken:
            payload["ptoken"] = ptoken
        if captcha_voucher:
            payload["voucher"] = captcha_voucher

        headers = self.session.headers.copy()
        headers["x-risk-header"] = self._gen_risk_header()
        if token:
            headers["Referer"] = (
                f"https://show.bilibili.com/platform/confirmOrder.html"
                f"?token={token}&project_id={project_id}"
            )

        url = f"/api/ticket/order/createV2?project_id={project_id}"
        if ptoken:
            url += f"&ptoken={ptoken}"

        return self._request("POST", url, headers=headers, json=payload)

    def prepare_order(self, project_id: int, screen_id: int,
                       sku_id: int, buy_num: int = 1,
                       buyer_info: str = "",
                       order_type: int = 1,
                       device_id: str = "") -> dict:
        """准备订单, 获取 token + ptoken

        参考 BHYG 的 prepare_token 实现。
        返回 data 中包含 token 和 ptoken。
        """
        if not device_id:
            device_id = self._extract_device_id()

        ctoken = generate_ctoken(ticket_collection_t=time.time(),
                                  stay_time=random.randint(2000, 10000),
                                  is_create_v2=False)

        payload = {
            "project_id": project_id,
            "screen_id": screen_id,
            "order_type": order_type,
            "count": buy_num,
            "sku_id": sku_id,
            "buyer_info": buyer_info,
            "token": ctoken,
            "ignoreRequestLimit": True,
            "ticket_agent": "",
            "newRisk": True,
            "requestSource": "neul-next",
        }
        return self._request("POST",
            f"/api/ticket/order/prepare?project_id={project_id}",
            json=payload)

    def get_order_status(self, order_id: str) -> dict:
        return self._request("GET",
            f"/api/ticket/order/orderInfo?version={self.version}&order_id={order_id}")

    # ── 响应分类 (用于退避策略) ────────────────────────────────

    @staticmethod
    def classify_response(data: dict) -> Tuple[bool, str]:
        """根据接口返回判断成功/失败及原因

        返回 (是否成功, 原因描述)

        退避策略:
          - "请慢一点" → 指数退避 (500ms → 1000ms → 2000ms)
          - "尚未开售/不可售" → 继续等
          - "库存不足" → 停止
          - "风控/验证码" → 停止
          - "登录异常" → 停止
        """
        code = data.get("code")
        errno = data.get("errno")
        msg = str(data.get("message") or data.get("msg") or "")

        if code == 0 or errno == 0:
            return True, "成功"

        if code == 100048 or errno == 100048:
            return True, "成功(已有未完成订单)"

        if any(k in msg for k in ("联系人信息", "姓名及手机号", "手机号",
                                        "购买人信息", "请选择", "没有选择")):
            return False, "NEED_CONTACT"

        text = msg.lower()
        if "请慢一点" in msg or "前方拥堵" in msg:
            return False, "RATE_LIMIT"
        if any(k in msg for k in ("未开始", "未开售", "不可售", "未到开售")):
            return False, "NOT_STARTED"
        if any(k in msg for k in ("库存", "售罄", "卖光", "已抢光")):
            return False, "SOLD_OUT"
        if any(k in msg for k in ("登录", "账号", "cookie", "鉴权")):
            return False, "AUTH_ERROR"
        if any(k in msg for k in ("风控", "验证码", "滑块")):
            return False, "CAPTCHA"
        if any(k in text for k in ("risk", "captcha")):
            return False, "CAPTCHA"

        return False, f"code={code} errno={errno}"

    # ── 抢票引擎 (dry-run + 指数退避 + 开售等待) ─────────────

    def sniper_buy(self, project_id: int, sku_id: int,
                    screen_id: int = 0, buy_num: int = 1,
                    buyer_name: str = "", buyer_phone: str = "",
                    buyer_id_card: str = "",
                    pay_money: int = 0, dry_run: bool = True,
                    token: str = "", id_bind: int = 0,
                    order_type: int = 1,
                    with_ptoken: bool = True,
                    wait_sale: bool = False, sale_time_str: str = "",
                    poll_interval: float = None,
                    max_retry_per_token: int = 60) -> Optional[dict]:
        """抢票引擎

        dry_run: True=仅打印 payload, False=prepare → createV2 真实下单
        token: 已有 token (跳过 prepare), 为空则自动调用 prepare 获取
        """
        if poll_interval is None:
            poll_interval = load_config().get("poll_interval", 0.3)
        cfg = load_config()
        device_id = self._extract_device_id()
        ctoken = generate_ctoken(ticket_collection_t=time.time(),
                                  stay_time=random.randint(2000, 10000),
                                  is_create_v2=False)

        # 缓存项目信息 (用于通知)
        status, avail = self.check_ticket_available(project_id, sku_id=sku_id)
        self._last_proj_name = status.get("name", "")
        if avail:
            self._last_ticket_desc = avail[0].get("desc", "")

        # 定时等待 (dry-run 和 real 都等)
        if wait_sale and sale_time_str:
            try:
                st = datetime.fromisoformat(sale_time_str.replace(" ", "T"))
                self._wait_until_sale(st)
            except Exception as e:
                print(f"[警告] 开售时间解析失败: {e}")

        if dry_run:
            payload = {
                "project_id": project_id, "screen_id": screen_id,
                "sku_id": sku_id, "count": buy_num,
                "pay_money": pay_money, "order_type": order_type,
                "id_bind": id_bind, "need_contact": 1 if id_bind == 0 else 0,
                "buyer": buyer_name, "tel": buyer_phone,
                "deviceId": device_id, "token": token or "(auto via prepare)",
                "ctoken": ctoken, "clickPosition": "...",
                "requestSource": "neul-next", "newRisk": True,
                "version": "1.1.0",
            }
            if id_bind != 0 and buyer_id_card:
                payload["buyer_info"] = json.dumps([{
                    "name": buyer_name, "id_card": buyer_id_card,
                    "phone": buyer_phone,
                }])
            print(f"\n{' DRY-RUN 模拟下单 ':━^60}")
            print(f"  流程: prepare (获取token) → createV2 (下单)")
            print(f"  createV2 URL: /api/ticket/order/createV2?project_id={project_id}")
            print(f"  Payload:")
            for k, v in payload.items():
                print(f"    {k}: {v}")
            print(f"  (未真实提交, 使用 --real 参数可真实下单)")
            if not token:
                print(f"  (token 为空时将自动调用 prepare 获取)")
            return {"dry_run": True, "payload": payload}

        _token = token
        _ptoken = ""
        _voucher = ""
        attempt = 0
        round_num = 0

        while True:
            attempt += 1
            try:
                if not _token:
                    print(f"[prepare #{attempt}] 获取 token...")
                    prep = self.prepare_order(
                        project_id, screen_id, sku_id, buy_num,
                        order_type=order_type,
                    )
                    prep_code = prep.get("code")
                    prep_errno = prep.get("errno")
                    if prep_code == 0 or prep_errno == 0:
                        prep_data = prep.get("data", {})
                        _token = prep_data.get("token", "")
                        _ptoken = prep_data.get("ptoken", "")
                        print(f"[prepare OK] token={_token[:20]}..."
                              f"{' ptoken=' + _ptoken[:10] + '...' if _ptoken else ''}")
                    elif prep_errno == 412 or prep_errno == 429:
                        print(f"[prepare {prep_errno}] 重试 {poll_interval}s")
                        time.sleep(poll_interval)
                        continue
                    else:
                        print(f"[prepare fail #{attempt}] "
                              f"code={prep_code} msg={prep.get('message')}")
                        if prep_code == 83000005:
                            print(f"[prepare] 项目可能未开售或参数错误")
                            return None
                        time.sleep(poll_interval)
                        continue

                print(f"[下单 #{attempt}] createV2...")
                order = self.create_order_v2(
                    project_id, screen_id, sku_id, buy_num,
                    device_id=device_id,
                    buyer_name=buyer_name,
                    buyer_phone=buyer_phone,
                    buyer_id_card=buyer_id_card,
                    pay_money=pay_money,
                    token=_token,
                    ptoken=_ptoken,
                    id_bind=id_bind,
                    order_type=order_type,
                    ctoken=ctoken,
                    captcha_voucher=_voucher,
                )

                ok, reason = self.classify_response(order)
                code = order.get("code")
                errno = order.get("errno")
                msg = order.get("message", order.get("msg", ""))

                if ok:
                    od = order.get("data", {})
                    oid = od.get("orderId", od.get("order_id", ""))
                    pay = od.get("pay_money", od.get("payMoney", pay_money))
                    print(f"[成功] order_id={oid} 金额=¥{pay/100:.2f}")
                    self._notify_success(order, buyer_name,
                        project_name=self._last_proj_name,
                        ticket_desc=self._last_ticket_desc,
                        count=buy_num, total_price=pay or pay_money)
                    return {"order_id": oid, **od}

                if reason == "RATE_LIMIT" or code in (412, 429) or errno in (412, 429):
                    print(f"[#{attempt}] code={code or errno} {msg[:40]}")
                    time.sleep(poll_interval)
                    continue

                if reason == "NOT_STARTED":
                    print(f"[#{attempt}] {msg} → 重新prepare")
                    break

                if reason == "NEED_CONTACT":
                    print(f"[#{attempt}] {msg}")
                    return None

                if reason == "SOLD_OUT":
                    print(f"[#{attempt}] {msg}")
                    return None

                if reason == "AUTH_ERROR":
                    print(f"[#{attempt}] {msg}")
                    return None

                if reason == "CAPTCHA" or code == 100044 or errno == 100044:
                    _voucher = self.solve_captcha(project_id, screen_id)
                    if _voucher:
                        print(f"[验证码] 已解决, 重试...")
                        continue
                    return None

                if code == 100051 or errno == 100051:
                    print(f"[#{attempt}] token过期 → 重新prepare")
                    break

                if code == 100034 or errno == 100034:
                    new_price = order.get("data", {}).get("pay_money", pay_money)
                    if new_price and new_price != pay_money:
                        print(f"[#{attempt}] 票价 {pay_money}→{new_price}")
                        pay_money = new_price
                    break

                if code in (100001, 100009) or errno in (100001, 100009):
                    print(f"[#{attempt}] code={code or errno} {msg[:40]} → 重新prepare")
                    break

                if code == -401 or errno == -401:
                    print(f"[GAIA] 触发反机器人验证")
                    return None

                # 412/429/900001/3 等全部不退避, 固定间隔死磕
                print(f"[#{attempt}] code={code or errno} {msg[:40]}")
                # 被限流时动态加长间隔
                delay = poll_interval
                if code == 1 or "请慢一点" in msg:
                    delay = poll_interval + random.uniform(0.5, 1.0)
                elif code == 900001 or "拥堵" in msg:
                    delay = poll_interval + random.uniform(0.3, 0.8)
                time.sleep(delay)

            except requests.Timeout:
                print(f"[超时 #{attempt}]")
                time.sleep(poll_interval)
            except requests.ConnectionError:
                print(f"[网络 #{attempt}]")
                time.sleep(1)
            except KeyboardInterrupt:
                print("\n[中断]")
                return None

            # token 耗尽 → 重新 prepare
            _token = ""
            _ptoken = ""

    @staticmethod
    def _wait_until_sale(target_time: datetime) -> None:
        """自适应等待到开售时间

        距离开售 >60s: 每 15s 检查
        距离开售 >10s: 每 2s 检查
        距离开售 >1s:  每 0.1s 检查
        距离开售 <=1s: 每 3ms 检查 (高精度)
        """
        print(f"\n[等待] 目标开售时间: {target_time.strftime('%Y-%m-%d %H:%M:%S')}")
        last_remain = None
        while True:
            remain = (target_time - datetime.now()).total_seconds()
            if remain <= 0:
                print("[开售] 时间到, 开始下单!")
                return

            # 只在变化明显时打印
            if last_remain is None or abs(remain - last_remain) > 0.5:
                print(f"[等待] 距离开售 {remain:.0f}s")
                last_remain = remain

            if remain > 60:
                time.sleep(15)
            elif remain > 10:
                time.sleep(2)
            elif remain > 1:
                time.sleep(0.1)
            else:
                time.sleep(0.003)

    def _notify_success(self, order: dict, buyer_name: str,
                         project_name: str = "", ticket_desc: str = "",
                         count: int = 1, total_price: int = 0) -> None:
        """发送抢票成功通知 + 获取支付链接"""
        try:
            from notify import send_order_success
            od = order.get("data", order)
            oid = od.get("orderId", od.get("order_id", ""))

            # 获取支付链接
            pay_url = ""
            try:
                r = self._request("GET",
                    f"/api/ticket/order/getPayParam?order_id={oid}")
                if r.get("errno", r.get("code")) == 0:
                    pay_url = (r.get("data", {}) or {}).get("code_url", "")
            except Exception:
                pass

            send_order_success(
                od,
                project_name=project_name,
                ticket_desc=ticket_desc,
                buyer_name=buyer_name,
                count=count, total_price=total_price,
                pay_url=pay_url,
                config=self.config.get("notification", {}),
            )
        except Exception as e:
            print(f"[通知] 发送失败: {e}")


def load_buyers() -> list:
    if os.path.exists(BUYERS_FILE):
        with open(BUYERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_buyers(buyers: list) -> None:
    with open(BUYERS_FILE, "w", encoding="utf-8") as f:
        json.dump(buyers, f, ensure_ascii=False, indent=2)
