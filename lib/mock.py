"""模拟发布端：本地 HTTP 服务，可回放各类响应。

为什么要有它：客户明确写了「样例使用脱敏素材和模拟发布端，不向真实账号自动发布」。
所以这份交付里**根本不存在**真实发布代码路径 —— 不是"默认关掉"，是没有。

★ 这个 mock 会做的四件事，全都是为了让"失败恢复"可演示：
  1) 按 scenario 回放：success / transient / permanent / ratelimit / timeout
  2) 记录每一次被调用的幂等键 —— 这样能验证"重试没有重复发"
  3) 支持"第一次失败第二次成功"，即真实世界里最常见的瞬时故障
  4) 幂等键相同且已成功过，返回 duplicate 而不是再发一次

端口默认 0（让内核分配），避免和本机其它服务撞车；测试不需要固定端口。
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockState:
    def __init__(self, scenario="success"):
        self.scenario = scenario
        self.calls = []          # 每次调用：{idempotency_key, at, result}
        self.succeeded = {}      # idempotency_key -> 第一次成功时间
        self.lock = threading.Lock()
        self.call_index = 0

    def decide(self, key):
        """返回 (http_status, body, should_sleep)。"""
        with self.lock:
            self.call_index += 1
            n = self.call_index
            if key in self.succeeded:
                return 200, {"ok": True, "duplicate": True,
                             "post_id": self.succeeded[key]["post_id"]}, 0
            sc = self.scenario
            if sc == "success":
                return 200, {"ok": True, "post_id": "post_%d" % n}, 0
            if sc == "transient":
                if n == 1:
                    return 503, {"error": "service unavailable (simulated)"}, 0
                return 200, {"ok": True, "post_id": "post_%d" % n}, 0
            if sc == "permanent":
                return 400, {"error": "invalid payload (simulated, non-retryable)"}, 0
            if sc == "ratelimit":
                if n == 1:
                    return 429, {"error": "rate limited", "retry_after": 1}, 0
                return 200, {"ok": True, "post_id": "post_%d" % n}, 0
            if sc == "timeout":
                return 0, {"error": "simulated timeout"}, 2.0
            return 500, {"error": "unknown scenario %r" % sc}, 0

    def record(self, key, result):
        with self.lock:
            self.calls.append({"idempotency_key": key, "at": time.time(),
                               "result": result})

    def mark_success(self, key, post_id):
        with self.lock:
            self.succeeded.setdefault(key, {"post_id": post_id, "at": time.time()})

    def snapshot(self):
        with self.lock:
            return {"scenario": self.scenario, "calls": list(self.calls),
                    "succeeded": dict(self.succeeded)}


def make_handler(state):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, obj):
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                state.record("", {"http": 400, "why": "bad json"})
                self._send(400, {"error": "malformed JSON"})
                return
            key = body.get("idempotency_key") or ""
            if not key:
                # 没有幂等键的发布请求：拒绝。真实平台也应当如此，
                # 否则重试就无法与"新的一条"区分。
                state.record("", {"http": 422, "why": "missing idempotency_key"})
                self._send(422, {"error": "idempotency_key required"})
                return
            code, payload, sleep = state.decide(key)
            if sleep:
                time.sleep(sleep)
                state.record(key, {"http": 0, "why": "timeout"})
                try:
                    self._send(504, {"error": "gateway timeout (simulated)"})
                except Exception:
                    pass
                return
            if code == 200 and payload.get("ok"):
                state.mark_success(key, payload.get("post_id"))
            state.record(key, {"http": code, "body": payload})
            self._send(code, payload)

        def log_message(self, *a):
            pass

    return H


def start(scenario="success", port=0):
    """启动 mock，返回 (server, thread, state, base_url)。用完调用 server.shutdown()。"""
    state = MockState(scenario)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    return srv, th, state, base
