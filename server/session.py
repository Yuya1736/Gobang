# -*- coding: utf-8 -*-
"""服务器端会话对象。

一个会话由登录时签发的 token 标识，跨多次 TCP 连接存活：
客户端断线后可用同一 token 重新连接（断线重连），
服务器在宽限期内为其保留房间席位与对局状态。

会话状态机
    connected=True  -> 在线，conn 指向当前 TCP 连接；
    mark_offline()  -> 掉线，conn 置 None，启动宽限期定时器；
    rebind(new)     -> 重连成功，取消定时器并绑定新连接；
    close()         -> 会话终结（登出/过期），不可再恢复。
    closed 是终态标记，保证销毁流程幂等。
"""
import threading
import time
from collections import deque

from common.protocol import send_message


class Session:
    """一个在线用户的服务端会话。"""

    def __init__(self, token, user, conn):
        # token 是跨连接的身份凭据；用户名只用于索引与显示
        self.token = token
        self.user = user                 # 用户公开信息字典
        self.username = user["username"]
        self.conn = conn
        self.connected = True
        self.last_seen = time.time()
        self.room_id = None              # 当前所在房间号（None 表示在大厅）
        self.grace_timer = None          # 掉线判负定时器
        self.disconnected_at = None
        self.closed = False              # 会话已被销毁（登出/过期）
        self.send_lock = threading.Lock()
        self.chat_times = deque(maxlen=20)   # 聊天限速用的时间戳窗口

    def refresh_user(self, user):
        """对局结算后同步会话内缓存的最新积分信息。"""
        self.user = user

    def send(self, message):
        """线程安全地发送一条报文，失败返回 False。

        多个处理线程可能同时向同一用户推送（房间广播、聊天、事件），
        用 send_lock 保证一条报文的多个 sendall 不会交错。
        """
        if not self.connected or self.conn is None:
            return False
        try:
            with self.send_lock:
                send_message(self.conn, message)
            return True
        except OSError:
            # 写失败说明连接已断，交由读取线程的断开处理逻辑统一收尾
            return False

    def rebind(self, conn):
        """断线重连成功：绑定新连接并恢复在线状态。

        先取消宽限期定时器（否则到点会把刚恢复的会话判负），
        再关闭旧连接；旧连接的关闭处理不会误伤新会话（凭 conn 判断）。
        """
        old = self.conn
        self.conn = conn
        self.connected = True
        self.disconnected_at = None
        self.last_seen = time.time()
        if self.grace_timer is not None:
            self.grace_timer.cancel()
            self.grace_timer = None
        if old is not None and old is not conn:
            try:
                old.close()
            except OSError:
                pass

    def mark_offline(self):
        """标记掉线，保留会话与房间席位等待重连。

        不关闭房间席位、不删除 token：这两件事由宽限期到期后的
        _grace_expired / _destroy_session 负责。
        """
        self.connected = False
        self.disconnected_at = time.time()
        self.conn = None

    def close(self):
        """彻底关闭会话（登出或过期）。

        取消可能仍在计时的宽限期定时器，防止其在会话销毁后再次触发判负。
        """
        self.closed = True
        self.connected = False
        if self.grace_timer is not None:
            self.grace_timer.cancel()
            self.grace_timer = None
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass