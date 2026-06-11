#!/usr/bin/env python3
"""
B站会员购 API 逆向工程工具

用法:
  python reverse.py 1001227          对指定项目全量逆向
  python reverse.py 1001227 --quick  快速模式 (仅关键字段)
  python reverse.py 1001227 --deep   深度模式 (含穷举)

方法:
  1. 差分测试 (Differential Testing)
     基线 payload → 逐一变异字段 → 对比响应差异

  2. 端点扫描 (Endpoint Scanning)
     尝试常见路径模式 → 探测隐藏接口

  3. 类型推断 (Type Inference)
     string vs int → 缺字段 vs 空值 → 观察错误信息变化

  4. 错误码穷举 (Error Enumeration)
     故意触发各种错误 → 记录 code/message 映射
"""

import json
import time
import base64
import random
import argparse
import sys
import re
from collections import defaultdict
from typing import Any, Optional

import requests

from bili_api import BiliTicketAPI, generate_ctoken


class APIReverser:
    """B站会员购 API 逆向器"""

    def __init__(self, project_id: int):
        self.api = BiliTicketAPI()
        self.pid = project_id
        self.session = self.api.session
        self.log: list[dict] = []
        self.errors: dict[int, str] = {}

        status, avail = self.api.check_ticket_available(project_id)
        self.project = status
        self.available = avail

        if avail:
            t = avail[0]
            self.sid = t["screen_id"]
            self.sku = t["sku_id"]
            self.pay_money = int(t["price"])
            self.ticket_desc = t["desc"]
        else:
            self.sid = 0
            self.sku = 0
            self.pay_money = 1000
            self.ticket_desc = "(无可购票档)"

        # 备用项目列表 (用于限流时轮换)
        self.fallback_pids = []

        self.token: str = ""
        self.ptoken: str = ""
        self.device_id: str = self.api._extract_device_id()
        self.uid: int = self.api.config.get("auth", {}).get("uid", 0)

        self._record("project", {
            "name": self.project.get("name"),
            "status": self.project.get("sale_flag"),
            "code": self.project.get("sale_flag_number"),
            "id_bind": self.project.get("id_bind"),
            "buyer_info": self.project.get("buyer_info"),
            "avail_count": len(self.available),
            "sid": self.sid,
            "sku": self.sku,
            "desc": self.ticket_desc,
            "pay_money": self.pay_money,
        })

    def _record(self, category: str, data: dict):
        entry = {"ts": time.time(), "category": category, **data}
        self.log.append(entry)
        if "errno" in data:
            errno = data["errno"]
            msg = data.get("msg", data.get("message", ""))
            if errno not in self.errors and errno is not None:
                self.errors[errno] = msg

    # ── 核心: 差分测试引擎 ──

    def diff_test(self, label: str, url: str, base: dict,
                   mutations: list[tuple[str, dict, str]],
                   first_only: bool = False) -> list[dict]:
        """
        差分测试引擎

        Args:
          label: 测试名称
          url: API URL
          base: 基线 payload
          mutations: [(变异名, 字段覆盖, 预期行为描述), ...]
          first_only: True = 只测第一个通过就停 (用于限流场景)

        Returns:
          [{变异名, errno, msg, orderId, passed, ...}, ...]

        策略:
          - 基线先跑, 确认正常
          - 按重要性排序 mutations
          - 每次变异后对比基线的 errno/msg
          - 错误变化 = 该字段影响行为
        """
        print(f"\n{'─' * 60}")
        print(f"  差分测试: {label} ({len(mutations)} 变体)")
        print(f"{'─' * 60}")

        results = []
        baseline_ok = False

        # 先跑基线
        baseline = self._send(url, base)
        baseline_errno = baseline.get("errno")
        baseline_ok = baseline_errno in (0, 100048) or bool(
            (baseline.get("data", {}) or {}).get("orderId"))

        print(f"  [基线] errno={baseline_errno} "
              f"{'✓' if baseline_ok else '✗'}")
        results.append({"variant": "基线", "errno": baseline_errno,
                         "passed": baseline_ok, "response": baseline})

        for i, (name, overrides, expected) in enumerate(mutations):
            payload = {**base, **overrides}
            resp = self._send(url, payload)
            errno = resp.get("errno")
            msg = resp.get("msg", resp.get("message", ""))

            # 判断通过条件
            ok = errno in (0, 100048) or bool(
                (resp.get("data", {}) or {}).get("orderId"))

            # 变化分析
            changed = errno != baseline_errno
            is_rate_limit = "拥堵" in (msg or "") or errno == 900001
            is_biz_error = errno and errno not in (0, 100048, 900001) and not ok

            if changed and not is_rate_limit:
                signal = "← 参数影响: "
                if ok:
                    signal += f"通过 (errno {baseline_errno}→{errno})"
                else:
                    signal += f"新错误: {msg[:50]}"
            elif is_rate_limit:
                signal = "(限流, 无法判断)"
            elif changed:
                signal = f"(errno {baseline_errno}→{errno})"
            else:
                signal = ""

            print(f"  [{i+1}/{len(mutations)}] "
                  f"{'✓' if ok else ('∅' if is_rate_limit else '✗')} "
                  f"{name:30s} → errno={errno} {signal}")

            results.append({
                "variant": name, "errno": errno, "passed": ok,
                "changed": changed, "rate_limited": is_rate_limit,
                "biz_error": is_biz_error, "signal": signal,
                "response": resp,
            })

            self._record(f"diff:{label}", {
                "variant": name, "errno": errno, "msg": msg or "",
                "passed": ok, "rate_limited": is_rate_limit,
            })

            if first_only and baseline_ok:
                print(f"  (first_only 模式, 基线已通过, 后续被限流无法判断)")
                break
            if is_rate_limit and i > 0:
                remaining = len(mutations) - i - 1
                print(f"  ⚠ 检测到限流 → 无法测试剩余 {remaining} 变体")
                print(f"  提示: 用 --rotate 参数可轮流在不同项目上测试每个变体")
                break

        return results

    def _send(self, url: str, payload: dict) -> dict:
        try:
            r = self.session.post(url, json=payload, timeout=10)
            return r.json()
        except Exception as e:
            return {"errno": -999, "msg": str(e)}

    def discover_fallback_projects(self, count=5):
        """发现可选项目列表 (createV2 限流时轮换)"""
        pids = []
        try:
            r = self.session.get(
                "https://show.bilibili.com/api/ticket/project/listV2"
                "?page=1&pagesize=20&platform=web&area=-1&p_type=0",
                timeout=10)
            data = r.json()
            results = (data.get("data", {}) or {}).get("result", [])
            for proj in results:
                pid = proj.get("id")
                if pid and pid != self.pid:
                    detail = self.api.get_project_detail(pid)
                    pd = detail.get("data", {})
                    screens = pd.get("screen_list", [])
                    has_avail = False
                    for sc in screens:
                        for tk in sc.get("ticket_list", []):
                            if tk.get("sale_flag_number") == 2 and tk.get("clickable"):
                                has_avail = True
                                break
                    if has_avail and pd.get("sale_flag_number") == 2:
                        pids.append(pid)
                        if len(pids) >= count:
                            break
        except Exception as e:
            print(f"  (发现备用项目失败: {e})")
        return pids

    def _switch_project(self, pid):
        """切换到新的测试项目 (创建新 token)"""
        status, avail = self.api.check_ticket_available(pid)
        if not avail:
            return False
        t = avail[0]
        self.pid = pid
        self.sid = t["screen_id"]
        self.sku = t["sku_id"]
        self.pay_money = int(t["price"])
        self.project = status
        self.available = avail
        prep = self.api.prepare_order(self.pid, self.sid, self.sku, 1)
        self.token = (prep.get("data", {}) or {}).get("token", "")
        self.ptoken = (prep.get("data", {}) or {}).get("ptoken", "") or ""
        return True

    # ── 端点扫描 ──

    def scan_endpoints(self):
        """扫描潜在 API 端点"""
        print(f"\n{'═' * 60}")
        print(f"  端点扫描")
        print(f"{'═' * 60}")

        patterns = [
            ("GET", "/api/ticket/stock/check"),
            ("POST", "/api/ticket/stock/check"),
            ("GET", "/api/ticket/order/createstatus"),
            ("GET", "/api/ticket/order/orderInfo"),
            ("GET", "/api/ticket/order/list"),
            ("GET", "/api/ticket/buyer/list"),
            ("GET", "/api/ticket/graph/prepare"),
            ("POST", "/api/ticket/graph/check"),
            ("GET", "/api/ticket/coupon/list"),
            ("GET", "/api/ticket/project/listV2"),
            ("GET", "/api/ticket/project/getV2"),
            ("GET", "/api/ticket/user/info"),
            ("GET", "/api/ticket/address/list"),
        ]

        found = []
        for method, path in patterns:
            url = f"https://show.bilibili.com{path}"
            params = f"?project_id={self.pid}"
            if "listV2" in path:
                params = f"?page=1&pagesize=1&platform=web&area=-1&p_type=0"
            elif "getV2" in path:
                params = f"?id={self.pid}"
            elif "graph/prepare" in path:
                params = f"?project_id={self.pid}&screen_id={self.sid}&timestamp={int(time.time()*1000)}"
            elif "createstatus" in path or "orderInfo" in path:
                params = f"?orderId=1&project_id={self.pid}"

            full_url = url + params

            try:
                if method == "GET":
                    r = self.session.get(full_url, timeout=8)
                else:
                    r = self.session.post(url, json={
                        "project_id": self.pid,
                        "screen_id": self.sid, "sku_id": self.sku,
                        "count": 1,
                    }, timeout=8)

                ct = r.headers.get("content-type", "")
                if "json" in ct:
                    data = r.json()
                    code = data.get("code", data.get("errno"))
                    has_data = bool(data.get("data"))
                    errno = data.get("errno")

                    status = "✓" if code == 0 or errno == 0 else \
                             "∅" if code == 404 else f"✗({code})"
                    detail = ""
                    if has_data:
                        d = data.get("data", {})
                        if isinstance(d, dict):
                            keys = list(d.keys())[:5]
                            detail = f"keys={keys}"
                        elif isinstance(d, list):
                            detail = f"list[{len(d)}]"

                    print(f"  {status} {method:4s} {path:35s} → "
                          f"has_data={has_data} {detail}")

                    if code == 0 or errno == 0:
                        found.append(path)
                else:
                    print(f"  -- {method:4s} {path:35s} → "
                          f"status={r.status_code} (HTML)")

            except Exception as e:
                print(f"  !! {method:4s} {path:35s} → {str(e)[:50]}")

        print(f"\n  发现 {len(found)} 个可用端点")
        return found

    # ── 完整的准备+下单逆向 ──

    def reverse_prepare(self):
        """逆向 prepare 接口"""
        url = f"https://show.bilibili.com/api/ticket/order/prepare?project_id={self.pid}"

        # 基线: 最小必需
        base = {
            "project_id": self.pid,
            "screen_id": self.sid,
            "sku_id": self.sku,
            "order_type": 1,
            "count": 1,
            "buyer_info": "",
        }

        mutations = [
            # 核心字段类型测试
            ("project_id=str", {"project_id": str(self.pid)}, "string ID也可用"),
            ("screen_id=str", {"screen_id": str(self.sid)}, "string也可用"),
            # 可选字段
            ("+ctoken", {"token": generate_ctoken()}, "加指纹"),
            ("+ignoreRequestLimit", {"ignoreRequestLimit": True}, "加跳过限流"),
            ("+newRisk", {"newRisk": True}, "加风险标记"),
            ("+requestSource", {"requestSource": "neul-next"}, "加请求源"),
            # 数量/订单类型
            ("count=2", {"count": 2}, "多张"),
            ("count=0", {"count": 0}, "零张"),
            ("order_type=2", {"order_type": 2}, "不同订单类型"),
        ]

        return self.diff_test("prepare", url, base, mutations)

    def reverse_create_v2(self, rotate=True):
        """逆向 createV2 接口

        单项目限流对策: 如果 rotate=True, 自动发现备用项目轮流测试
        """
        if not self.token:
            prep = self.api.prepare_order(self.pid, self.sid, self.sku, 1)
            self.token = (prep.get("data", {}) or {}).get("token", "")
            self.ptoken = (prep.get("data", {}) or {}).get("ptoken", "") or ""

        if not self.token:
            print("  无法获取 token, 跳过 createV2 逆向")
            return []

        url = f"https://show.bilibili.com/api/ticket/order/createV2?project_id={self.pid}"
        now_ms = int(time.time() * 1000)
        id_bind = self.project.get("id_bind", 0)

        def _make_base():
            return {
                "project_id": self.pid, "screen_id": self.sid,
                "sku_id": self.sku, "count": 1,
                "pay_money": self.pay_money, "timestamp": int(time.time()*1000),
                "token": self.token, "deviceId": self.device_id,
                "order_type": 1, "id_bind": id_bind,
                "need_contact": 1 if id_bind == 0 else 0,
                "is_package": 0, "package_num": 1,
                "version": "1.1.0", "coupon_code": "", "again": 0,
                "contactInfo": {"uid": self.uid, "username": "_rev_",
                                "tel": "13800138000"},
                "buyer": "_rev_", "tel": "13800138000",
                "clickPosition": {"x": random.randint(200,400),
                    "y": random.randint(750,800),
                    "origin": int(time.time()*1000)-random.randint(2000,8000),
                    "now": int(time.time()*1000)},
                "ctoken": generate_ctoken(),
                "requestSource": "neul-next", "newRisk": True,
            }

        mutations = [
            ("clickPosition=str", {"clickPosition": json.dumps({"x":255,"y":750,"origin":now_ms-5000,"now":now_ms})}),
            ("clickPosition-缺失", {"clickPosition": None}),
            ("-ctoken", {"ctoken": None}),
            ("ctoken=全零", {"ctoken": base64.b64encode(b'\x00'*16).decode()}),
            ("requestSource=pc-new", {"requestSource": "pc-new"}),
            ("-requestSource", {"requestSource": None}),
            ("-newRisk", {"newRisk": None}),
            ("-version", {"version": None}),
            ("-contactInfo", {"contactInfo": None}),
            ("tel=空", {"tel": "", "contactInfo": {"uid": self.uid, "username": "_rev_", "tel": ""}}),
            ("pay_money=1", {"pay_money": 1}),
            ("pay_money=0", {"pay_money": 0}),
            ("+ptoken", {"ptoken": self.ptoken or "fake"}),
            ("id_bind=2", {"id_bind": 2, "need_contact": 0,
                "buyer_info": json.dumps([{"name":"x","id_card":"110101199001011234","phone":"13800138000"}])}),
        ]

        base = _make_base()

        # 先跑基线
        resp = self._send(url, base)
        baseline_errno = resp.get("errno")
        baseline_ok = baseline_errno in (0, 100048) or bool((resp.get("data",{}) or {}).get("orderId"))
        print(f"\n  [基线] errno={baseline_errno} {'✓' if baseline_ok else '✗'}"
              f" pid={self.pid}")

        if not baseline_ok:
            print("  基线失败, 无法继续差分测试")
            return []

        # 如果需要轮换, 先发现备用项目
        fallback_pids = []
        if rotate:
            print(f"  正在发现备用项目 (用于限流轮换)...")
            fallback_pids = self.discover_fallback_projects(count=len(mutations))
            print(f"  找到 {len(fallback_pids)} 个备用项目: {fallback_pids}")

        tested = 1
        for i, (name, overrides) in enumerate(mutations):
            payload = {**base, **overrides}
            # remove None values
            payload = {k: v for k, v in payload.items() if v is not None}
            resp = self._send(url, payload)
            errno = resp.get("errno")
            msg = resp.get("msg", resp.get("message", ""))
            oid = (resp.get("data", {}) or {}).get("orderId", "")
            is_rl = "拥堵" in (msg or "") or errno == 900001
            ok = errno in (0, 100048) or bool(oid)

            mark = "✓" if ok else ("∅" if is_rl else "✗")
            detail = f"orderId={oid}" if oid else msg[:40]
            print(f"  [{i+1}/{len(mutations)}] {mark} {name:25s} → "
                  f"errno={errno} {detail}  pid={self.pid}")

            tested += 1
            self._record(f"diff:createV2", {
                "variant": name, "errno": errno, "msg": msg,
                "passed": ok, "rate_limited": is_rl, "pid": self.pid,
            })

            # 限流 → 换项目继续
            if is_rl and fallback_pids:
                switched = False
                for fp in fallback_pids:
                    if self._switch_project(fp):
                        fallback_pids.remove(fp)
                        url = f"https://show.bilibili.com/api/ticket/order/createV2?project_id={self.pid}"
                        base = _make_base()
                        print(f"  → 切换项目: {self.pid} ({self.project.get('name','')[:20]})")
                        switched = True
                        break
                if not switched:
                    remaining = len(mutations) - i - 1
                    print(f"  ⚠ 无可用备用项目, 剩余 {remaining} 变体无法测试")
                    break

        print(f"\n  测试 {tested}/{len(mutations)+1} 变体 ({len(fallback_pids)} 备用项目)")

    # ── 错误码穷举 ──

    def enumerate_errors(self):
        """通过故意构造错误请求来收集错误码"""
        print(f"\n{'═' * 60}")
        print(f"  错误码穷举")
        print(f"{'═' * 60}")

        tests = [
            ("empty_payload", "https://show.bilibili.com/api/ticket/order/prepare?project_id=0", {}),
            ("bad_project", "https://show.bilibili.com/api/ticket/order/prepare?project_id=99999999",
             {"project_id": 99999999, "screen_id": 1, "sku_id": 1, "order_type": 1, "count": 1, "buyer_info": ""}),
            ("no_auth", "https://show.bilibili.com/api/ticket/order/prepare?project_id=0",
             {"project_id": 0, "screen_id": 0, "sku_id": 0}),
            ("string_ids", "https://show.bilibili.com/api/ticket/order/prepare?project_id=0",
             {"project_id": "x", "screen_id": "y", "sku_id": "z"}),
        ]

        for name, url, payload in tests:
            resp = self._send(url, payload)
            errno = resp.get("errno")
            msg = resp.get("msg", resp.get("message", ""))
            print(f"  {name:20s} → errno={errno} msg={msg[:60]}")
            self._record("error_enum", {"test": name, "errno": errno, "msg": msg})

    # ── 报告生成 ──

    def print_report(self):
        print(f"\n{'═' * 60}")
        print(f"  逆向报告: {self.project.get('name', f'project {self.pid}')}")
        print(f"{'═' * 60}")

        print(f"\n  项目状态: {self.project.get('sale_flag')}"
              f" (code={self.project.get('sale_flag_number')})")
        print(f"  实名要求: id_bind={self.project.get('id_bind')}"
              f" buyer_info={self.project.get('buyer_info')}")
        print(f"  可购票档: {len(self.available)} 个")
        if self.available:
            for a in self.available[:3]:
                print(f"    [{a['sku_id']}] {a['desc']} ¥{a['price_yuan']}")

        print(f"\n  已发现错误码 ({len(self.errors)}):")
        for errno in sorted(self.errors.keys(), key=lambda x: (0 if isinstance(x, int) else 1, x or 0)):
            print(f"    {str(errno):>8s}  {self.errors[errno][:60]}")

        # 统计
        diff_pass = sum(1 for e in self.log
                        if e["category"].startswith("diff:")
                        and e.get("passed"))
        diff_total = sum(1 for e in self.log
                         if e["category"].startswith("diff:"))
        rl_count = sum(1 for e in self.log
                       if e.get("rate_limited"))

        print(f"\n  差分测试: {diff_pass}/{diff_total} 通过"
              f" (限流干扰: {rl_count})")

    def run(self, quick: bool = False, deep: bool = False):
        print(f"\n{'═' * 60}")
        print(f"  B站会员购 API 逆向工程")
        print(f"  目标: {self.project.get('name', f'ID={self.pid}')}")
        print(f"{'═' * 60}")

        # 阶段1: 端点扫描
        endpoints = self.scan_endpoints()

        # 阶段2: prepare 接口逆向
        if deep or not quick:
            print(f"\n{'─' * 40}")
            print(f"  阶段2: prepare 接口逆向")
            print(f"{'─' * 40}")
            self.reverse_prepare()
        else:
            print(f"\n  快速模式: 跳过 prepare 差分测试")

        # 阶段3: createV2 接口逆向
        print(f"\n{'─' * 40}")
        print(f"  阶段3: createV2 接口逆向")
        print(f"{'─' * 40}")
        self.reverse_create_v2()

        # 阶段4: 错误码
        if deep:
            self.enumerate_errors()

        # 报告
        self.print_report()

        # 发现总结
        print(f"\n{'═' * 60}")
        print(f"  发现总结")
        print(f"{'═' * 60}")

        findings = defaultdict(list)
        for entry in self.log:
            if entry["category"].startswith("diff:"):
                if entry.get("rate_limited"):
                    findings["limited"].append(entry["variant"])
                elif entry.get("passed"):
                    findings["passed"].append(entry["variant"])
                elif entry.get("biz_error"):
                    findings["biz_errors"].append(
                        f"{entry['variant']} (errno={entry['errno']})")

        if findings["passed"]:
            print(f"  格式兼容: {', '.join(findings['passed'][:8])}")
        if findings["biz_errors"]:
            print(f"  业务拒绝: {', '.join(findings['biz_errors'][:8])}")
        if findings["limited"]:
            print(f"  限流干扰: {len(findings['limited'])} 项无法判断")


def main():
    parser = argparse.ArgumentParser(
        description="B站会员购 API 逆向工程工具")
    parser.add_argument("project_id", type=int, help="项目ID")
    parser.add_argument("--quick", action="store_true", help="快速模式")
    parser.add_argument("--deep", action="store_true", help="深度模式 (含穷举)")
    args = parser.parse_args()

    reverser = APIReverser(args.project_id)
    reverser.run(quick=args.quick, deep=args.deep)


if __name__ == "__main__":
    main()
