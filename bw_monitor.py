#!/usr/bin/env python3
"""BW2026 游园票监控 + 自动抢"""
import time, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bili_api import BiliTicketAPI

api = BiliTicketAPI(app_mode=True)

TICKETS = [
    ("周五游园", 893317, 1009926, 12800),
    ("周六游园", 893377, 1009927, 12800),
    ("周日游园", 893210, 1009928, 12800),
    ("周五VIP",  893239, 1009926, 58800),
    ("周六VIP",  893334, 1009927, 58800),
]

FLAG_MAP = {2:"预售中", 3:"已停售", 4:"已售罄", 5:"不可售", 102:"已结束"}
G = "\033[92m"; R = "\033[91m"; X = "\033[0m"

while True:
    try:
        d = api.get_project_summary(1001653)
        if not d:
            print(f"[{time.strftime('%H:%M:%S')}] 获取失败(限流), 稍后重试...")
            time.sleep(15)
            continue
        for name, sku, sid, price in TICKETS:
            for sc in d.get("screen_list", []):
                for tk in sc.get("ticket_list", []):
                    if tk["id"] == sku:
                        f = tk.get("sale_flag_number")
                        n = tk.get("num", 0)
                        ts = time.strftime("%H:%M:%S")
                        if f == 2 and n > 0:
                            print(f"{G}[{ts}] {name}: 可购! num={n}{X}")
                            api.sniper_buy(1001653, sku, sid, 1,
                                buyer_name="邓恺璐", buyer_phone="13542527698",
                                buyer_id_card="440923199909090616",
                                pay_money=price, dry_run=False, id_bind=2,
                                max_retry_per_token=200)
                            print(f"{G}[{ts}] {name}: 下单流程结束{X}")
                        else:
                            flag = FLAG_MAP.get(f, "?")
                            print(f"[{ts}] {name}: {flag} num={n}")
    except Exception as e:
        print(f"{R}[错误] {e}{X}")
    time.sleep(5)
