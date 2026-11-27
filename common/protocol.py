# -*- coding: utf-8 -*-
"""应用层协议：一行一个 JSON 报文，以换行符分隔。"""
import json
import socket

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9527

# 棋盘与棋子
BOARD_SIZE = 15
EMPTY, BLACK, WHITE = 0, 1, 2
STONE_NAME = {BLACK: "黑方", WHITE: "白方"}

# 房间状态
ROOM_WAITING = "waiting"
ROOM_PLAYING = "playing"
ROOM_FINISHED = "finished"

# 断线后保留席位的宽限时间（秒）
GRACE_SECONDS = 60

# 客户端心跳间隔（秒）
HEARTBEAT_INTERVAL = 15


class ProtocolError(Exception):
    """报文格式错误。"""


def encode_message(obj):
    """对象 -> 一行 JSON 字节串。"""
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def decode_message(line):
    """一行 JSON 字节串 -> 字典。"""
    try:
        obj = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("报文解析失败: %s" % exc) from exc
    if not isinstance(obj, dict):
        raise ProtocolError("报文必须是 JSON 对象")
    return obj


def send_message(conn, obj):
    """发送一条报文；同一连接上的发送由调用方保证串行。"""
    conn.sendall(encode_message(obj))


def set_keepalive(conn):
    """开启 TCP Keep-Alive。"""
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)