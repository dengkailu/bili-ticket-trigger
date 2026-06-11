#!/usr/bin/env python3
"""
API 指纹/反爬参数探测器

核心方法:
  错误码分级 — 区分"格式错"vs"业务拒"vs"限流"
  以 900001(限流) 和 errno=0(成功) 做真假双锚定
  → 先确定基线(肯定通过)和反基线(肯定不通过)
  → 再逐个参数测试是真检验还是假检验

用法:
  python probe.py 1001227 875261 1004195
"""

import json, time, base64, random, sys
from collections import defaultdict

from bili_api import BiliTicketAPI, generate_ctoken

api = BiliTicketAPI()

# ── 错误码分类器: 判断"失败原因" ──

def failure_category(errno, msg=""):
    """
    把失败分类, 关键区分:
      FORMAT_ERROR  = 格式/参数错误 (缺字段、类型错、值不对)
      BIZ_REJECT    = 业务拒绝 (售罄、限购、未开售)
      RATE_LIMIT    = 风控限流 (900001 "前方拥堵")
      AUTH_ERROR    = 鉴权失效
    """
    m = (msg or "").lower()
    if errno in (0, None) and not msg:
        return "SUCCESS"
    if errno == 900001 or "拥堵" in msg:
        return "RATE_LIMIT"
    if errno in (100048,) and "未完成" in msg:
        return "SUCCESS"  # 复用旧订单 = 格式通过了
    if errno in (100098,):
        return "BIZ_REJECT"  # 超过购买数
    if errno in (100080,):
        return "BIZ_REJECT"  # 项目不存在
    if any(k in msg for k in ("联系人信息", "购买人信息", "姓名及手机号")):
        return "BIZ_REJECT"  # 缺联系人 = 不是格式错
    if any(k in msg for k in ("未开始", "未开售", "不可售", "已结束", "售罄", "库存")):
        return "BIZ_REJECT"
    if errno in (83000005,):
        return "FORMAT_ERROR"  # 参数为null
    if any(k in msg for k in ("不能为null", "must not be null")):
        return "FORMAT_ERROR"
    if errno in (2,) and "登录" in msg:
        return "AUTH_ERROR"
    if errno == 100001:
        return "RATE_LIMIT"  # 被拦截但可变相重试
    return f"UNKNOWN(errno={errno})"


def test_param_smart(url, base_payload, param_name,
                     valid_value, invalid_value=...,
                     boundary_values=None,
                     project_bound=True, project_pool=None):
    """
    智能参数测试:
      1. 基线: base_payload → 分类 → 确认 "格式正确"
      2. 移除: base_payload - param_name → 看错误类型
      3. 错值: param_name=invalid_value → 看是否 "格式错误"
      4. 边界: param_name=boundary_values → 看接受范围

    判定逻辑:
      - 移除后 = FORMAT_ERROR → 字段被校验 (必需的服务器端检查)
      - 移除后 = BIZ_REJECT  → 字段缺了导致业务失败 (非格式校验)
      - 移除后 = SUCCESS     → 字段没被校验 (可选)
      - 错值后 = FORMAT_ERROR → 服务端检查值格式
      - 错值后 = SUCCESS     → 服务端不检查值格式
      - 边界通过            → 服务端接受这些值
    """
    results = {"param": param_name, "tests": []}

    def _send(payload):
        try:
            r = api._request("POST", url, json=payload)
            return r
        except Exception as e:
            return {"errno": -999, "msg": str(e)}

    # 辅助: 如果限流, 换项目
    def _send_or_rotate(payload):
        global _current_pid
        for attempt in range(3):
            resp = _send(payload)
            cat = failure_category(resp.get("errno"), resp.get("msg", resp.get("message", "")))
            if cat != "RATE_LIMIT":
                return resp, cat
            # 限流 → 换项目
            if project_pool:
                for pp in list(project_pool):
                    if _switch_project(pp):
                        project_pool.remove(pp)
                        print(f"    → 切换项目 {_current_pid} (限流)")
                        # 更新 base_payload 中的 project 相关字段
                        payload = payload.copy()
                        payload.update({
                            "project_id": _current_pid,
                            "screen_id": _current_sid,
                            "sku_id": _current_sku,
                            "token": _current_token,
                            "pay_money": _current_pay_money,
                        })
                        break
                else:
                    break
            else:
                break
        return resp, cat

    # 测试1: 基线
    baseline, bcat = _send_or_rotate(base_payload)
    results["tests"].append({
        "variant": "baseline", "errno": baseline.get("errno"),
        "msg": baseline.get("msg", baseline.get("message", ""))[:50],
        "category": bcat,
    })
    if bcat not in ("SUCCESS", "BIZ_REJECT"):
        results["verdict"] = f"基线失败({bcat}), 无法测试"
        return results

    # 测试2: 移除参数
    removed = {k: v for k, v in base_payload.items() if k != param_name}
    resp, cat = _send_or_rotate(removed)
    results["tests"].append({
        "variant": "removed", "errno": resp.get("errno"),
        "msg": resp.get("msg", resp.get("message", ""))[:50],
        "category": cat,
    })

    if cat == "FORMAT_ERROR":
        results["validated"] = True
        results["required"] = True
        results["desc"] = "服务端校验 — 缺失导致格式错误"
    elif cat == "BIZ_REJECT":
        results["validated"] = True
        results["required"] = False
        results["desc"] = "服务端校验 — 缺失导致业务拒绝(非格式错)"
    elif cat == "SUCCESS":
        results["validated"] = False
        results["required"] = False
        results["desc"] = "不校验 — 缺失仍成功"
    elif cat == "RATE_LIMIT":
        results["validated"] = "unknown"
        results["required"] = "unknown"
        results["desc"] = "无法判断 — 被限流"
    else:
        results["validated"] = "maybe"
        results["required"] = "maybe"
        results["desc"] = f"无法判断 — {cat}"

    # 测试3: 错误值 (如果提供)
    if invalid_value is not ...:
        wrong = {**base_payload, param_name: invalid_value}
        resp, cat = _send_or_rotate(wrong)
        results["tests"].append({
            "variant": f"wrong={invalid_value}", "errno": resp.get("errno"),
            "msg": resp.get("msg", resp.get("message", ""))[:50],
            "category": cat,
        })
        if cat == "FORMAT_ERROR":
            results["type_checked"] = True
        elif cat == "SUCCESS":
            results["type_checked"] = False

    # 测试4: 边界值
    if boundary_values:
        for label, val in boundary_values:
            variant = {**base_payload, param_name: val}
            resp, cat = _send_or_rotate(variant)
            passed = cat in ("SUCCESS", "BIZ_REJECT")
            results["tests"].append({
                "variant": f"boundary={label}", "errno": resp.get("errno"),
                "msg": resp.get("msg", resp.get("message", ""))[:50],
                "category": cat,
                "passed": passed,
            })

    return results


# ── 项目池管理 ──

_current_pid = None
_current_sid = None
_current_sku = None
_current_token = None
_current_pay_money = None

def _switch_project(pid):
    global _current_pid, _current_sid, _current_sku, _current_token, _current_pay_money
    status, avail = api.check_ticket_available(pid)
    if not avail:
        return False
    t = avail[0]
    _current_pid = pid
    _current_sid = t["screen_id"]
    _current_sku = t["sku_id"]
    _current_pay_money = int(t["price"])
    prep = api.prepare_order(_current_pid, _current_sid, _current_sku, 1)
    _current_token = (prep.get("data", {}) or {}).get("token", "")
    return True

def discover_pool(count=15):
    pids = []
    try:
        r = api.session.get(
            "https://show.bilibili.com/api/ticket/project/listV2"
            "?page=1&pagesize=30&platform=web&area=-1&p_type=0", timeout=10)
        for proj in (r.json().get("data", {}) or {}).get("result", []):
            pid = proj.get("id")
            if not pid or pid == _current_pid:
                continue
            detail = api.get_project_detail(pid)
            screens = (detail.get("data", {}) or {}).get("screen_list", [])
            has = False
            for sc in screens:
                for tk in sc.get("ticket_list", []):
                    if tk.get("sale_flag_number") == 2 and tk.get("clickable"):
                        has = True
                        break
            if has and (detail.get("data", {}) or {}).get("sale_flag_number") == 2:
                pids.append(pid)
                if len(pids) >= count:
                    break
    except Exception as e:
        print(f"  发现项目失败: {e}")
    return pids


# ── 主程序 ──

if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1001227
    sku = int(sys.argv[2]) if len(sys.argv) > 2 else 875261
    sid = int(sys.argv[3]) if len(sys.argv) > 3 else 1004195

    _switch_project(pid)
    pool = discover_pool(20)
    print(f"项目池: {len(pool)} 个 (主: {pid}, {_current_pay_money/100:.0f}元)")

    # ── prepare 接口指纹探测 ──
    print(f"\n{'═' * 60}")
    print(f"  prepare 接口 — 指纹参数探测")
    print(f"{'═' * 60}")

    base_prep = {
        "project_id": _current_pid, "screen_id": _current_sid,
        "sku_id": _current_sku, "order_type": 1, "count": 1,
        "buyer_info": "",
    }
    prep_url = f"https://show.bilibili.com/api/ticket/order/prepare?project_id={_current_pid}"

    prep_params = {
        "token (ctoken)": ("可选指纹", generate_ctoken(), ""),
        "requestSource": ("请求源标记", "neul-next", "invalid_value"),
        "newRisk": ("新风险评估", True, "not_bool"),
        "ignoreRequestLimit": ("跳限流", True, "not_bool"),
        "ticket_agent": ("票务代理", "", ...),
    }

    for param_name, (desc, valid, invalid) in prep_params.items():
        r = test_param_smart(prep_url, {**base_prep, param_name.split()[0]: valid},
                              param_name.split()[0],
                              valid_value=valid,
                              invalid_value=invalid if invalid is not ... else ...,
                              project_bound=False)
        verdict = r.get("desc", "?")
        req = "✓必需" if r.get("required") else "✗不校验" if r.get("validated") == False else "?"
        bounded = "✓校验值" if r.get("type_checked") else "✗不校值" if r.get("type_checked") == False else "?"
        print(f"  {req:>8s} {bounded:>8s} {param_name:22s} — {verdict}")

    # ── createV2 接口指纹探测 (需要项目轮换) ──
    print(f"\n{'═' * 60}")
    print(f"  createV2 接口 — 指纹参数探测")
    print(f"  (每个参数消耗1个干净项目, 池: {len(pool)})")
    print(f"{'═' * 60}")

    cv2_url_base = f"https://show.bilibili.com/api/ticket/order/createV2?project_id={_current_pid}"
    now_ms = int(time.time() * 1000)

    def _make_cv2_base():
        return {
            "project_id": _current_pid, "screen_id": _current_sid,
            "sku_id": _current_sku, "count": 1,
            "pay_money": _current_pay_money,
            "timestamp": int(time.time()*1000),
            "token": _current_token,
            "deviceId": api._extract_device_id(),
            "order_type": 1, "id_bind": 0, "need_contact": 1,
            "is_package": 0, "package_num": 1,
            "version": "1.1.0", "coupon_code": "", "again": 0,
            "contactInfo": {"uid": api.config.get("auth",{}).get("uid",0),
                            "username": "test", "tel": "13800138000"},
            "buyer": "test", "tel": "13800138000",
            "clickPosition": {"x": 255, "y": 750,
                               "origin": now_ms-5000, "now": now_ms},
            "ctoken": generate_ctoken(),
            "requestSource": "neul-next", "newRisk": True,
        }

    cv2_params = {
        "clickPosition": ("点击位置", _make_cv2_base()["clickPosition"],
                           json.dumps(_make_cv2_base()["clickPosition"])),
        "ctoken": ("浏览器指纹", generate_ctoken(), base64.b64encode(b'x'*8).decode()),
        "requestSource": ("请求源", "neul-next", "unknown_source"),
        "newRisk": ("新风险标记", True, "no"),
        "version": ("API版本", "1.1.0", "0.0.1"),
        "contactInfo": ("联系人信息", _make_cv2_base()["contactInfo"], {}),
    }

    for param_name, (desc, valid, invalid) in cv2_params.items():
        base = _make_cv2_base()
        r = test_param_smart(cv2_url_base, base, param_name,
                              valid_value=valid,
                              invalid_value=invalid,
                              project_bound=True, project_pool=pool)
        verdict = r.get("desc", "?")
        req = "✓必需" if r.get("required") else "✗不校验" if r.get("validated") == False else "?"
        bounded = "✓校验值" if r.get("type_checked") else "✗不校值" if r.get("type_checked") == False else "?"
        print(f"  {req:>8s} {bounded:>8s} {param_name:22s} — {verdict}")

    print(f"\n  项目池剩余: {len(pool)}")
