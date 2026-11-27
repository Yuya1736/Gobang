# -*- coding: utf-8 -*-
"""客户端网络层：同步请求 + 事件队列 + 心跳。"""
import itertools
import queue
import socket
import threading

from common.protocol import (
    HEARTBEAT_INTERVAL, ProtocolError,
    decode_message, set_keepalive, send_message,
)


class NetworkError(Exception):
    """网络或业务错误。"""


class NetClient:
    def __init__(self):
        self.sock = None
        self.reader = None
        self.events = queue.Queue()        # 服务器推送事件 + 断线通知
        self._pending = {}                 # 请求 id -> 等待队列
        self._pending_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._closed = False
        self._alive = False
        self._heartbeat_stop = threading.Event()

    # ---------- 连接生命周期 ----------

    def connect(self, host, port, timeout=5):
        """建立连接并启动接收线程与心跳线程。"""
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise NetworkError("无法连接服务器 %s:%s（%s）" % (host, port, exc)) from exc
        sock.settimeout(60)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        set_keepalive(sock)
        self.sock = sock
        self.reader = sock.makefile("rb")
        self._alive = True
        threading.Thread(target=self._recv_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        return self

    def _recv_loop(self):
        """读一行报文：带 id 的响应交给等待线程，事件放入队列。"""
        reason = "服务器关闭了连接"
        try:
            while not self._closed:
                line = self.reader.readline()
                if not line:
                    break
                try:
                    msg = decode_message(line)
                except ProtocolError:
                    continue
                if msg.get("type") == "resp":
                    with self._pending_lock:
                        waiter = self._pending.pop(msg.get("id"), None)
                    if waiter is not None:
                        waiter.put(msg)
                else:
                    self.events.put(msg)
        except (OSError, ValueError) as exc:
            reason = "网络异常: %s" % exc
        self._fail(reason)

    def _fail(self, reason):
        """连接失效：唤醒所有等待请求并通知界面进入重连流程。"""
        if self._closed:
            return
        self._alive = False
        self._heartbeat_stop.set()
        with self._pending_lock:
            waiters = list(self._pending.values())
            self._pending.clear()
        for waiter in waiters:
            waiter.put(NetworkError(reason))
        self.events.put({"type": "__disconnected__", "reason": reason})
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass

    def close(self):
        """主动关闭连接，不触发重连。"""
        self._closed = True
        self._alive = False
        self._heartbeat_stop.set()
        sock = self.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    @property
    def alive(self):
        return self._alive and not self._closed

    # ---------- 请求 ----------

    def request(self, msg_type, data=None, timeout=10):
        """发送请求并阻塞等待响应，失败抛出 NetworkError。"""
        if not self.alive:
            raise NetworkError("尚未连接服务器")
        req_id = next(self._ids)
        waiter = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[req_id] = waiter
        msg = {"type": msg_type, "id": req_id, "data": data or {}}
        try:
            with self._send_lock:
                send_message(self.sock, msg)
        except OSError as exc:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            self._fail("发送失败: %s" % exc)
            raise NetworkError("发送失败: %s" % exc) from exc

        try:
            result = waiter.get(timeout=timeout)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            raise NetworkError("请求超时（%s）" % msg_type) from None
        if isinstance(result, Exception):
            raise result
        if not result.get("ok"):
            raise NetworkError(result.get("error") or "操作失败")
        return result.get("data") or {}

    # ---------- 心跳 ----------

    def _heartbeat_loop(self):
        while not self._heartbeat_stop.wait(HEARTBEAT_INTERVAL):
            try:
                self.request("ping", timeout=8)
            except NetworkError:
                self._fail("心跳超时")
                return