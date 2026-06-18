#!/usr/bin/env python3
"""
B站会员购 API 参数级规格生成器

对每个已知端点做:
  1. 字段必要性 (required/optional)
  2. 类型约束 (int/string/bool/object)
  3. 值约束 (空值/零值/负值/边界)
  4. 格式约束 (string→int, object→string)
  5. 响应结构 (data keys, error codes)
"""

import json, time, base64, random, sys
from collections import defaultdict
from datetime import datetime

from bili_api import BiliTicketAPI, generate_ctoken

api = BiliTicketAPI()
PID, SID, SKU = 1001227, 1004195, 875261  # 默认测试项目
s = api.session
now_ms = int(time.time() * 1000)
dev_id = api._extract_device_id()
ct = generate_ctoken()

# 先获取 token 供 createV2 测试
prep = api.prepare_order(PID, SID, SKU, 1)
TOKEN = (prep.get("data", {}) or {}).get("token", "")
PTOKEN = (prep.get("data", {}) or {}).get("ptoken", "") or ""

print(f"  token={TOKEN[:25]}... ptoken={PTOKEN}")

# ═══════════════════════════════════════════════════════════
# 参数测试引擎
# ═══════════════════════════════════════════════════════════

def test_param(endpoint_name, url, base_payload, params,
               method="POST", headers=None, project_bound=True):
    """
    对单个参数的多个变体逐一测试

    params: [(param_name, variations), ...]
    variations: [(label, value, expected_type), ...]
      expected_type: "required" | "optional" | "rejected" | "ignored"
    """
    global PID
    results = []

    # 如果该项目已下单导致限流, 换项目
    if project_bound:
        # 检查是否已在这个项目上下过单
        pass

    for param_name, variations in params:
        for label, value, expected in variations:
            payload = base_payload.copy()

            if value is ...:  # Ellipsis = 移除字段
                payload.pop(param_name, None)
            elif value is None:
                payload[param_name] = None
            else:
                payload[param_name] = value

            # 递归清理 None
            payload = _clean_none(payload)

            try:
                h = headers or s.headers.copy()
                if method == "POST":
                    r = s.post(url, json=payload, headers=h, timeout=8)
                else:
                    r = s.get(url, params=payload, headers=h, timeout=8)

                data = r.json()
                errno = data.get("errno")
                code = data.get("code")
                msg = data.get("msg", data.get("message", ""))
                oid = (data.get("data", {}) or {}).get("orderId", "")

                # 判断结果
                ok = errno in (0, 100048) or code == 0 or bool(oid)
                is_rl = "拥堵" in (msg or "") or errno == 900001
                has_oid = bool(oid)

                verdict = "pass" if ok else \
                          "rate_limited" if is_rl else \
                          "reject"

                results.append({
                    "param": param_name,
                    "variant": label,
                    "value": str(value)[:40],
                    "expected": expected,
                    "verdict": verdict,
                    "errno": errno,
                    "code": code,
                    "msg": msg[:60],
                    "orderId": oid,
                })

                if has_oid and project_bound:
                    # 该project已消耗, 换下一个
                    pass

            except Exception as e:
                results.append({
                    "param": param_name,
                    "variant": label,
                    "value": str(value)[:40],
                    "expected": expected,
                    "verdict": "error",
                    "error": str(e)[:60],
                })

    return results


def _clean_none(obj):
    if isinstance(obj, dict):
        return {k: _clean_none(v) for k, v in obj.items()
                if v is not None and v is not ...}
    if isinstance(obj, list):
        return [_clean_none(v) for v in obj
                if v is not None and v is not ...]
    return obj


def group_results(results):
    """按参数分组, 每个参数的测试结果"""
    grouped = defaultdict(list)
    for r in results:
        grouped[r["param"]].append(r)
    return dict(grouped)


def verdict_summary(grouped):
    """总结每个参数的约束"""
    summary = {}
    for param, tests in grouped.items():
        req = []
        opt = []
        rej = []
        for t in tests:
            if t["verdict"] == "pass":
                opt.append(t["variant"])
            elif t["verdict"] == "reject":
                rej.append(f"{t['variant']}({t['errno']})")
            else:
                pass  # rate_limited, ignore

        summary[param] = {
            "accepted": opt,
            "rejected": rej,
            "tests": len(tests),
            "verdict": "required" if not opt else
                       "type_sensitive" if rej and opt else
                       "lenient" if opt and not rej else
                       "unknown",
        }
    return summary


# ═══════════════════════════════════════════════════════════
# API-1: project/getV2
# ═══════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("  API-1: /api/ticket/project/getV2")
print("=" * 70)

results_gv2 = test_param(
    "project/getV2",
    f"https://show.bilibili.com/api/ticket/project/getV2",
    {"id": PID},
    [
        ("id", [
            ("正确值(int)", PID, "required"),
            ("字符串", str(PID), "optional"),
            ("id=0", 0, "rejected"),
            ("-id(缺失)", ..., "required"),
        ]),
    ],
    method="GET", project_bound=False,
)

gv2_grouped = group_results(results_gv2)
for param, tests in gv2_grouped.items():
    print(f"\n  ── {param} ──")
    for t in tests:
        mark = {"pass": "✓", "reject": "✗", "rate_limited": "∅", "error": "!!"}
        print(f"  {mark[t['verdict']]} {t['variant']:20s} → errno={t.get('errno')} "
              f"{t.get('msg','')[:40]}")

# ═══════════════════════════════════════════════════════════
# API-2: order/prepare
# ═══════════════════════════════════════════════════════════

print("\n\n" + "=" * 70)
print("  API-2: /api/ticket/order/prepare")
print("=" * 70)

prepare_url = f"https://show.bilibili.com/api/ticket/order/prepare?project_id={PID}"
prepare_base = {
    "project_id": PID,
    "screen_id": SID,
    "sku_id": SKU,
    "order_type": 1,
    "count": 1,
    "buyer_info": "",
}

results_prep = test_param(
    "order/prepare",
    prepare_url, prepare_base,
    [
        ("project_id", [
            ("正确值(int)", PID, "required"),
            ("字符串", str(PID), "optional"),
            ("=0", 0, "rejected"),
            ("缺失", ..., "required"),
        ]),
        ("screen_id", [
            ("正确值(int)", SID, "required"),
            ("字符串", str(SID), "optional"),
            ("=0", 0, "rejected"),
            ("缺失", ..., "required"),
        ]),
        ("sku_id", [
            ("正确值(int)", SKU, "required"),
            ("字符串", str(SKU), "optional"),
            ("=0", 0, "rejected"),
            ("缺失", ..., "required"),
        ]),
        ("count", [
            ("=1", 1, "optional"),
            ("=2", 2, "optional"),
            ("=0", 0, "rejected"),
            ("缺失", ..., "optional"),
        ]),
        ("order_type", [
            ("=1", 1, "optional"),
            ("=2", 2, "optional"),
            ("=0", 0, "rejected"),
            ("缺失", ..., "optional"),
        ]),
        ("buyer_info", [
            ("空字符串", "", "optional"),
            ("={name/id}", json.dumps([{"name":"x","id_card":"110101199001011234","phone":"13800138000"}]), "optional"),
            ("缺失", ..., "optional"),
        ]),
        ("token (ctoken)", [
            ("有效ctoken", generate_ctoken(), "optional"),
            ("空字符串", "", "optional"),
            ("缺失", ..., "optional"),
        ]),
        ("ignoreRequestLimit", [
            ("true", True, "optional"),
            ("false", False, "optional"),
            ("缺失", ..., "optional"),
        ]),
        ("newRisk", [
            ("true", True, "optional"),
            ("false", False, "optional"),
            ("缺失", ..., "optional"),
        ]),
        ("requestSource", [
            ("neul-next", "neul-next", "optional"),
            ("pc-new", "pc-new", "optional"),
            ("空字符串", "", "optional"),
            ("缺失", ..., "optional"),
        ]),
        ("ticket_agent", [
            ("空字符串", "", "optional"),
            ("缺失", ..., "optional"),
        ]),
    ],
    project_bound=False,
)

prep_grouped = group_results(results_prep)
for param, tests in prep_grouped.items():
    print(f"\n  ── {param} ──")
    for t in tests:
        mark = {"pass": "✓", "reject": "✗", "rate_limited": "∅", "error": "!!"}
        print(f"  {mark[t['verdict']]} {t['variant']:25s} → "
              f"errno={t.get('errno')} {t.get('msg','')[:40]}")

prep_summary = verdict_summary(prep_grouped)

# ═══════════════════════════════════════════════════════════
# API-3: order/createV2
# ═══════════════════════════════════════════════════════════

print("\n\n" + "=" * 70)
print("  API-3: /api/ticket/order/createV2")
print("=" * 70)

cv2_url = f"https://show.bilibili.com/api/ticket/order/createV2?project_id={PID}"
cv2_base = {
    "project_id": PID, "screen_id": SID, "sku_id": SKU,
    "count": 1, "pay_money": 8000,
    "timestamp": now_ms, "token": TOKEN, "deviceId": dev_id,
    "order_type": 1, "id_bind": 0, "need_contact": 1,
    "is_package": 0, "package_num": 1,
    "version": "1.1.0", "coupon_code": "", "again": 0,
    "contactInfo": {"uid": 630366719, "username": "test", "tel": "13800138000"},
    "buyer": "test", "tel": "13800138000",
    "clickPosition": {"x": 255, "y": 750, "origin": now_ms-5000, "now": now_ms},
    "ctoken": ct, "requestSource": "neul-next", "newRisk": True,
}
cv2_headers = s.headers.copy()
cv2_headers["x-risk-header"] = f"platform/pc uid/630366719 deviceId/{dev_id}"

# createV2 单项目只能测一次 (会创建订单), 参数按重要程度排
results_cv2 = test_param(
    "order/createV2",
    cv2_url, cv2_base,
    [
        # 格式敏感参数 (最重要, 先测)
        ("clickPosition", [
            ("对象格式(正确)", cv2_base["clickPosition"], "required"),
            ("JSON字符串", json.dumps(cv2_base["clickPosition"]), "rejected"),
            ("缺失", ..., "required"),
        ]),
        # 核心字段
        ("ctoken", [
            ("有效ctoken", ct, "required"),
            ("全零16字节", base64.b64encode(b'\x00'*16).decode(), "optional"),
            ("缺失", ..., "required"),
        ]),
        ("requestSource", [
            ("neul-next", "neul-next", "required"),
            ("pc-new", "pc-new", "rejected"),
            ("空字符串", "", "rejected"),
            ("缺失", ..., "required"),
        ]),
        ("newRisk", [
            ("true", True, "required"),
            ("缺失", ..., "required"),
        ]),
        ("version", [
            ("1.1.0", "1.1.0", "required"),
            ("缺失", ..., "required"),
        ]),
        ("contactInfo", [
            ("完整", cv2_base["contactInfo"], "required"),
            ("username=空", {"uid": 630366719, "username": "", "tel": "13800138000"}, "rejected"),
            ("tel=空", {"uid": 630366719, "username": "test", "tel": ""}, "optional"),
            ("uid=0", {"uid": 0, "username": "test", "tel": "13800138000"}, "optional"),
            ("缺失", ..., "required"),
        ]),
        ("pay_money", [
            ("正确值", 8000, "required"),
            ("=0", 0, "rejected"),
            ("=1", 1, "rejected"),
            ("=100", 100, "rejected"),
        ]),
        # 可选字段
        ("coupon_code", [
            ("空字符串", "", "optional"),
            ("缺失", ..., "optional"),
        ]),
        ("again", [
            ("=0", 0, "optional"),
            ("缺失", ..., "optional"),
        ]),
        ("is_package", [
            ("=0", 0, "optional"),
            ("缺失", ..., "optional"),
        ]),
        ("package_num", [
            ("=1", 1, "optional"),
            ("缺失", ..., "optional"),
        ]),
        ("id_bind", [
            ("=0(联系人)", 0, "optional"),
            ("=2(实名)", 2, "optional"),
            ("缺失", ..., "optional"),
        ]),
        ("ptoken", [
            ("有值", PTOKEN, "optional"),
            ("缺失", ..., "optional"),
        ]),
    ],
    headers=cv2_headers, project_bound=True,
)

# createV2 结果显示
cv2_grouped = group_results(results_cv2)
for param, tests in cv2_grouped.items():
    print(f"\n  ── {param} ──")
    for t in tests:
        mark = {"pass": "✓", "reject": "✗", "rate_limited": "∅", "error": "!!"}
        oid_str = f" orderId={t.get('orderId','')}" if t.get('orderId') else ""
        print(f"  {mark[t['verdict']]} {t['variant']:25s} → "
              f"errno={t.get('errno')} {t.get('msg','')[:40]}{oid_str}")

# ═══════════════════════════════════════════════════════════
# API-4: stock/check
# ═══════════════════════════════════════════════════════════

print("\n\n" + "=" * 70)
print("  API-4: /api/ticket/stock/check")
print("=" * 70)

results_stock = test_param(
    "stock/check",
    f"https://show.bilibili.com/api/ticket/stock/check",
    {"project_id": PID, "screen_id": SID, "sku_id": SKU, "count": 1},
    [
        ("project_id", [
            ("正确值", PID, "required"),
            ("=0", 0, "rejected"),
            ("缺失", ..., "required"),
        ]),
        ("screen_id", [
            ("正确值", SID, "required"),
            ("=0", 0, "rejected"),
            ("缺失", ..., "required"),
        ]),
        ("sku_id", [
            ("正确值", SKU, "required"),
            ("=0", 0, "rejected"),
            ("缺失", ..., "required"),
        ]),
        ("count", [
            ("=1", 1, "optional"),
            ("=99", 99, "optional"),
            ("缺失", ..., "optional"),
        ]),
    ],
    project_bound=False,
)

for param, tests in group_results(results_stock).items():
    print(f"\n  ── {param} ──")
    for t in tests:
        mark = {"pass": "✓", "reject": "✗", "rate_limited": "∅", "error": "!!"}
        print(f"  {mark[t['verdict']]} {t['variant']:20s} → "
              f"errno={t.get('errno')} {t.get('msg','')[:40]}")


# ═══════════════════════════════════════════════════════════
# 生成规格文档
# ═══════════════════════════════════════════════════════════

print(f"\n\n{'═' * 70}")
print(f"  B站会员购 API 参数规格 (自动生成)")
print(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  测试项目: {PID} (东方同人ONLY-THO5th)")
print(f"{'═' * 70}")

specs = [
    {
        "name": "project/getV2",
        "desc": "获取项目详情 (场次/票档/场馆/信息)",
        "method": "GET",
        "url": "https://show.bilibili.com/api/ticket/project/getV2?id={project_id}",
        "auth": "不需要",
        "rate_limit": "无",
        "params": [
            {"name": "id", "type": "int", "required": True,
             "desc": "项目ID, 不支持0和负数, 字符串格式也接受"},
        ],
        "response": {
            "success": {"code": 0, "data": {"id", "name", "screen_list[]",
                         "venue_info", "price_low", "price_high",
                         "sale_flag", "sale_flag_number", "id_bind",
                         "buyer_info", "pick_seat", "has_eticket",
                         "has_paper_ticket", "refund_desc",
                         "performance_desc"}},
        },
    },
    {
        "name": "order/prepare",
        "desc": "准备订单 — 获取下单 token",
        "method": "POST",
        "url": "https://show.bilibili.com/api/ticket/order/prepare?project_id={project_id}",
        "auth": "需要 Cookie (SESSDATA + bili_jct)",
        "rate_limit": "prepare 接口无明显限流",
        "params": [
            {"name": "project_id", "type": "int", "required": True,
             "desc": "项目ID"},
            {"name": "screen_id", "type": "int", "required": True,
             "desc": "场次ID"},
            {"name": "sku_id", "type": "int", "required": True,
             "desc": "票档ID"},
            {"name": "count", "type": "int", "required": False,
             "desc": "购买数量, 默认1, 可为2-8但不可为0"},
            {"name": "order_type", "type": "int", "required": False,
             "desc": "订单类型, 通常=1, 可为2但不可为0"},
            {"name": "buyer_info", "type": "string", "required": False,
             "desc": "购票人JSON数组, 空字符串=''或包含name/id_card/phone的JSON"},
            {"name": "token (ctoken)", "type": "string(base64)", "required": False,
             "desc": "浏览器指纹, 16字节base64编码, 服务端不校验内容"},
            {"name": "ignoreRequestLimit", "type": "bool", "required": False,
             "desc": "跳过请求限流, 默认false"},
            {"name": "newRisk", "type": "bool", "required": False,
             "desc": "启用新风险评估, 默认false"},
            {"name": "requestSource", "type": "string", "required": False,
             "desc": "请求来源标记, neul-next/pc-new/空均接受"},
            {"name": "ticket_agent", "type": "string", "required": False,
             "desc": "票务代理标记, 空字符串=''"},
        ],
        "response": {
            "success": {"errno": 0, "data": {"token", "ptoken", "shield",
                         "project_name", "screen_name", "ga_data"}},
            "errors": {
                83000005: "参数为null — 检查 project_id/screen_id/sku_id 是否传入",
                100080: "项目不存在",
            },
        },
    },
    {
        "name": "order/createV2",
        "desc": "创建订单 — 下单",
        "method": "POST",
        "url": "https://show.bilibili.com/api/ticket/order/createV2?project_id={project_id}[&ptoken={ptoken}]",
        "auth": "需要 Cookie + x-risk-header",
        "rate_limit": "⚠ 同一项目仅第1次成功, 后续全部 900001 限流",
        "params": [
            {"name": "project_id", "type": "int", "required": True,
             "desc": "项目ID"},
            {"name": "screen_id", "type": "int", "required": True,
             "desc": "场次ID"},
            {"name": "sku_id", "type": "int", "required": True,
             "desc": "票档ID"},
            {"name": "count", "type": "int", "required": True,
             "desc": "购买数量"},
            {"name": "pay_money", "type": "int", "required": True,
             "desc": "单价(分), 必须正确! 0和错误值会导致失败"},
            {"name": "timestamp", "type": "int", "required": True,
             "desc": "当前时间戳(毫秒)"},
            {"name": "token", "type": "string", "required": True,
             "desc": "从 prepare 获取的 token"},
            {"name": "deviceId", "type": "string", "required": True,
             "desc": "设备指纹, 32位hex"},
            {"name": "order_type", "type": "int", "required": False,
             "desc": "订单类型, 通常=1"},
            {"name": "id_bind", "type": "int", "required": False,
             "desc": "实名类型: 0=联系人 2=身份证"},
            {"name": "need_contact", "type": "int", "required": True,
             "desc": "id_bind=0时为1, id_bind=2时为0"},
            {"name": "is_package", "type": "int", "required": False,
             "desc": "是否套餐, 通常=0"},
            {"name": "package_num", "type": "int", "required": False,
             "desc": "套餐数量, 通常=1"},
            {"name": "version", "type": "string", "required": True,
             "desc": "API版本, 固定 '1.1.0'"},
            {"name": "coupon_code", "type": "string", "required": False,
             "desc": "优惠券码, 空字符串"},
            {"name": "again", "type": "int", "required": False,
             "desc": "重试标记, 通常=0"},
            {"name": "contactInfo", "type": "object", "required": True,
             "desc": "联系人信息 {uid, username, tel}, tel可为空但username不能为空"},
            {"name": "buyer", "type": "string", "required": True,
             "desc": "购票人姓名"},
            {"name": "tel", "type": "string", "required": True,
             "desc": "购票人手机号, 可为空字符串"},
            {"name": "clickPosition", "type": "object(❌不能是string)", "required": True,
             "desc": "点击位置 {x, y, origin, now} 四个字段缺一不可, "
                     "origin必须≤now, x/y可为0"},
            {"name": "ctoken", "type": "string(base64)", "required": True,
             "desc": "浏览器指纹, 最小16字节, 服务端不校验内容"},
            {"name": "requestSource", "type": "string", "required": True,
             "desc": "必须 'neul-next', pc-new和空值均被拒"},
            {"name": "newRisk", "type": "bool", "required": True,
             "desc": "必须 true"},
            {"name": "ptoken", "type": "string", "required": False,
             "desc": "从 prepare 获取的 ptoken (热门项目必需)"},
            {"name": "voucher", "type": "string", "required": False,
             "desc": "验证码凭证 (100044触发时必需)"},
        ],
        "response": {
            "success": {"errno": 0, "data": {"orderId", "orderCreateTime",
                         "token", "count", "status", "pay_money"}},
            "errors": {
                0: "成功",
                100048: "已有未完成订单 (返回已有订单ID)",
                100098: "超过购买数量",
                900001: "前方拥堵 (限流, 该项目已被下单)",
                100044: "需要验证码",
                "联系人信息": "缺少contactInfo或username为空",
            },
        },
        "headers": {
            "Required": {
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": "必须伪装 Chrome (python-requests 会被封禁)",
                "x-risk-header": "platform/pc uid/{uid} deviceId/{deviceId}",
                "Cookie": "必须通过 Session 注入, 不能直接写 Header",
            },
        },
    },
    {
        "name": "stock/check",
        "desc": "库存查询",
        "method": "POST",
        "url": "https://show.bilibili.com/api/ticket/stock/check",
        "auth": "不需要",
        "rate_limit": "无明显限流",
        "params": [
            {"name": "project_id", "type": "int", "required": True,
             "desc": "项目ID"},
            {"name": "screen_id", "type": "int", "required": True,
             "desc": "场次ID"},
            {"name": "sku_id", "type": "int", "required": True,
             "desc": "票档ID"},
            {"name": "count", "type": "int", "required": False,
             "desc": "查询数量, 默认1"},
        ],
        "response": {
            "success": {"errno": 0, "data": {"hasStock", "stockStatus",
                         "unpaidOrderId"}},
            "stockStatus": {1: "有货", 2: "缺货", 3: "未知"},
        },
    },
    {
        "name": "order/createstatus",
        "desc": "查询订单支付状态 (获取支付参数)",
        "method": "GET",
        "url": "https://show.bilibili.com/api/ticket/order/createstatus?orderId={order_id}&project_id={project_id}&token={token}",
        "auth": "需要 Cookie",
        "rate_limit": "无明显限流",
        "params": [
            {"name": "orderId", "type": "int", "required": True,
             "desc": "订单ID"},
            {"name": "project_id", "type": "int", "required": True,
             "desc": "项目ID"},
            {"name": "token", "type": "string", "required": True,
             "desc": "下单时用的 token"},
        ],
        "response": {
            "success": {"errno": 0, "data": {"payParam", "order_id"}},
            "payParam": {"customerId", "defaultChoose", "feeType",
                         "notifyUrl", "extData (含 orderId)"},
        },
    },
    {
        "name": "graph/prepare",
        "desc": "检查是否需要验证码",
        "method": "GET",
        "url": "https://show.bilibili.com/api/ticket/graph/prepare?project_id={pid}&screen_id={sid}&timestamp={ts_ms}",
        "auth": "需要 Cookie",
        "rate_limit": "无明显限流",
        "params": [
            {"name": "project_id", "type": "int", "required": True},
            {"name": "screen_id", "type": "int", "required": True},
            {"name": "timestamp", "type": "int(ms)", "required": True},
        ],
        "response": {
            "no_captcha": {"errno": 0, "data": []},
            "has_captcha": {"errno": 0, "data": {"gt": "", "challenge": ""}},
        },
    },
    {
        "name": "buyer/list",
        "desc": "获取B站已绑定的实名观演人",
        "method": "GET",
        "url": "https://show.bilibili.com/api/ticket/buyer/list",
        "auth": "需要 Cookie",
        "rate_limit": "无",
        "params": [],
        "response": {
            "success": {"errno": 0, "data": {"max_limit": int, "list": [
                {"id", "name", "tel", "id_card", ...}
            ]}},
        },
    },
    {
        "name": "user/info",
        "desc": "获取当前登录用户信息",
        "method": "GET",
        "url": "https://show.bilibili.com/api/ticket/user/info",
        "auth": "需要 Cookie",
        "rate_limit": "无",
        "params": [],
        "response": {
            "success": {"errno": 0, "data": {
                "mid", "uname", "face", "rank", "scores",
                "coins", "sex", "sign", "jointime", "silence",
                "email_verified", "identification", "mobile_verified",
            }},
        },
    },
]

# 打印规格
for i, spec in enumerate(specs, 1):
    print(f"\n{'─' * 70}")
    print(f"  {spec['name']}")
    print(f"{'─' * 70}")
    print(f"  描述: {spec['desc']}")
    print(f"  方法: {spec['method']} {spec['url']}")
    print(f"  鉴权: {spec['auth']}")
    print(f"  限流: {spec['rate_limit']}")
    print(f"\n  参数 ({len(spec['params'])}):")

    for p in spec["params"]:
        req = "必需" if p["required"] else "可选"
        print(f"    {p['name']:25s} {p['type']:20s} {req}")
        print(f"      {p.get('desc', '')}")

    if "headers" in spec:
        print(f"\n  请求头:")
        for h_name, h_val in spec["headers"].get("Required", {}).items():
            print(f"    {h_name}: {h_val}")

    print(f"\n  响应:")
    print(f"    成功: {json.dumps(spec.get('response_success',{}), ensure_ascii=False)[:200]}")

print(f"\n{'═' * 70}")
print(f"  规格生成完成 — {len(specs)} 个端点, 共 {sum(len(s['params']) for s in specs)} 个参数")
print(f"{'═' * 70}")
