# -*- coding: utf-8 -*-
"""客户端网络层：请求/响应 + 事件推送 + 心跳。

线程模型
    * 调用线程（GUI 的工作线程）：request() 阻塞等待响应；
    * 接收线程 net-reader：持续 readline，把报文分流到等待队列；
    * 心跳线程 heartbeat：每 HEARTBEAT_INTERVAL 秒发一次 ping。
    GUI 主线程不直接调用本类，而是通过 ui_queue 拿结果。

报文分流规则
    * 带 id 的 resp：取出 _pending 中对应的等待队列并投递，实现 RPC 关联；
    * 不带 id 的 event：原样放入 events 队列，由 Tk 主线程轮询处理；
    * 连接异常：_fail() 唤醒所有等待者并投递 __disconnected__ 伪事件。

TCP 流式处理
    socket.makefile("rb") 的 readline 以 \n 为界切分，
    天然解决粘包与半包；MAX_LINE_BYTES 同时限制单条报文长度。
"""
import queue
import socket
import threading
import time

from common.protocol import (
    HEARTBEAT_INTERVAL, MAX_LINE_BYTES, ProtocolError,
    decode_message, set_keepalive, send_message,
)


class NetworkError(Exception):
    """网络或业务错误。"""


class NetClient:
    def __init__(self):
        self.sock = None
        self.reader = None
        # events 由接收线程写、GUI 主线程读，标准库 Queue 自带锁
        self.events = queue.Queue()        # 服务器推送事件 + 断线通知
        self._pending = {}                 # 请求 id -> 等待队列
        # 为 pending 注册/注销、请求 id 自增、报文发送分别准备独立的锁，
        # 避免锁粒度过大导致接收线程与发送线程互相等待
        self._pending_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self._next_id = 1
        self._closed = False
        self._heartbeat_stop = threading.Event()
        self._recv_thread = None
        self._alive = False

    # ---------- 连接生命周期 ----------

    def connect(self, host, port, timeout=5):
        """建立 TCP 连接并启动接收线程与心跳线程。

        TCP_NODELAY 关闭 Nagle 算法，落子等小报文可立即发出，
        降低请求-响应往返延迟；SO_KEEPALIVE 由 set_keepalive 统一开启。
        """
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise NetworkError("无法连接服务器 %s:%s（%s）" % (host, port, exc)) from exc
        # 连接建立后放宽读超时：由应用层心跳判断存活，而不是靠阻塞读超时
        sock.settimeout(60)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        set_keepalive(sock)
        self.sock = sock
        # makefile("rb") + readline 按 \n 切分，天然处理 TCP 粘包/半包
        self.reader = sock.makefile("rb")
        self._alive = True
        self._recv_thread = threading.Thread(target=self._recv_loop,
                                             name="net-reader", daemon=True)
        self._recv_thread.start()
        threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True).start()
        return self

    def _recv_loop(self):
        """接收线程：读一行 → 解析 → 按报文类型分流。

        带 id 的 resp 唤醒对应等待队列（RPC 关联），
        其余 event 原样放入事件队列，交给 Tk 主线程处理。
        """
        reason = "服务器关闭了连接"
        try:
            while not self._closed:
                line = self.reader.readline(MAX_LINE_BYTES + 2)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    reason = "收到超长报文"
                    break
                try:
                    msg = decode_message(line)
                except ProtocolError:
                    continue
                if msg.get("type") == "resp":
                    self._deliver_response(msg)
                else:
                    self.events.put(msg)
        except (OSError, ValueError) as exc:
            reason = "网络异常: %s" % exc
        self._fail(reason)

    def _deliver_response(self, msg):
        """按 id 找到发起请求的线程并投递响应；找不到说明请求已超时丢弃。"""
        with self._pending_lock:
            waiter = self._pending.pop(msg.get("id"), None)
        if waiter is not None:
            waiter.put(msg)

    def _fail(self, reason):
        """连接失效：唤醒所有等待中的请求，并向界面发出断线事件。

        可能由接收线程（EOF/异常）或心跳线程（ping 超时）触发，
        因此用 _closed 判断以区分「主动关闭」和「意外断开」：
        主动关闭时不应再弹重连。
        """
        if self._closed:
            return
        self._alive = False
        self._heartbeat_stop.set()
        # 每个等待者都收到同一个错误，避免调用线程永久阻塞
        with self._pending_lock:
            waiters = list(self._pending.values())
            self._pending.clear()
        for waiter in waiters:
            waiter.put(NetworkError(reason))
        # 界面线程据此进入 start_reconnect 流程
        self.events.put({"type": "__disconnected__", "reason": reason})
        try:
            if self.sock is not None:
                self.sock.close()
        except OSError:
            pass

    def close(self):
        """主动关闭（登出/切换连接时调用），不触发重连事件。

        先 shutdown 唤醒阻塞在 readline 的接收线程，再关闭套接字；
        不直接调用 reader.close()——BufferedReader 关闭需要等待读锁，
        在接收线程阻塞时会死锁。

        _closed=True 让接收线程随后的 _fail() 直接返回，
        所以主动关闭不会向界面投递 __disconnected__。
        """
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
        """连接可用：既未主动关闭，也未因读写异常而失效。"""
        return self._alive and not self._closed

    # ---------- 请求 ----------

    def request(self, msg_type, data=None, timeout=10):
        """发送请求并阻塞等待响应，失败抛出 NetworkError。

        每个请求分配自增 id 并注册一个仅容一条消息的等待队列；
        接收线程读到匹配 id 的响应后唤醒本线程，实现同步 RPC 语义。
        """
        if not self.alive:
            raise NetworkError("尚未连接服务器")
        with self._id_lock:
            req_id = self._next_id
            self._next_id += 1
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
        # _fail() 会把 NetworkError 放进等待队列，与正常响应统一在此处理
        if isinstance(result, Exception):
            raise result
        if not result.get("ok"):
            raise NetworkError(result.get("error") or "操作失败")
        return result.get("data") or {}

    # ---------- 心跳 ----------

    def _heartbeat_loop(self):
        """周期性 ping，及时感知半开连接（如网线拔出、进程挂起）。

        TCP 本身不会立即发现对端消失，应用层心跳配合服务器 last_seen
        是判断连接存活的主要手段；ping 超时即认为连接不可用。
        """
        while not self._heartbeat_stop.wait(HEARTBEAT_INTERVAL):
            try:
                self.request("ping", timeout=8)
            except NetworkError:
                # 心跳失败说明连接已不可用，交给断线重连流程处理
                self._fail("心跳超时")
                return