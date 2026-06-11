#!/usr/bin/env python3
"""
B站会员购抢票工具 - CLI

用法:
  python main.py info    <project_id>        查看项目摘要
  python main.py skus    <project_id>        列出所有票档 (表格)
  python main.py check   <project_id>        检查可购票档
  python main.py monitor <project_id>        监控 (不购买)
  python main.py buy     <project_id> <sku>  自动抢票
  python main.py login                       鉴权: Cookie 登录 + 验证
  python main.py auth                        鉴权: 验证当前登录状态
  python main.py proxy   on|off|show         代理配置
  python main.py notify  tg|feishu|on|off|show  通知配置
  python main.py buyer   add|list|del        购票人管理

三种测试案例:
  1001653 - 不可售 (BW2026, 未开售)
  1001405 - 预售中 (凡人修仙传x餐厅)
  102194  - 已结束 (BW2025)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time as _time
from time import sleep

from bili_api import (
    BiliTicketAPI,
    SALE_FLAG_MAP,
    PROJECT_SALE_FLAG_MAP,
    PROJECT_TYPE_MAP,
    SCREEN_TICKET_TYPE_MAP,
    DELIVERY_TYPE_MAP,
    load_buyers,
    save_buyers,
    validate_buyer,
    validate_buyer_name,
    validate_id_card,
    validate_phone,
)
from config import load_config, save_config, get_proxy


# ═══════════════════════════════════════════════════════════════
# 鉴权命令
# ═══════════════════════════════════════════════════════════════

def _show_qr(url: str):
    """生成并展示二维码图片"""
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=12, border=4,
                            error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        img_path = os.path.join(os.path.dirname(__file__), ".qrcode.png")
        img.save(img_path)

        import subprocess, sys as _sys
        if _sys.platform == "darwin":
            subprocess.Popen(["open", img_path])
        elif _sys.platform == "win32":
            os.startfile(img_path)
        else:
            subprocess.Popen(["xdg-open", img_path])

        print(f"  (二维码图片已保存: {img_path})")
    except Exception as e:
        print(f"  [提示] 二维码图片生成失败: {e}")


def cmd_login(args):
    print(f"\n{' B站扫码登录 ':━^60}")
    print()
    print("  正在生成登录二维码...")
    print()

    qr_url = None

    def _capture_url(url):
        nonlocal qr_url
        qr_url = url
        _show_qr(url)

    success, result = BiliTicketAPI.qrcode_login(show_func=_capture_url)

    if success:
        print()

        if qr_url:
            print(f"  如果没有自动打开, 手动访问以下链接扫码:")
            print(f"  {qr_url}")
            print()

        api = BiliTicketAPI(cookie=result)
        ok, uname, user = api.verify_auth()
        print(f"\n  {' 登录成功! ':━^40}")
        if ok:
            print(f"  用户名: {uname}")
            print(f"  UID   : {user.get('mid', '')}")
        else:
            print(f"  验证提示: {uname}")
        print(f"  Cookie 已保存到 config.json")
    else:
        print(f"\n  [失败] {result}")
        print(f"  请重试: python main.py login")


def cmd_auth(args):
    cfg = load_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        print("[信息] 尚未登录, 请先执行: python main.py login")
        return

    api = BiliTicketAPI(cookie=cookie)
    success, msg, user = api.verify_auth()

    auth = cfg.get("auth", {})
    print(f"\n{' 鉴权状态 ':━^40}")
    print(f"  已保存登录: {'是' if cookie else '否'}")
    print(f"  验证通过  : {'是' if success else '否'}")
    if success:
        print(f"  用户名    : {msg}")
        print(f"  UID       : {user.get('mid', '')}")
    else:
        print(f"  错误信息  : {msg}")
    print(f"  CSRF Token: {'已获取' if api.csrf else '缺失'}")

    if not success:
        print("\n  需要重新登录: python main.py login")


# ═══════════════════════════════════════════════════════════════
# 代理命令
# ═══════════════════════════════════════════════════════════════

def cmd_proxy(args):
    cfg = load_config()

    if args.action == "show":
        proxy = cfg.get("proxy", {})
        print(f"\n{' 代理配置 ':━^40}")
        print(f"  启用  : {'是' if proxy.get('enabled') else '否'}")
        print(f"  HTTP  : {proxy.get('http', '(未设置)') or '(未设置)'}")
        print(f"  HTTPS : {proxy.get('https', '(未设置)') or '(未设置)'}")
        return

    if args.action == "on":
        cfg["proxy"]["enabled"] = True
        save_config(cfg)
        print("[代理] 已启用")
        if not cfg["proxy"].get("http"):
            print("[提示] 尚未配置代理地址, 请先设置: python main.py proxy set")

    elif args.action == "off":
        cfg["proxy"]["enabled"] = False
        save_config(cfg)
        print("[代理] 已禁用")

    elif args.action == "set":
        http_proxy = input("  HTTP 代理 (例: http://127.0.0.1:7890): ").strip()
        https_proxy = input("  HTTPS 代理 (回车使用同HTTP): ").strip()
        cfg["proxy"]["http"] = http_proxy
        cfg["proxy"]["https"] = https_proxy or http_proxy
        cfg["proxy"]["enabled"] = True
        save_config(cfg)
        print(f"[代理] 已设置: {http_proxy}")


# ═══════════════════════════════════════════════════════════════
# 通知命令
# ═══════════════════════════════════════════════════════════════

def cmd_notify(args):
    cfg = load_config()
    notify = cfg.get("notification", {})

    if args.action == "show":
        print(f"\n{' 通知配置 ':━^40}")
        print(f"  启用      : {'是' if notify.get('enabled') else '否'}")
        print(f"  Telegram  : {'已配置' if notify.get('tg_token') else '未配置'}")
        if notify.get('tg_token'):
            print(f"    Token   : {notify['tg_token'][:10]}...")
            print(f"    Chat ID : {notify.get('tg_chat_id', '')}")
        print(f"  飞书      : {'已配置' if notify.get('feishu_webhook') else '未配置'}")
        if notify.get('feishu_webhook'):
            url = notify['feishu_webhook']
            print(f"    Webhook : {url[:40]}...")
        return

    if args.action == "tg":
        token = input("  Telegram Bot Token: ").strip()
        chat_id = input("  Telegram Chat ID: ").strip()
        if token and chat_id:
            notify["tg_token"] = token
            notify["tg_chat_id"] = chat_id
            notify["enabled"] = True
            cfg["notification"] = notify
            save_config(cfg)
            print("[通知] Telegram 已配置")

            test_msg = {"chat_id": chat_id, "text": "B站抢票工具: 通知配置成功!",
                         "parse_mode": "Markdown"}
            try:
                import requests
                r = requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json=test_msg, timeout=10)
                if r.status_code == 200:
                    print("[通知] Telegram 测试消息发送成功!")
                else:
                    print(f"[通知] 测试发送失败: {r.text}")
            except Exception as e:
                print(f"[通知] 测试发送异常: {e}")
        else:
            print("[取消]")

    elif args.action == "feishu":
        webhook = input("  飞书 Webhook URL: ").strip()
        if webhook:
            notify["feishu_webhook"] = webhook
            notify["enabled"] = True
            cfg["notification"] = notify
            save_config(cfg)
            print("[通知] 飞书已配置")

            try:
                import requests
                payload = {
                    "msg_type": "text",
                    "content": {"text": "B站抢票工具: 通知配置成功!"},
                }
                r = requests.post(webhook, json=payload, timeout=10)
                if r.status_code == 200:
                    print("[通知] 飞书测试消息发送成功!")
                else:
                    print(f"[通知] 测试发送失败: {r.text}")
            except Exception as e:
                print(f"[通知] 测试发送异常: {e}")
        else:
            print("[取消]")

    elif args.action == "on":
        notify["enabled"] = True
        cfg["notification"] = notify
        save_config(cfg)
        print("[通知] 已启用")

    elif args.action == "off":
        notify["enabled"] = False
        cfg["notification"] = notify
        save_config(cfg)
        print("[通知] 已禁用")


# ═══════════════════════════════════════════════════════════════
# 购票人管理命令
# ═══════════════════════════════════════════════════════════════

def cmd_buyer(args):
    if args.action == "add":
        print(f"\n{' 添加购票人 ':━^50}")
        name = input("  姓名: ").strip()
        ok, result = validate_buyer_name(name)
        if not ok:
            print(f"[错误] {result}")
            return
        name = result

        id_card = input("  身份证号: ").strip()
        ok, result = validate_id_card(id_card)
        if not ok:
            print(f"[错误] {result}")
            return
        id_card = result

        phone = input("  手机号 (可选): ").strip()
        if phone:
            ok, result = validate_phone(phone)
            if not ok:
                print(f"[错误] {result}")
                return
            phone = result

        buyers = load_buyers()
        for b in buyers:
            if b.get("id_card") == id_card:
                print(f"[错误] 身份证号已存在: {b['name']}")
                return

        buyers.append({"name": name, "id_card": id_card, "phone": phone})
        save_buyers(buyers)
        print(f"[完成] 已保存: {name} ({id_card[:3]}****{id_card[-3:]})")

    elif args.action == "list":
        buyers = load_buyers()
        if not buyers:
            print("[空] 还没有添加购票人，使用: python main.py buyer add")
        else:
            print(f"\n{' 购票人列表 ':━^50}")
            for i, b in enumerate(buyers, 1):
                id_card = b.get("id_card", "")
                id_masked = f"{id_card[:3]}****{id_card[-3:]}" if len(id_card) > 6 else "***"
                phone = b.get("phone", "")
                phone_masked = ""
                if phone and len(phone) == 11:
                    phone_masked = f" {phone[:3]}****{phone[7:]}"
                print(f"  [{i}] {b['name']}  {id_masked}{phone_masked}")
            print(f"\n  共 {len(buyers)} 人")

    elif args.action == "del":
        buyers = load_buyers()
        idx = args.index - 1
        if 0 <= idx < len(buyers):
            removed = buyers.pop(idx)
            save_buyers(buyers)
            print(f"[完成] 已删除: {removed['name']}")
        else:
            print(f"[错误] 无效序号: {args.index}")


# ═══════════════════════════════════════════════════════════════
# 项目查询命令
# ═══════════════════════════════════════════════════════════════

def cmd_info(args):
    api = BiliTicketAPI()
    data = api.get_project_summary(args.project_id)
    if data is None:
        return

    p = data
    sf = PROJECT_SALE_FLAG_MAP.get(p.get("sale_flag_number", -1), "未知")
    venue = p.get("venue_info", {})
    ticket_type = PROJECT_TYPE_MAP.get(p.get("type", 0), "未知")
    delivery = []
    if p.get("has_eticket"):
        delivery.append("电子票")
    if p.get("has_paper_ticket"):
        delivery.append("纸质票")

    print(f"\n{' 项目详情 ':━^60}")
    print(f"  名称    : {p.get('name', '未知')}")
    print(f"  ID      : {p.get('id', '')}")
    print(f"  票种    : {ticket_type} (project_type={p.get('project_type')})")
    print(f"  地点    : {venue.get('province_name', '')} {venue.get('city_name', '')}"
          f" {venue.get('name', '')}")
    print(f"  地址    : {venue.get('address_detail', '')}")
    print(f"  时间    : {p.get('project_label', '')}")
    print(f"  状态    : {sf} (code={p.get('sale_flag_number')})")
    print(f"  按钮    : {'立即购买' if p.get('default_button') == 1 else '提醒/待开售'}")
    print(f"  实名    : {'需要' if p.get('id_bind') else '不需要'}"
          f" (buyer_info={p.get('buyer_info', '无')})")
    print(f"  选座    : {'支持' if p.get('pick_seat') else '不支持'}")
    print(f"  物流    : {', '.join(delivery) if delivery else '无'}")
    print(f"  退票    : {p.get('refund_desc', '')}")
    print(f"  票价    : ¥{p.get('price_low', 0)/100:.0f} - ¥{p.get('price_high', 0)/100:.0f}")
    wish = p.get("wish_info", {})
    print(f"  想看    : {wish.get('count', 0)} 人")

    screens = p.get("screen_list", [])
    print(f"\n  ── 场次列表 ({len(screens)}) ──")
    if not screens:
        print("  (暂无场次数据，可能还未设置开售时间)")

    for sc in screens:
        sf_sc = SALE_FLAG_MAP.get(
            sc.get("saleFlag", {}).get("number", 0), "未知")
        clk = "✓" if sc.get("clickable") else "✗"
        d_name = DELIVERY_TYPE_MAP.get(sc.get("delivery_type", 1), "")
        print(f"\n  [{sc['id']}] {sc['name']}  [{sf_sc}] 可购:{clk}  "
              f"{d_name}")
        for tk in sc.get("ticket_list", []):
            sf_tk = SALE_FLAG_MAP.get(tk.get("sale_flag_number", 0), "未知")
            clk_tk = "✓" if tk.get("clickable") else "✗"
            stock = tk.get("num", 0)
            stock_str = f"余{stock}" if stock < 100 else ""
            limit = tk.get("static_limit", {}).get("num", "")
            limit_str = f"限购{limit}" if limit and limit < 100 else ""
            sale_t = tk.get("sale_start", "")[:10] if tk.get("sale_start") else ""
            print(f"       [{tk['id']}] {tk['desc']:<14s} ¥{tk['price']/100:>7.0f}"
                  f"  [{sf_tk}] 可购:{clk_tk}  {stock_str}  {limit_str}"
                  f"{' ('+sale_t+')' if sale_t else ''}")


def cmd_skus(args):
    api = BiliTicketAPI()
    skus = api.get_project_skus(args.project_id)
    if not skus:
        print("[提示] 未找到票档")
        return

    print(f"\n{'SKU_ID':>8} {'场次ID':>8} {'场次 (时间段)':<22} {'票档':<16}"
          f" {'单价':>8} {'类型':<8} {'状态':<8} {'余量':>6}")
    print("-" * 96)
    for s in skus:
        flag = SALE_FLAG_MAP.get(s["sale_flag_number"], "未知")
        sc_name = s["screen_name"][:20] if len(s["screen_name"]) <= 22 \
            else s["screen_name"][:20] + ".."
        num_str = str(s["num"]) if s["num"] < 500 else "充足"
        ttype = s.get("ticket_type", s.get("project_type", ""))[:8]
        print(f"{s['sku_id']:>8} {s['screen_id']:>8} {sc_name:<22} "
              f"{s['desc']:<16} ¥{s['price_yuan']:>7.0f} {ttype:<8} "
              f"{flag:<8} {num_str:>6}")


def cmd_check(args):
    api = BiliTicketAPI()
    status, available = api.check_ticket_available(
        args.project_id,
        sku_id=args.sku_id or 0,
        screen_id=args.screen_id or 0,
        min_price=args.min_price,
        max_price=args.max_price,
    )

    sf = PROJECT_SALE_FLAG_MAP.get(status.get("sale_flag_number", -1), "未知")
    print(f"\n  项目: {status.get('name', '未知')}")
    print(f"  票种: {status.get('project_type', '')}")
    print(f"  场馆: {status.get('venue', '')}")
    print(f"  状态: {sf} (code={status.get('sale_flag_number')})")
    print(f"  票价: ¥{status.get('price_low', 0)/100:.0f}"
          f" - ¥{status.get('price_high', 0)/100:.0f}")
    print(f"  实名: {'需要' if status.get('id_bind') else '不需要'}"
          f" (buyer_info={status.get('buyer_info')})")

    if not available:
        print(f"\n  [提示] 当前没有可购票档")
        if status.get("sale_flag_number") == 5:
            print(f"  原因: 尚未开售 / 还未设置场次")
        elif status.get("sale_flag_number") == 102:
            print(f"  原因: 活动已结束")
        else:
            print(f"  原因: 已售罄或不在销售时间内")
        return

    print(f"\n  可购票档 ({len(available)}):")
    print(f"  {'SKU':>8} {'场次':>8} {'场次 (时间段)':<20} {'票档':<16}"
          f" {'单价':>8} {'余量':>6} {'发售时间'}")
    print("  " + "-" * 88)
    for s in available:
        sc_name = s["screen_name"][:18] if len(s["screen_name"]) <= 20 \
            else s["screen_name"][:18] + ".."
        sale_info = f"{s.get('sale_start', '')[:16]}"
        print(f"  {s['sku_id']:>8} {s['screen_id']:>8} {sc_name:<20} "
              f"{s['desc']:<16} ¥{s['price_yuan']:>7.0f} {s['num']:>6} "
              f"{sale_info}")


# ═══════════════════════════════════════════════════════════════
# 监控与购买命令
# ═══════════════════════════════════════════════════════════════

def cmd_monitor(args):
    api = BiliTicketAPI()

    print(f"\n  监控模式: 仅监控 (不购买)")
    print(f"  项目: {args.project_id}")
    if args.sku_id:
        print(f"  票档: {args.sku_id}")
    else:
        print(f"  票档: 所有可售票档")
    if args.screen_id:
        print(f"  场次: {args.screen_id}")
    print(f"  间隔: {args.interval}s")
    print(f"  按 Ctrl+C 退出\n")

    count = 0
    while True:
        count += 1
        try:
            status, available = api.check_ticket_available(
                args.project_id,
                sku_id=args.sku_id or 0,
                screen_id=args.screen_id or 0,
                min_price=args.min_price,
                max_price=args.max_price,
            )

            ts = _time.strftime("%H:%M:%S")
            proj_sf = PROJECT_SALE_FLAG_MAP.get(
                status.get("sale_flag_number", -1), "未知")

            if status.get("sale_flag_number") == 102:
                print(f"[{ts} #{count}] 项目已结束 ({status.get('sale_flag')})")
                break

            if available:
                for t in available:
                    sc_name = t["screen_name"][:20]
                    print(f"[{ts} #{count}] 可购 | {sc_name} | {t['desc']} "
                          f"¥{t['price_yuan']} 余{t['num']} | "
                          f"发售:{t.get('sale_start', '?')[:10]}")
            else:
                print(f"[{ts} #{count}] 无票 | 状态: {proj_sf}")
            sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[退出]")
            break


def cmd_buy(args):
    cfg = load_config()
    if not cfg.get("cookie"):
        print("[错误] 未登录, 请先执行: python main.py login")
        return

    api = BiliTicketAPI()
    success, msg, user = api.verify_auth()

    if not success:
        print(f"[错误] 鉴权失败: {msg}")
        print("       请重新登录: python main.py login")
        return

    print(f"\n  鉴权通过: {msg} (uid={user.get('mid', '?')})")

    buyers = load_buyers()
    buyer_name = args.name or (buyers[0]["name"] if buyers else "")
    buyer_phone = args.phone or (buyers[0].get("phone", "") if buyers else "")

    status, available = api.check_ticket_available(
        args.project_id, sku_id=args.sku_id, screen_id=args.screen_id or 0)
    if not status:
        print("[错误] 无法获取项目信息")
        return

    proj_sale = status.get("sale_flag_number", -1)
    if proj_sale == 102:
        print(f"[终止] 项目已结束: {status.get('name')}")
        return

    target_desc = ""
    pay_money = 0
    actual_screen = args.screen_id or 0
    if available:
        t = available[0]
        target_desc = f"{t['screen_name'][:20]} - {t['desc']}"
        pay_money = int(t["price"])
        if not actual_screen:
            actual_screen = t["screen_id"]

    dry_run = not args.real
    dry_label = "DRY-RUN (模拟)" if dry_run else "真实下单"

    print(f"{f' 自动抢票 ({dry_label}) ':━^60}")
    print(f"  项目    : {status.get('name')}")
    if target_desc:
        print(f"  票档    : {target_desc}")
        print(f"  单价    : ¥{pay_money / 100:.2f}")
    print(f"  数量    : {args.num}")
    print(f"  购票人  : {buyer_name or '(未设置)'}")
    print(f"  手机号  : {buyer_phone or '(未设置)'}")
    print(f"  模式    : {dry_label}")

    proxy = cfg.get("proxy", {})
    if proxy.get("enabled") and proxy.get("http"):
        print(f"  代理    : {proxy['http']}")

    notify = cfg.get("notification", {})
    if notify.get("enabled"):
        channels = [c for c in ["Telegram", "飞书"]
                    if notify.get({"Telegram": "tg_token", "飞书": "feishu_webhook"}[c])]
        if channels:
            print(f"  通知    : {', '.join(channels)}")

    if args.sale_time:
        print(f"  开售时间: {args.sale_time} (定时等待)")
    if args.token:
        print(f"  Token   : {args.token[:15]}...")
    print(f"  每token重试: {args.max_retry}次")
    print(f"{'─' * 60}")

    if proj_sale == 5 and not available:
        print(f"[提示] 项目当前不可售 ({status.get('sale_flag')})")
        if not args.real:
            print(f"       dry-run 模式下将直接打印模拟 payload")

    if status.get("id_bind") and not buyer_name and not dry_run:
        print(f"[错误] 该项目需要购票人信息!")
        print(f"       请添加购票人: python main.py buyer add")
        print(f"       或使用 --name --phone 参数")
        return

    print()

    try:
        result = api.sniper_buy(
            project_id=args.project_id,
            sku_id=args.sku_id,
            screen_id=actual_screen,
            buy_num=args.num,
            buyer_name=buyer_name,
            buyer_phone=buyer_phone,
            pay_money=pay_money,
            dry_run=dry_run,
            token=args.token or "",
            wait_sale=bool(args.sale_time),
            sale_time_str=args.sale_time or "",
            poll_interval=args.interval,
            max_retry_per_token=args.max_retry,
        )
        if result:
            if result.get("dry_run"):
                print(f"\n[DRY-RUN] 模拟完成 (未实际下单)")
                print(f"  使用 --real 参数可真实提交订单")
            else:
                print(f"\n{' 下单完成 ':━^60}")
                print(f"  order_id: {result.get('order_id', '-')}")
        else:
            if dry_run:
                print("\n[DRY-RUN] 模拟完成")
            else:
                print("\n[结束] 未成功下单")
    except KeyboardInterrupt:
        print("\n[退出]")


# ═══════════════════════════════════════════════════════════════
# 诊断命令
# ═══════════════════════════════════════════════════════════════

def cmd_diagnose(args):
    """诊断接口参数 - 探测必需/可选字段 + 格式敏感性"""
    from bili_api import generate_ctoken
    import time as _t

    api = BiliTicketAPI()
    pid = args.project_id

    sid = args.screen_id or 0
    sku = args.sku_id or 0

    status, available = api.check_ticket_available(pid)
    if not status:
        print(f"[错误] 无法获取项目 {pid} 信息")
        return

    print(f"\n  项目: {status.get('name')}")
    print(f"  状态: {status.get('sale_flag')} "
          f"(code={status.get('sale_flag_number')})")

    if not available:
        print(f"  无可购票档 (需要 --screen-id --sku-id)")
        if not sid or not sku:
            return
    else:
        t = available[0]
        if not sid:
            sid = t["screen_id"]
        if not sku:
            sku = t["sku_id"]
        print(f"  票档: {t['desc']} ¥{t['price_yuan']}")

    # ── prepare 接口诊断 ──
    print(f"\n{' 诊断 prepare 接口 ':━^60}")
    base = {"project_id": pid, "screen_id": sid, "sku_id": sku,
            "order_type": 1, "count": 1, "buyer_info": ""}

    r = api._request("POST", f"/api/ticket/order/prepare?project_id={pid}",
                      json=base)
    ok = r.get("code") == 0 or r.get("errno") == 0
    print(f"  最小payload: {'✓' if ok else '✗'} "
          f"errno={r.get('errno')}")

    extras = {
        "ctoken": generate_ctoken(),
        "ignoreRequestLimit": True,
        "newRisk": True,
        "requestSource": "neul-next",
        "ticket_agent": "",
    }
    for name, val in extras.items():
        p = {**base, name: val}
        r = api._request("POST", f"/api/ticket/order/prepare?project_id={pid}",
                          json=p)
        ok = r.get("code") == 0 or r.get("errno") == 0
        marker = "← 解决之前的错误" if ok != (r.get("code")==0 or r.get("errno")==0) else ""
        print(f"  +{name:22s}: errno={r.get('errno')} {marker}")

    # ── createV2 诊断 ──
    prep = api.prepare_order(pid, sid, sku, 1)
    token = (prep.get("data", {}) or {}).get("token", "")
    if not token:
        print(f"\n  prepare 失败, 跳过 createV2 诊断")
        return

    print(f"\n{' 诊断 createV2 接口 ':━^60}")
    dev_id = api._extract_device_id()
    now_ms = int(_t.time() * 1000)
    base_cv2 = {
        "project_id": pid, "screen_id": sid, "sku_id": sku,
        "count": 1, "pay_money": (available[0]["price"] if available else 1000),
        "timestamp": now_ms,
        "token": token, "deviceId": dev_id,
        "order_type": 1, "id_bind": status.get("id_bind", 0),
        "need_contact": 1 if status.get("id_bind", 0) == 0 else 0,
        "is_package": 0, "package_num": 1,
        "version": "1.1.0", "coupon_code": "", "again": 0,
        "contactInfo": {"uid": 630366719, "username": "x", "tel": "13800138000"},
        "buyer": "x", "tel": "13800138000",
        "clickPosition": {"x": 255, "y": 750,
                           "origin": now_ms - 5000, "now": now_ms},
        "ctoken": generate_ctoken(),
        "requestSource": "neul-next", "newRisk": True,
    }

    tests = [
        "完整payload",
        "-ctoken", "-requestSource", "-newRisk",
        "-version", "ctoken=空",
        "clickPosition=string",
        "clickPosition=missing",
    ]

    for case in tests:
        payload = base_cv2.copy()
        label = case

        if case == "完整payload":
            pass
        elif case.startswith("-"):
            field = case[1:]
            payload.pop(field, None)
        elif "=" in case:
            k, v = case.split("=", 1)
            if v == "空":
                payload[k] = ""
            elif v == "string":
                payload[k] = json.dumps(payload[k])
            else:
                payload[k] = v

        h = api.session.headers.copy()
        h["x-risk-header"] = f"platform/pc uid/630366719 deviceId/{dev_id}"
        r = api._request("POST",
            f"/api/ticket/order/createV2?project_id={pid}",
            headers=h, json=payload)
        errno = r.get("errno")
        msg = r.get("msg", r.get("message", ""))
        oid = (r.get("data", {}) or {}).get("orderId", "")
        mark = "✓" if errno in (0, 100048) or oid else "✗"
        detail = f"orderId={oid}" if oid else msg[:45]
        print(f"  {mark} {label:25s} → errno={errno} {detail}")

    print(f"\n{'━' * 60}")
    print(f"  ✓ = 格式正确  ✗ = 缺失/格式错误/被风控")


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="B站会员购抢票工具 (show.bilibili.com)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
鉴权与配置:
  python main.py login                        扫码登录 (生成二维码图片)
  python main.py auth                         验证登录状态
  python main.py proxy set                    设置代理
  python main.py notify tg                    设置 Telegram 通知
  python main.py notify feishu                设置飞书通知
  python main.py buyer add                    添加购票人
  python main.py buyer list                   查看购票人

项目查询:
  python main.py info 1001405                 项目详情
  python main.py skus 1001405                 票档表格
  python main.py check 1001405                可购票档

监控与抢票:
  python main.py monitor 1001405              监控所有可售票档
  python main.py monitor 1001405 --sku-id 877212  监控指定票档
  python main.py buy 1001405 877212            DRY-RUN 模拟抢票 (默认,不提交)
  python main.py buy 1001405 877212 --real     真实抢票
  python main.py buy 1001405 877212 --real --sale-time \"2026-06-10 18:00:00\"  定时抢票

测试案例:
  1001653  不可售 (BW2026, 未开售)
  1001405  预售中 (凡人修仙传x餐厅)
  102194   已结束 (BW2025)
        """,
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    p_login = sub.add_parser("login", help="Cookie 登录并验证")
    p_login.set_defaults(func=cmd_login)

    p_auth = sub.add_parser("auth", help="检查登录状态")
    p_auth.set_defaults(func=cmd_auth)

    p_proxy = sub.add_parser("proxy", help="代理配置")
    p_proxy_sub = p_proxy.add_subparsers(dest="action", help="操作")
    p_proxy_set = p_proxy_sub.add_parser("set", help="设置代理地址")
    p_proxy_set.set_defaults(func=cmd_proxy, action="set")
    p_proxy_on = p_proxy_sub.add_parser("on", help="启用代理")
    p_proxy_on.set_defaults(func=cmd_proxy, action="on")
    p_proxy_off = p_proxy_sub.add_parser("off", help="禁用代理")
    p_proxy_off.set_defaults(func=cmd_proxy, action="off")
    p_proxy_show = p_proxy_sub.add_parser("show", help="查看代理配置")
    p_proxy_show.set_defaults(func=cmd_proxy, action="show")

    p_notify = sub.add_parser("notify", help="通知配置 (TG / 飞书)")
    p_notify_sub = p_notify.add_subparsers(dest="action", help="操作")
    p_notify_tg = p_notify_sub.add_parser("tg", help="配置 Telegram")
    p_notify_tg.set_defaults(func=cmd_notify, action="tg")
    p_notify_fs = p_notify_sub.add_parser("feishu", help="配置飞书")
    p_notify_fs.set_defaults(func=cmd_notify, action="feishu")
    p_notify_on = p_notify_sub.add_parser("on", help="启用通知")
    p_notify_on.set_defaults(func=cmd_notify, action="on")
    p_notify_off = p_notify_sub.add_parser("off", help="禁用通知")
    p_notify_off.set_defaults(func=cmd_notify, action="off")
    p_notify_show = p_notify_sub.add_parser("show", help="查看通知配置")
    p_notify_show.set_defaults(func=cmd_notify, action="show")

    p_buyer = sub.add_parser("buyer", help="管理购票人")
    p_buyer_sub = p_buyer.add_subparsers(dest="action", help="操作")
    p_buyer_add = p_buyer_sub.add_parser("add", help="添加购票人 (含实名校验)")
    p_buyer_add.set_defaults(func=cmd_buyer, action="add")
    p_buyer_list = p_buyer_sub.add_parser("list", help="列表购票人")
    p_buyer_list.set_defaults(func=cmd_buyer, action="list")
    p_buyer_del = p_buyer_sub.add_parser("del", help="删除购票人")
    p_buyer_del.add_argument("index", type=int, help="序号")
    p_buyer_del.set_defaults(func=cmd_buyer, action="del")

    p_diag = sub.add_parser("diagnose", help="诊断接口参数 (必需/可选字段)")
    p_diag.add_argument("project_id", type=int, help="项目 ID")
    p_diag.add_argument("--screen-id", type=int, default=0)
    p_diag.add_argument("--sku-id", type=int, default=0)
    p_diag.set_defaults(func=cmd_diagnose)

    p_info = sub.add_parser("info", help="查看项目详情 (含场次/票档/价格/时间)")
    p_info.add_argument("project_id", type=int, help="项目 ID")
    p_info.set_defaults(func=cmd_info)

    p_skus = sub.add_parser("skus", help="票档表格 (含价格/状态/余量)")
    p_skus.add_argument("project_id", type=int, help="项目 ID")
    p_skus.set_defaults(func=cmd_skus)

    p_check = sub.add_parser("check", help="检查可购票档 (含发售时间)")
    p_check.add_argument("project_id", type=int, help="项目 ID")
    p_check.add_argument("--sku-id", type=int, default=0)
    p_check.add_argument("--screen-id", type=int, default=0)
    p_check.add_argument("--min-price", type=int, default=0)
    p_check.add_argument("--max-price", type=int, default=99999999)
    p_check.set_defaults(func=cmd_check)

    p_monitor = sub.add_parser("monitor", help="监控票档 (不购买)")
    p_monitor.add_argument("project_id", type=int, help="项目 ID")
    p_monitor.add_argument("--sku-id", type=int, default=0)
    p_monitor.add_argument("--screen-id", type=int, default=0)
    p_monitor.add_argument("--min-price", type=int, default=0)
    p_monitor.add_argument("--max-price", type=int, default=99999999)
    p_monitor.add_argument("--interval", type=float, default=0.5)
    p_monitor.set_defaults(func=cmd_monitor)

    p_buy = sub.add_parser("buy", help="自动抢票 (默认 dry-run, --real 真实下单)")
    p_buy.add_argument("project_id", type=int, help="项目 ID")
    p_buy.add_argument("sku_id", type=int, help="票档 ID")
    p_buy.add_argument("--screen-id", type=int, default=0)
    p_buy.add_argument("--num", type=int, default=1, help="购买数量")
    p_buy.add_argument("--name", type=str, default="", help="购票人姓名")
    p_buy.add_argument("--phone", type=str, default="", help="手机号")
    p_buy.add_argument("--token", type=str, default="",
                        help="下单 token (从浏览器 confirmOrder 页面 URL 提取)")
    p_buy.add_argument("--real", action="store_true",
                        help="真实下单 (默认 dry-run 模拟)")
    p_buy.add_argument("--sale-time", type=str, default="",
                        help="开售时间, 如 '2026-06-10 18:00:00' (定时等待)")
    p_buy.add_argument("--interval", type=float, default=0.3,
                        help="轮询/退避间隔 秒")
    p_buy.add_argument("--max-retry", type=int, default=60,
                        help="每token重试次数 (默认 60)")
    p_buy.set_defaults(func=cmd_buy)

    args = parser.parse_args()
    if not args.command:
        interactive_menu()
        return
    args.func(args)


# ═══════════════════════════════════════════════════════════════
# 交互式菜单
# ═══════════════════════════════════════════════════════════════

def interactive_menu():
    """纯 CLI 驱动的交互式菜单, 无需记忆命令"""
    from bili_api import generate_ctoken

    cfg = load_config()
    api = None
    last_project = None
    last_avail = None

    logged_in = bool(cfg.get("cookie"))
    if logged_in:
        try:
            api = BiliTicketAPI()
            ok, uname, _ = api.verify_auth()
            if ok:
                logged_in = True
                login_name = uname
            else:
                logged_in = False
        except Exception:
            logged_in = False

    def _prompt(msg, default=""):
        val = input(f"  {msg}" + (f" [{default}]" if default else "") + ": ").strip()
        return val if val else default

    def _ensure_api():
        nonlocal api, logged_in, login_name
        if not api or not api.is_authenticated():
            cfg = load_config()
            if cfg.get("cookie"):
                api = BiliTicketAPI()
                ok, uname, _ = api.verify_auth()
                if ok:
                    logged_in = True
                    login_name = uname
                    return True
            return False
        return True

    while True:
        print(f"\n{' B站会员购抢票工具 ':━^60}")
        print(f"  登录: {'✓ ' + (login_name if logged_in else '') if logged_in else '✗ 未登录'}")
        print(f"  购票人: {len(load_buyers())} 人")
        proxy = cfg.get("proxy", {})
        if proxy.get("enabled"):
            print(f"  代理: {proxy.get('http', '已启用')}")
        notify = cfg.get("notification", {})
        if notify.get("enabled"):
            print(f"  通知: {'TG/飞书' if notify.get('tg_token') and notify.get('feishu_webhook') else 'TG' if notify.get('tg_token') else '飞书'}")
        print(f"{'─' * 60}")
        print(f"  [1] 扫码登录        [2] 验证登录状态")
        print(f"  [3] 管理购票人      [4] 从B站获取实名观演人")
        print(f"  [5] 查看项目详情    [6] 查看可购票档")
        print(f"  [7] 监控票档        [8] 抢票 (dry-run / 真实)")
        print(f"  [9] 接口诊断        [R] API逆向工程")
        print(f"  [A] 代理/通知配置    [0] 退出")
        print(f"{'─' * 60}")

        choice = _prompt("选择").strip().upper()

        try:
            if choice == "1":
                cmd_login(None)

            elif choice == "2":
                if _ensure_api():
                    print(f"  已登录: {login_name}")
                    print(f"  CSRF: {'已获取' if api.csrf else '缺失'}")
                else:
                    print("  未登录, 请先执行 [1] 扫码登录")

            elif choice == "3":
                while True:
                    buyers = load_buyers()
                    print(f"\n  {' 购票人管理 ':━^40}")
                    print(f"  [1] 添加    [2] 列表 ({len(buyers)}人)    [3] 删除    [0] 返回")
                    sub = _prompt("选择")
                    if sub == "1":
                        print(f"  输入购票人信息:")
                        name = _prompt("姓名")
                        if not name:
                            continue
                        id_card = _prompt("身份证号")
                        if not id_card:
                            continue
                        phone = _prompt("手机号 (可选)")
                        buyers = load_buyers()
                        buyers.append({"name": name, "id_card": id_card, "phone": phone})
                        save_buyers(buyers)
                        print(f"  ✓ 已保存: {name}")
                    elif sub == "2":
                        buyers = load_buyers()
                        if not buyers:
                            print("  (空)")
                        for i, b in enumerate(buyers, 1):
                            ic = b.get("id_card", "")
                            m = f"{ic[:3]}****{ic[-3:]}" if len(ic) > 6 else "***"
                            print(f"  [{i}] {b['name']}  {m}  {b.get('phone', '')}")
                    elif sub == "3":
                        buyers = load_buyers()
                        if buyers:
                            idx = _prompt("序号")
                            try:
                                idx = int(idx) - 1
                                if 0 <= idx < len(buyers):
                                    r = buyers.pop(idx)
                                    save_buyers(buyers)
                                    print(f"  ✓ 已删除: {r['name']}")
                            except ValueError:
                                pass
                    elif sub == "0":
                        break

            elif choice == "4":
                if not _ensure_api():
                    print("  请先登录")
                    continue
                bl = api.get_buyers_list()
                if bl:
                    print(f"\n  B站实名观演人 ({len(bl)}):")
                    for b in bl:
                        print(f"  [{b['id']}] {b['name']}  {b.get('tel','')[:3]}****")
                    print(f"\n  如需导入为本地购票人, 请手动执行 buyer add")
                else:
                    print("  未获取到实名观演人 (可能未绑定)")

            elif choice == "5":
                pid = _prompt("项目ID (或完整URL)")
                if "show.bilibili.com" in pid:
                    import re
                    m = re.search(r'id=(\d+)', pid)
                    pid = m.group(1) if m else pid
                try:
                    pid = int(pid)
                except ValueError:
                    print("  无效的项目ID")
                    continue
                cmd_info(type("A", (), {"project_id": pid})())
                last_project = pid

            elif choice == "6":
                if not last_project:
                    pid = _prompt("项目ID")
                    try:
                        pid = int(pid)
                        last_project = pid
                    except ValueError:
                        continue
                else:
                    pid = _prompt("项目ID", str(last_project))
                    try:
                        pid = int(pid)
                        last_project = pid
                    except ValueError:
                        pid = last_project

                cmd_check(type("A", (), {
                    "project_id": pid, "sku_id": 0, "screen_id": 0,
                    "min_price": 0, "max_price": 99999999
                })())

            elif choice == "7":
                if not last_project:
                    pid = int(_prompt("项目ID") or "0")
                    if not pid:
                        continue
                    last_project = pid
                else:
                    pid = int(_prompt("项目ID", str(last_project)) or str(last_project))
                    last_project = pid
                interval = float(_prompt("轮询间隔(秒)", "0.5") or "0.5")
                cmd_monitor(type("A", (), {
                    "project_id": pid, "sku_id": 0, "screen_id": 0,
                    "min_price": 0, "max_price": 99999999,
                    "interval": interval,
                })())

            elif choice == "8":
                if not _ensure_api():
                    print("  请先登录")
                    continue
                if not last_project:
                    pid = int(_prompt("项目ID") or "0")
                    if not pid:
                        continue
                    last_project = pid
                else:
                    pid = int(_prompt("项目ID", str(last_project)) or str(last_project))
                    last_project = pid

                api2 = BiliTicketAPI()
                status, avail = api2.check_ticket_available(pid)
                if not status:
                    print(f"  无法获取项目信息")
                    continue

                print(f"\n  项目: {status.get('name')}")
                proj_sale = status.get("sale_flag_number", -1)
                print(f"  状态: {status.get('sale_flag')} (code={proj_sale})")

                if avail:
                    print(f"\n  可购票档:")
                    for i, t in enumerate(avail, 1):
                        print(f"  [{i}] {t['screen_name'][:20]} {t['desc']} ¥{t['price_yuan']}")
                    idx = _prompt("选择票档", "1")
                    try:
                        idx = int(idx) - 1
                        target = avail[idx]
                    except (ValueError, IndexError):
                        print("  无效选择")
                        continue
                else:
                    sku_id = int(_prompt("票档ID") or "0")
                    sid = int(_prompt("场次ID") or "0")
                    if not sku_id:
                        continue
                    target = {"sku_id": sku_id, "screen_id": sid, "screen_name": "",
                               "desc": "", "price_yuan": 0, "price": 0}

                num = int(_prompt("购买数量", "1") or "1")

                buyers = load_buyers()
                buyer_name = ""
                buyer_phone = ""
                if buyers:
                    print(f"\n  购票人:")
                    for i, b in enumerate(buyers, 1):
                        p = f" {b.get('phone', '')[:3]}****" if b.get("phone") else " 无手机号"
                        print(f"  [{i}] {b['name']}{p}")
                    print(f"  [0] 手动输入")
                    b_idx = _prompt("选择", "1")
                    try:
                        b_idx = int(b_idx)
                        if b_idx > 0:
                            b = buyers[b_idx - 1]
                            buyer_name = b["name"]
                            buyer_phone = b.get("phone", "")
                        else:
                            buyer_name = _prompt("姓名")
                            buyer_phone = _prompt("手机号")
                    except (ValueError, IndexError):
                        buyer_name = _prompt("姓名")
                        buyer_phone = _prompt("手机号")

                if not buyer_phone:
                    buyer_phone = _prompt("手机号 (项目可能需要)")

                real = _prompt("真实下单? (y=下单, 其他=dry-run)", "n").lower() in ("y", "yes")
                sale_time = _prompt("开售时间 (如2026-06-10 18:00, 空=立即)", "")
                max_retry = int(_prompt("每token重试次数", "60") or "60")

                api2.sniper_buy(
                    project_id=pid,
                    sku_id=target["sku_id"],
                    screen_id=target["screen_id"],
                    buy_num=num,
                    buyer_name=buyer_name,
                    buyer_phone=buyer_phone,
                    pay_money=int(target.get("price", 0)),
                    dry_run=not real,
                    wait_sale=bool(sale_time),
                    sale_time_str=sale_time or "",
                    max_retry_per_token=max_retry,
                )

            elif choice == "9":
                pid = int(_prompt("项目ID") or "0")
                if not pid:
                    continue
                cmd_diagnose(type("A", (), {
                    "project_id": pid, "screen_id": 0, "sku_id": 0
                })())

            elif choice in ("R", "r"):
                import subprocess
                pid = int(_prompt("项目ID", str(last_project or 1001227)) or "1001227")
                print(f"\n  启动逆向工程 (python reverse.py {pid} --quick)...\n")
                subprocess.run([sys.executable, "reverse.py", str(pid), "--quick"])
                input("\n  按回车返回...")

            elif choice in ("A", "a"):
                while True:
                    print(f"\n  {' 配置 ':━^40}")
                    print(f"  [1] 设置/查看代理")
                    print(f"  [2] 设置 Telegram 通知")
                    print(f"  [3] 设置飞书通知")
                    print(f"  [4] 启用/禁用通知")
                    print(f"  [0] 返回")
                    sub = _prompt("选择")
                    cfg = load_config()
                    if sub == "1":
                        proxy = cfg.get("proxy", {})
                        print(f"  代理: {'启用' if proxy.get('enabled') else '禁用'}")
                        print(f"  HTTP: {proxy.get('http', '(未设)')}")
                        http = _prompt("新HTTP代理 (空=不变)")
                        if http:
                            proxy["http"] = http
                            proxy["https"] = _prompt("HTTPS代理 (空=同HTTP)") or http
                            proxy["enabled"] = True
                            cfg["proxy"] = proxy
                            save_config(cfg)
                            print(f"  ✓ 已设置")
                    elif sub == "2":
                        token = _prompt("Bot Token")
                        chat_id = _prompt("Chat ID")
                        if token and chat_id:
                            cfg["notification"]["tg_token"] = token
                            cfg["notification"]["tg_chat_id"] = chat_id
                            cfg["notification"]["enabled"] = True
                            save_config(cfg)
                            print(f"  ✓ Telegram 已配置")
                    elif sub == "3":
                        webhook = _prompt("Webhook URL")
                        if webhook:
                            cfg["notification"]["feishu_webhook"] = webhook
                            cfg["notification"]["enabled"] = True
                            save_config(cfg)
                            print(f"  ✓ 飞书已配置")
                    elif sub == "4":
                        cfg["notification"]["enabled"] = not cfg["notification"].get("enabled", False)
                        save_config(cfg)
                        print(f"  通知: {'启用' if cfg['notification']['enabled'] else '禁用'}")
                    elif sub == "0":
                        break

            elif choice == "0":
                print("  再见!")
                break

        except KeyboardInterrupt:
            print("\n")
            continue
        except Exception as e:
            print(f"  错误: {e}")
            continue


if __name__ == "__main__":
    main()
