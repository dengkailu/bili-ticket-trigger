#!/usr/bin/env python3
"""
未知指纹参数发现器

场景: B站对某个项目做了额外指纹, 但你不知道参数名/类型/值
方法:
  1. 从项目详情 API 找线索 (hotProject/risk_level/ga_data 等)
  2. 全字段注入 — 把所有可能的指纹参数都带上
  3. 差分缩减 — 逐个摘除字段, 看哪个摘掉后失败
  4. 跨项目对比 — 正常项目 vs 特殊项目, diff 出差异

用法:
  python discover.py 1001227                    # 默认项目
  python discover.py 1001227 --compare 1001405  # 对比两个项目
"""

import json, time, base64, random, sys
from collections import defaultdict

from bili_api import BiliTicketAPI, generate_ctoken

api = BiliTicketAPI()

# ── 全字段注入: 所有已知的指纹/反爬字段 ──

KNOWN_FINGERPRINT_FIELDS = {
    # 浏览器指纹
    "ctoken": lambda: generate_ctoken(),
    "deviceId": lambda: api._extract_device_id(),
    "deviceFingerprint": lambda: api._extract_device_id(),
    "buvid_fp": lambda: hex(random.randint(0, 2**48))[2:],
    "buvid3": lambda: f"{random.randint(10**9, 10**10-1)}",
    "buvid4": lambda: f"{random.randint(10**9, 10**10-1)}",
    "fp": lambda: hashlib.md5(str(time.time()).encode()).hexdigest(),
    "canvasFp": lambda: hashlib.md5(str(random.random()).encode()).hexdigest(),
    "webglFp": lambda: hashlib.md5(str(random.random()).encode()).hexdigest(),

    # 点击/行为
    "clickPosition": lambda: {"x": random.randint(200,400),
        "y": random.randint(750,800),
        "origin": int(time.time()*1000)-random.randint(2000,8000),
        "now": int(time.time()*1000)},
    "timestamp": lambda: int(time.time()*1000),
    "clickTime": lambda: int(time.time()*1000),
    "loadTime": lambda: int(time.time()*1000) - random.randint(500, 3000),
    "dwellTime": lambda: random.randint(3000, 15000),

    # 请求标记
    "requestSource": lambda: "neul-next",
    "newRisk": lambda: True,
    "ignoreRequestLimit": lambda: True,
    "token": lambda: generate_ctoken(),
    "ptoken": lambda: "",
    "version": lambda: "1.1.0",
    "v": lambda: random.randint(1, 100),
    "_": lambda: int(time.time()*1000),

    # 风控/GAIA
    "riskParams": lambda: "",
    "grisk_id": lambda: f"risk_{int(time.time()*1000)}",
    "voucher": lambda: "",
    "captchaToken": lambda: "",
    "geetest_challenge": lambda: "",
    "geetest_validate": lambda: "",
    "geetest_seccode": lambda: "",

    # 设备/环境
    "screen_info": lambda: f"{random.choice([362,375,414])}*{random.choice([795,812,896])}*24",
    "platform": lambda: "web",
    "os": lambda: "mac",
    "build": lambda: str(random.randint(100000, 999999)),
    "feSign": lambda: hashlib.md5(str(time.time()).encode()).hexdigest(),

    # 账号/会话
    "uid": lambda: api.config.get("auth", {}).get("uid", 0),
    "mid": lambda: api.config.get("auth", {}).get("uid", 0),
    "access_key": lambda: "",
    "appkey": lambda: "1d8b6e7d45233436",
    "ts": lambda: int(time.time()),

    # WBI 签名
    "w_rid": lambda: hashlib.md5(str(time.time()).encode()).hexdigest(),
    "wts": lambda: int(time.time()),
}

import hashlib as _hashlib


def inject_all_fingerprints(base_payload, exclude=None):
    """将所有已知指纹字段注入 payload"""
    exclude = exclude or set()
    result = base_payload.copy()
    for field, generator in KNOWN_FINGERPRINT_FIELDS.items():
        if field not in result and field not in exclude:
            try:
                result[field] = generator()
            except Exception:
                pass
    return result


def binary_search_field(payload, candidate_fields, url, test_fn):
    """
    二分查找: 从候选字段中找出真正影响结果的那个

    1. 注入所有候选字段 = full_payload (基线, 确保成功)
    2. 每次移除一半候选字段 → 看是否仍成功
    3. 递归缩小范围, 直到定位到具体字段
    """
    if not candidate_fields:
        return []

    # 测试移除这组字段后是否失败
    reduced = {k: v for k, v in payload.items() if k not in set(candidate_fields)}
    resp = test_fn(reduced)

    if resp.get("errno") == 0 or resp.get("errno") == 100048:
        # 移除后仍成功 → 这些字段都不是必需的
        return []

    if len(candidate_fields) == 1:
        # 只剩一个字段, 移除它就失败 → 这个字段是必需的
        return list(candidate_fields)

    # 二分: 前后两半各自测试
    mid = len(candidate_fields) // 2
    left = binary_search_field(reduced, candidate_fields[:mid], url, test_fn)
    right = binary_search_field(reduced, candidate_fields[mid:], url, test_fn)

    return left + right


def analyze_project_detail(pid):
    """从项目详情找指纹线索"""
    detail = api.get_project_detail(pid)
    data = detail.get("data", {})
    clues = {}

    # 检查项目是否标记为"热门" (可能有额外校验)
    if data.get("hotProject"):
        clues["hotProject"] = True

    # 检查 GAIA 风险评估
    ga = data.get("ga_data") or {}
    if ga:
        clues["ga_data"] = {
            "risk_level": ga.get("risk_level"),
            "grisk_id": ga.get("grisk_id", "")[:20],
            "riskResult": ga.get("riskResult"),
        }

    # 检查预售/实名要求
    clues["id_bind"] = data.get("id_bind", 0)
    clues["buyer_info"] = data.get("buyer_info", "")

    # 检查是否允许 PC 购买
    clues["allowPc"] = data.get("allowPc", 1)

    # 检查是否有预售限制
    restrict = data.get("restrictBuyerInfo")
    if restrict:
        clues["restrictBuyerInfo"] = True

    # 检查 preFill 信息
    prefill = data.get("preFillSupport")
    if prefill:
        clues["preFillSupport"] = True

    # 检查票种是否特殊
    clues["ticket_type"] = data.get("ticket_type", data.get("type", "?"))

    return clues


def compare_projects(pid_a, pid_b):
    """对比两个项目, 找出差异字段"""
    da = api.get_project_detail(pid_a).get("data", {})
    db = api.get_project_detail(pid_b).get("data", {})

    def flat_keys(d, prefix=""):
        keys = set()
        if isinstance(d, dict):
            for k, v in d.items():
                full = f"{prefix}.{k}" if prefix else k
                keys.add(full)
                if isinstance(v, (dict, list)):
                    keys |= flat_keys(v, full)
        return keys

    keys_a = flat_keys(da)
    keys_b = flat_keys(db)

    only_a = keys_a - keys_b
    only_b = keys_b - keys_a

    # 找出值和类型都不同的公共字段
    different = {}
    for k in keys_a & keys_b:
        parts = k.split(".")
        va = da
        vb = db
        for p in parts:
            va = va.get(p, None) if isinstance(va, dict) else None
            vb = vb.get(p, None) if isinstance(vb, dict) else None
        if type(va) != type(vb) or va != vb:
            different[k] = {"a": str(va)[:60], "b": str(vb)[:60]}

    return {
        "only_in_a": sorted(only_a),
        "only_in_b": sorted(only_b),
        "different_values": {k: v for k, v in different.items()
                              if "cookie" not in k.lower()
                              and "time" not in k.lower()
                              and "id" not in k.lower()},
    }


def discover(pid, compare_pid=None):
    """主流程: 发现未知指纹参数"""

    print(f"\n{'═' * 60}")
    print(f"  未知指纹参数发现: {pid}")
    print(f"{'═' * 60}")

    # 阶段1: 项目详情分析
    print(f"\n── 阶段1: 项目详情线索 ──")
    clues = analyze_project_detail(pid)
    for k, v in clues.items():
        if v or v == 0:
            print(f"  {k}: {v}")
    if not any(clues.values()):
        print(f"  (无特殊标记 — 可能是普通项目)")

    # 阶段2: 跨项目对比
    if compare_pid:
        print(f"\n── 阶段2: 项目对比 ({pid} vs {compare_pid}) ──")
        diff = compare_projects(pid, compare_pid)
        if diff["only_in_a"]:
            print(f"  仅在 {pid} 的字段: {diff['only_in_a'][:10]}")
        if diff["only_in_b"]:
            print(f"  仅在 {compare_pid} 的字段: {diff['only_in_b'][:10]}")
        if diff["different_values"]:
            print(f"  值不同的字段:")
            for k, v in list(diff["different_values"].items())[:8]:
                print(f"    {k}: {v['a'][:30]} ≠ {v['b'][:30]}")
        if not any(diff.values() or {}):
            print(f"  (两个项目结构一致)")

    # 阶段3: 全字段注入 + 差分
    print(f"\n── 阶段3: 全字段注入 → 逐个摘除 ──")
    status, avail = api.check_ticket_available(pid)
    if not avail:
        print(f"  无可购票档, 跳过 createV2 测试")
        return

    t = avail[0]
    sid, sku = t["screen_id"], t["sku_id"]
    pay = int(t["price"])

    prep = api.prepare_order(pid, sid, sku, 1)
    token = (prep.get("data", {}) or {}).get("token", "")
    if not token:
        print(f"  prepare 失败")
        return

    # 构建最小基线
    min_base = {
        "project_id": pid, "screen_id": sid, "sku_id": sku,
        "count": 1, "pay_money": pay,
        "timestamp": int(time.time()*1000),
        "token": token,
        "deviceId": api._extract_device_id(),
        "order_type": 1, "id_bind": 0, "need_contact": 1,
        "is_package": 0, "package_num": 1,
        "version": "1.1.0", "coupon_code": "", "again": 0,
        "contactInfo": {"uid": api.config.get("auth",{}).get("uid",0),
                        "username": "test", "tel": "13800138000"},
        "buyer": "test", "tel": "13800138000",
        "clickPosition": {"x": 255, "y": 750,
                           "origin": int(time.time()*1000)-5000,
                           "now": int(time.time()*1000)},
        "ctoken": generate_ctoken(),
        "requestSource": "neul-next", "newRisk": True,
    }

    # 注入所有已知指纹
    full = inject_all_fingerprints(min_base, exclude=set(min_base.keys()))
    print(f"  注入 {len(full) - len(min_base)} 个额外指纹字段")

    url = f"/api/ticket/order/createV2?project_id={pid}"
    headers = api.session.headers.copy()

    def _test(payload):
        return api._request("POST", url, headers=headers, json=payload)

    # 测试基线
    r = _test(min_base)
    base_ok = r.get("errno") in (0, 100048) or bool((r.get("data",{}) or {}).get("orderId"))
    print(f"  最小基线: {'✓' if base_ok else '✗'} errno={r.get('errno')}"
          f" {r.get('msg',r.get('message',''))[:50]}")

    # 测试全字段
    r_full = _test(full)
    full_ok = r_full.get("errno") in (0, 100048) or bool((r_full.get("data",{}) or {}).get("orderId"))
    print(f"  全字段注入: {'✓' if full_ok else '✗'} errno={r_full.get('errno')}"
          f" {r_full.get('msg',r_full.get('message',''))[:50]}")

    if not base_ok and full_ok:
        print(f"\n  ⚠️ 最小基线失败但全字段成功!")
        print(f"  → 该项目有额外指纹要求")
        # 二分查找哪个字段是关键
        extra = {k: full[k] for k in full if k not in min_base}
        keys = list(extra.keys())
        print(f"  → 候选字段: {len(keys)} 个")
        print(f"  → 字段列表: {sorted(keys)[:15]}...")

        critical = binary_search_field(full, keys, url, _test)
        if critical:
            print(f"\n  ★ 发现关键指纹字段: {critical}")
        else:
            print(f"  (需要多项目轮换进一步缩小范围)")

    elif base_ok:
        print(f"  最小基线通过 — 该项目无额外指纹")
    else:
        print(f"  两者都失败 — 可能是项目本身的限制")

    print(f"\n{'═' * 60}")
    print(f"  发现完成")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1001227
    compare = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--compare" else None
    discover(pid, compare)
