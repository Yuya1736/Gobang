# -*- coding: utf-8 -*-
"""会话对象：登录时签发，跨 TCP 连接存活，用于断线重连。"""
import threading
import time

from common.protocol import send_message


class Session:
    def __init__(self, token, user, conn):
        self.token = token
        self.user = user
        self.username = user["username"]
        self.conn = conn
        self.room_id = None
        self.offline_since = None     # 掉线时间；在线时为 None
        self.closed = False
        self.send_lock = threading.Lock()
        self.chat_times = []          # 聊天限速用的时间戳

    @property
    def online(self):
        return self.conn is not None and not self.closed

    def send(self, message):
        """线程安全地发送一条报文，连接不可用返回 False。"""
        conn = self.conn
        if conn is None:
            return False
        try:
            with self.send_lock:
                send_message(conn, message)
            return True
        except OSError:
            return False

    def bind(self, conn):
        """断线重连：绑定新连接并恢复在线状态。"""
        old, self.conn = self.conn, conn
        self.offline_since = None
        if old is not None and old is not conn:
            try:
                old.close()
            except OSError:
                pass

    def mark_offline(self):
        """标记掉线，保留席位等待重连。"""
        self.conn = None
        if self.offline_since is None:
            self.offline_since = time.time()

    def close(self):
        """彻底关闭会话。"""
        self.closed = True
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass