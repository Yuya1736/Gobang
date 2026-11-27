# -*- coding: utf-8 -*-
"""应用层协议定义与 TCP 报文编解码。

本系统自定义的应用层协议采用「单行 JSON」的报文格式：
    <JSON 对象>\\n
每个 TCP 连接上的报文以换行符分隔，一行对应一条完整报文。
请求报文携带自增 id 用于响应关联，服务器推送的事件报文不带 id。
"""
import json
import socket

# 默认监听地址：课程设计要求全部使用回环地址运行
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9527

# 单条报文最大长度（防止恶意超长报文占用内存）
MAX_LINE_BYTES = 1024 * 1024

# 协议版本号，客户端与服务器握手时用于兼容性判断
PROTOCOL_VERSION = "1.0"

# 棋盘规格
BOARD_SIZE = 15
EMPTY, BLACK, WHITE = 0, 1, 2
STONE_NAME = {BLACK: "黑方", WHITE: "白方"}

# 房间状态
ROOM_WAITING = "waiting"      # 等待玩家 / 等待准备
ROOM_PLAYING = "playing"      # 对局进行中
ROOM_FINISHED = "finished"    # 本局结束，等待再来一局

# 断线宽限时间（秒）：掉线后保留席位与对局，超时判负
GRACE_SECONDS = 60

# 客户端心跳间隔（秒）
HEARTBEAT_INTERVAL = 15


class ProtocolError(Exception):
    """报文格式错误。"""


def encode_message(obj):
    """把 Python 对象编码为一行 JSON 字节串（含换行分隔符）。"""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    data = raw.encode("utf-8")
    if len(data) > MAX_LINE_BYTES:
        raise ProtocolError("报文超过最大长度限制")
    return data + b"\n"


def decode_message(line):
    """把一行 JSON 字节串解码为 Python 对象。"""
    try:
        text = line.decode("utf-8").strip()
        if not text:
            raise ProtocolError("空报文")
        obj = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("报文解析失败: %s" % exc) from exc
    if not isinstance(obj, dict):
        raise ProtocolError("报文必须是 JSON 对象")
    return obj


def send_message(conn, obj):
    """发送一条报文。

    调用方需保证同一连接上的发送串行化（服务器用会话锁，客户端用发送锁），
    避免多线程并发 sendall 造成字节流交错。
    """
    conn.sendall(encode_message(obj))


def set_keepalive(conn):
    """开启 TCP Keep-Alive，配合应用层心跳检测死连接。"""
    try:
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        pass