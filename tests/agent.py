# -*- coding: utf-8 -*-
"""测试用无界面客户端：持续消费事件流，便于断言最新房间状态。"""
import threading
import time

from client.net import NetClient, NetworkError


class Agent:
    def __init__(self, host, port, username=None, password="test1234",
                 register=True, token=None):
        self.username = username
        self.net = NetClient().connect(host, port)
        self.states = []            # [(seq, snapshot)]
        self.events = []            # [(seq, event dict)]
        self.seq = 0
        self.cv = threading.Condition()
        self.token = None
        self.user = None
        if token:
            res = self.net.request("resume", {"token": token})
            self.token = res["token"]
            self.user = res["user"]
            self.username = self.user["username"]
            if res.get("snapshot"):
                self._record_state(res["snapshot"])
        else:
            if register:
                try:
                    self.net.request("register", {"username": username, "password": password})
                except NetworkError:
                    pass
            res = self.net.request("login", {"username": username, "password": password})
            self.token = res["token"]
            self.user = res["user"]
        self._stop = False
        self._drain_thread = threading.Thread(target=self._drain, daemon=True)
        self._drain_thread.start()

    # ---------- 事件收集 ----------

    def _next_seq(self):
        with self.cv:
            self.seq += 1
            return self.seq

    def _record_state(self, snapshot):
        seq = self._next_seq()
        with self.cv:
            self.states.append((seq, snapshot))
            self.cv.notify_all()
        return seq

    def _drain(self):
        while not self._stop:
            try:
                ev = self.net.events.get(timeout=0.2)
            except Exception:  # queue.Empty
                continue
            seq = self._next_seq()
            with self.cv:
                if ev.get("type") == "__disconnected__":
                    self.events.append((seq, ev))
                elif ev.get("event") == "room_state":
                    self.states.append((seq, ev.get("data") or {}))
                else:
                    self.events.append((seq, ev))
                self.cv.notify_all()

    def request(self, msg_type, data=None, timeout=10):
        return self.net.request(msg_type, data, timeout)

    def mark(self):
        """返回当前事件序号，用于只等待之后产生的事件。"""
        with self.cv:
            return self.seq

    def latest_state(self):
        with self.cv:
            return self.states[-1][1] if self.states else None

    def wait_state(self, predicate=None, timeout=8, since=0):
        """等待序号 > since 且满足 predicate 的房间快照。"""
        predicate = predicate or (lambda s: True)
        deadline = time.time() + timeout
        while True:
            with self.cv:
                for seq, state in reversed(self.states):
                    if seq > since and predicate(state):
                        return state
                remaining = deadline - time.time()
                if remaining <= 0:
                    last = self.states[-1][1] if self.states else None
                    raise AssertionError(
                        "等待房间状态超时（since=%s），最新状态: %r" % (since, last))
                self.cv.wait(min(remaining, 0.2))

    def wait_event(self, name, timeout=8, since=0):
        deadline = time.time() + timeout
        while True:
            with self.cv:
                for seq, event in reversed(self.events):
                    if seq > since and event.get("event") == name:
                        return event.get("data") or {}
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise AssertionError("等待事件超时: %s" % name)
                self.cv.wait(min(remaining, 0.15))

    def disconnect(self):
        """模拟网络异常：直接关闭 socket（不发送登出）。"""
        self._stop = True
        self.net.close()

    def close(self):
        self._stop = True
        try:
            self.net.close()
        except Exception:
            pass