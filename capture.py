#!/usr/bin/env python3
"""
B站购票请求抓包代理

用法:
  python capture.py                       # 启动代理 (默认 127.0.0.1:8888)
  python capture.py --port 9999           # 指定端口

配置:
  浏览器设置 HTTP 代理 → 127.0.0.1:8888
  然后正常走一遍 B站购票流程 (选票 → 确认订单)
  代理自动捕获 createV2 / prepare 请求的 payload

输出:
  captured/ 目录下保存每个接口的完整 payload + headers

对比:
  python capture.py --diff                # 对比捕获的 payload 和我们的生成逻辑
"""

import json, os, sys, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import ssl

CAPTURE_DIR = os.path.join(os.path.dirname(__file__), "captured")


class CaptureHandler(BaseHTTPRequestHandler):
    """HTTP 代理处理器 — 只记录 B站 API 请求, 其余透传"""

    def do_CONNECT(self):
        """HTTPS 隧道 — 目标为 show.bilibili.com 时拦截"""
        host, port = self.path.split(":")
        port = int(port)

        if "bilibili.com" in host:
            self._intercept_connect(host, port)
        else:
            self._tunnel_connect(host, port)

    def _intercept_connect(self, host, port):
        """针对 bilibili.com 做 MITM"""
        self.send_response(200, "Connection Established")
        self.end_headers()

        try:
            # 包装 SSL
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ssl_sock = ctx.wrap_socket(self.connection, server_side=True)
            self.connection = ssl_sock
            self.rfile = self.connection.makefile("rb", self.rbufsize)
            self.wfile = self.connection.makefile("wb", self.wbufsize)

            # 读取客户端请求
            self.raw_requestline = self.rfile.readline(65537)
            self.parse_request()

            # 读取 body
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len else b""

            # 记录 API 请求
            self._record(self.command, self.path, dict(self.headers), body)

            # 转发到真实服务器
            import http.client
            conn = http.client.HTTPSConnection(host, port, timeout=30)
            req_headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in ("proxy-connection", "host")}
            conn.request(self.command, self.path, body=body, headers=req_headers)
            resp = conn.getresponse()

            # 返回响应
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() != "transfer-encoding":
                    self.send_header(k, v)
            self.end_headers()
            resp_body = resp.read()
            self.wfile.write(resp_body)
            conn.close()

        except Exception as e:
            print(f"  MITM 错误: {e}")

    def _tunnel_connect(self, host, port):
        """非 bilibili 域名直接透传"""
        self.send_response(200, "Connection Established")
        self.end_headers()

        import socket
        try:
            remote = socket.create_connection((host, port), timeout=30)
            self.connection.settimeout(30)

            def forward(src, dst, name):
                try:
                    while True:
                        data = src.recv(8192)
                        if not data:
                            break
                        dst.sendall(data)
                except Exception:
                    pass

            t1 = threading.Thread(target=forward, args=(self.connection, remote, "C→S"))
            t2 = threading.Thread(target=forward, args=(remote, self.connection, "S→C"))
            t1.daemon = t2.daemon = True
            t1.start()
            t2.start()
            t1.join(timeout=300)
            t2.join(timeout=300)
        except Exception:
            pass
        finally:
            try:
                remote.close()
            except Exception:
                pass

    def _record(self, method, path, headers, body):
        """记录 B站 API 请求"""
        # 只记录 ticket/order 相关接口
        if "/api/ticket/" not in path or "image" in path:
            return

        os.makedirs(CAPTURE_DIR, exist_ok=True)

        ts = time.strftime("%Y%m%d_%H%M%S")
        name = path.split("?")[0].replace("/", "_").strip("_")[:60]
        fname = os.path.join(CAPTURE_DIR, f"{ts}_{name}.json")

        record = {
            "timestamp": ts,
            "method": method,
            "path": path,
            "headers": {
                k: v for k, v in headers.items()
                if k.lower() in ("content-type", "referer", "origin",
                                 "user-agent", "x-risk-header", "cookie")
            },
            "body_raw": body.decode("utf-8", errors="replace"),
        }

        try:
            record["body_json"] = json.loads(record["body_raw"])
        except Exception:
            record["body_json"] = None

        with open(fname, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        # 简洁输出
        short_path = path[:60]
        body_preview = record["body_raw"][:120].replace("\n", " ")
        print(f"\n{'─' * 60}")
        print(f"  [{ts}] {method} {short_path}")
        print(f"  Body: {body_preview}...")
        print(f"  → 保存: {os.path.basename(fname)}")
        print(f"{'─' * 60}")

    def log_message(self, format, *args):
        pass  # 静默


def start_proxy(port=8888):
    print(f"\n{'═' * 60}")
    print(f"  B站购票抓包代理")
    print(f"  监听: 127.0.0.1:{port}")
    print(f"{'═' * 60}")
    print()
    print(f"  配置浏览器 HTTP 代理 → 127.0.0.1:{port}")
    print(f"  然后打开 https://show.bilibili.com 正常购票")
    print(f"  请求会自动保存到 captured/ 目录")
    print()
    print(f"  Chrome 代理设置: 系统偏好设置 → 网络 → 代理 → HTTP代理")
    print(f"  或使用 SwitchyOmega 插件")
    print()
    print(f"  按 Ctrl+C 停止\n")

    server = HTTPServer(("127.0.0.1", port), CaptureHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  代理已停止")
        server.shutdown()


def diff_captured():
    """对比捕获的 payload 和我们的生成逻辑"""
    if not os.path.exists(CAPTURE_DIR):
        print("captured/ 目录为空, 请先运行 python capture.py 抓包")
        return

    captured_files = sorted(os.listdir(CAPTURE_DIR))

    # 找 createV2 请求
    cv2_files = [f for f in captured_files if "createV2" in f]
    prepare_files = [f for f in captured_files if "prepare" in f and "graph" not in f]

    print(f"\n  捕获文件: {len(captured_files)} 个")
    print(f"  createV2: {len(cv2_files)} 个")
    print(f"  prepare: {len(prepare_files)} 个")

    if cv2_files:
        with open(os.path.join(CAPTURE_DIR, cv2_files[-1])) as f:
            captured = json.load(f)
        body = captured.get("body_json", {})
        headers = captured.get("headers", {})

        print(f"\n{'═' * 60}")
        print(f"  捕获的 createV2 Payload ({len(body)} 字段)")
        print(f"{'═' * 60}")

        # 列出所有字段
        for k, v in sorted(body.items()):
            val = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
            print(f"  {k:25s} = {val[:80]}")

        print(f"\n  捕获的 Headers:")
        for k, v in headers.items():
            print(f"  {k}: {v[:80]}")

    if prepare_files:
        with open(os.path.join(CAPTURE_DIR, prepare_files[-1])) as f:
            prep = json.load(f)
        body = prep.get("body_json", {})

        print(f"\n{'═' * 60}")
        print(f"  捕获的 prepare Payload ({len(body)} 字段)")
        print(f"{'═' * 60}")
        for k, v in sorted(body.items()):
            val = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
            print(f"  {k:25s} = {val[:80]}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="B站购票抓包代理")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--diff", action="store_true", help="对比捕获的 payload")
    args = parser.parse_args()

    if args.diff:
        diff_captured()
    else:
        start_proxy(args.port)
