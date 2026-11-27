# -*- coding: utf-8 -*-
"""五子棋对战服务器：连接管理、请求分发、房间与对局维护。

并发模型：主线程 accept，每个连接一个处理线程；服务器锁保护
rooms / sessions / online / conn_sessions 四张表，房间锁保护房间内部状态。
锁顺序固定为「先服务器锁，后房间锁」。

服务器是唯一裁判：客户端只上报落子坐标，合法性、轮次、胜负与积分
全部由服务器判定；每次状态变化推送完整房间快照，客户端整屏重绘。
"""
import argparse
import os
import re
import secrets
import socket
import threading
import time
import traceback

from common.protocol import (
    BLACK, BOARD_SIZE, DEFAULT_HOST, DEFAULT_PORT, GRACE_SECONDS,
    ROOM_PLAYING, ProtocolError, decode_message, send_message, set_keepalive,
)
from server.database import Database, DuplicateUser
from server.game import GomokuGame, MoveError
from server.room import MAX_PLAYERS, Room
from server.session import Session

USERNAME_RE = re.compile(r"^[\w一-龥]{2,16}$")
CHAT_INTERVAL = 2.0      # 聊天限速窗口（秒）
CHAT_LIMIT = 6           # 窗口内最多消息数
IDLE_TIMEOUT = 150       # 连接空闲超时（秒）


class ServerError(Exception):
    """可直接返回给用户的业务错误。"""


def _text(data, key, min_len=1, max_len=200):
    value = data.get(key)
    if not isinstance(value, str):
        raise ServerError("参数 %s 必须是字符串" % key)
    value = value.strip()
    if not min_len <= len(value) <= max_len:
        raise ServerError("参数 %s 长度应为 %d~%d" % (key, min_len, max_len))
    return value


def _int(data, key, minimum=None, maximum=None):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ServerError("参数 %s 必须是整数" % key)
    if minimum is not None and value < minimum:
        raise ServerError("参数 %s 过小" % key)
    if maximum is not None and value > maximum:
        raise ServerError("参数 %s 过大" % key)
    return value


class GameServer:
    # 无需登录即可调用的请求
    AUTH_FREE = {"register", "login", "resume", "ping"}

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, db_path="data/gomoku.json"):
        self.host = host
        self.port = port
        self.db = Database(db_path)
        self.rooms = {}                 # room_id -> Room
        self.sessions = {}              # token -> Session
        self.online = {}                # username -> Session
        self.conn_sessions = {}         # socket -> Session
        self.lock = threading.RLock()
        self.listener = None
        self.running = False
        self.handlers = {
            "register": self.handle_register,
            "login": self.handle_login,
            "resume": self.handle_resume,
            "logout": self.handle_logout,
            "rooms": self.handle_room_list,
            "room.create": self.handle_room_create,
            "room.join": self.handle_room_join,
            "room.leave": self.handle_room_leave,
            "room.ready": self.handle_room_ready,
            "room.chat": self.handle_room_chat,
            "game.move": self.handle_game_move,
            "game.resign": self.handle_game_resign,
            "ranking": self.handle_ranking,
            "ping": self.handle_ping,
        }

    # ==================== 网络层 ====================

    def start(self):
        """开始监听并阻塞运行，直到 stop() 被调用。"""
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((self.host, self.port))
        self.listener.listen(128)
        self.listener.settimeout(1.0)
        self.running = True
        print("[服务器] 已启动，监听 %s:%d" % (self.host, self.port))
        threading.Thread(target=self._maintenance, daemon=True).start()
        while self.running:
            try:
                conn, addr = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(IDLE_TIMEOUT)
            set_keepalive(conn)
            threading.Thread(target=self._client_loop, args=(conn, addr),
                             daemon=True).start()
        print("[服务器] 已停止")

    def stop(self):
        self.running = False
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass
        with self.lock:
            sessions = list(self.sessions.values())
        for sess in sessions:
            sess.close()

    def _client_loop(self, conn, addr):
        """单个连接的读取循环：按行解析报文并分发。"""
        peer = "%s:%s" % addr
        print("[服务器] 客户端接入 %s" % peer)
        reader = conn.makefile("rb")
        try:
            while self.running:
                line = reader.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                try:
                    msg = decode_message(line)
                except ProtocolError as exc:
                    self._raw_send(conn, {"type": "resp", "id": None,
                                          "ok": False, "error": str(exc)})
                    continue
                self._dispatch(conn, msg)
        except OSError:
            pass
        finally:
            try:
                reader.close()
            except OSError:
                pass
            self._on_connection_closed(conn)
            print("[服务器] 客户端断开 %s" % peer)

    def _dispatch(self, conn, msg):
        """解析请求 → 调用处理器 → 回送响应。"""
        req_id = msg.get("id")
        msg_type = msg.get("type")
        data = msg.get("data")
        if not isinstance(data, dict):
            data = {}
        try:
            if not isinstance(msg_type, str):
                raise ServerError("报文缺少 type 字段")
            handler = self.handlers.get(msg_type)
            if handler is None:
                raise ServerError("未知请求类型: %s" % msg_type)
            session = self.conn_sessions.get(conn)
            if msg_type not in self.AUTH_FREE and session is None:
                raise ServerError("请先登录")
            result = handler(session, data, conn) or {}
            self._reply(conn, req_id, True, result)
        except (ServerError, MoveError) as exc:
            self._reply(conn, req_id, False, None, str(exc))
        except Exception:
            traceback.print_exc()
            self._reply(conn, req_id, False, None, "服务器内部错误")

    def _reply(self, conn, req_id, ok, data=None, error=None):
        resp = {"type": "resp", "id": req_id, "ok": ok, "data": data, "error": error}
        session = self.conn_sessions.get(conn)
        if session is not None and session.conn is conn:
            session.send(resp)
        else:
            self._raw_send(conn, resp)

    @staticmethod
    def _raw_send(conn, obj):
        try:
            send_message(conn, obj)
        except OSError:
            pass

    # ==================== 认证 ====================

    def handle_register(self, session, data, conn):
        username = _text(data, "username", 2, 16)
        password = _text(data, "password", 4, 32)
        if not USERNAME_RE.match(username):
            raise ServerError("用户名只能包含中英文、数字和下划线（2~16 位）")
        try:
            user = self.db.create_user(username, password)
        except DuplicateUser as exc:
            raise ServerError(str(exc)) from exc
        return {"user": user}

    def handle_login(self, session, data, conn):
        if session is not None:
            raise ServerError("当前连接已登录")
        username = _text(data, "username", 2, 16)
        password = _text(data, "password", 4, 32)
        user = self.db.verify_user(username, password)
        if user is None:
            raise ServerError("用户名或密码错误")

        with self.lock:
            old = self.online.get(username)
            if old is not None and old.online:
                raise ServerError("该账号已在其他客户端登录")
            if old is not None:
                self._destroy_session(old, "账号重新登录")
            token = secrets.token_hex(16)
            sess = Session(token, user, conn)
            self.sessions[token] = sess
            self.online[username] = sess
            self.conn_sessions[conn] = sess
        print("[服务器] 用户登录 %s" % username)
        return {"token": token, "user": user}

    def handle_resume(self, session, data, conn):
        """断线重连：凭 token 恢复会话并返回所在房间的快照。"""
        token = _text(data, "token", 8, 64)
        with self.lock:
            sess = self.sessions.get(token)
            if sess is None or sess.closed:
                raise ServerError("会话已过期，请重新登录")
            if sess.online and sess.conn is not conn:
                raise ServerError("该会话已在线")
            if sess.conn is not None:
                self.conn_sessions.pop(sess.conn, None)
            sess.bind(conn)
            self.conn_sessions[conn] = sess
            user = self.db.get_user(sess.username)
            if user is not None:
                sess.user = user
            room = self.rooms.get(sess.room_id) if sess.room_id else None

        snapshot = None
        if room is not None:
            with room.lock:
                slot = room.player(sess.username)
                if slot is not None:
                    slot.connected = True
                snapshot = room.snapshot(sess.username)
            self.push_room(room)
        print("[服务器] 用户重连 %s" % sess.username)
        return {"token": token, "user": sess.user, "snapshot": snapshot}

    def handle_logout(self, session, data, conn):
        self._destroy_session(session, "主动退出登录")
        return {}

    # ==================== 房间 ====================

    def handle_room_list(self, session, data, conn):
        return {"rooms": self._room_list()}

    def handle_room_create(self, session, data, conn):
        name = _text(data, "name", 1, 20)
        with self.lock:
            if session.room_id is not None:
                raise ServerError("你已在房间中，请先离开")
            room = Room(name, session.username)
            self.rooms[room.id] = room
        with room.lock:
            user = self.db.get_user(session.username)
            room.add_player(session.username, user["score"] if user else 1000)
        session.room_id = room.id
        self.push_room(room)
        return {"room_id": room.id}

    def handle_room_join(self, session, data, conn):
        """以玩家或观战者身份加入房间，响应中附带首份完整快照。"""
        room_id = _int(data, "room_id", 1)
        role = data.get("as", "player")
        if role not in ("player", "spectator"):
            raise ServerError("未知的加入方式")
        with self.lock:
            room = self.rooms.get(room_id)
            if room is None:
                raise ServerError("房间不存在")
            if session.room_id is not None:
                raise ServerError("请先离开当前房间")
            with room.lock:
                if room.player(session.username) or room.is_spectator(session.username):
                    raise ServerError("你已在该房间中")
                if role == "player":
                    if room.status == ROOM_PLAYING:
                        raise ServerError("对局已开始，请选择观战")
                    if len(room.players) >= MAX_PLAYERS:
                        raise ServerError("房间已满，请选择观战")
                    user = self.db.get_user(session.username)
                    room.add_player(session.username, user["score"] if user else 1000)
                else:
                    room.spectators.append(session.username)
                session.room_id = room_id
                snapshot = room.snapshot(session.username)
        self.push_room(room)
        return {"room_id": room_id, "snapshot": snapshot}

    def handle_room_leave(self, session, data, conn):
        """离开房间；对局进行中需先认输。"""
        room = self._current_room(session)
        if room is None:
            raise ServerError("你不在任何房间中")
        with room.lock:
            slot = room.player(session.username)
            if slot is not None and room.game is not None and not room.game.finished:
                raise ServerError("对局进行中，请先认输或直接退出客户端（判负）")
            room.remove_member(session.username)
            empty = not room.players and not room.spectators
            remaining = room.members()
        session.room_id = None
        if empty:
            self._delete_room(room, "房间已解散")
        else:
            self.push_room(room)
            for name in remaining:
                sess = self.online.get(name)
                if sess is not None and sess.online:
                    sess.send({"type": "event", "event": "room_notice",
                               "data": {"room_id": room.id,
                                        "text": "%s 离开了房间" % session.username}})
        return {}

    def handle_room_ready(self, session, data, conn):
        """切换准备状态；两名玩家都准备好时立即开局。"""
        ready = bool(data.get("ready", True))
        room = self._current_room(session)
        if room is None:
            raise ServerError("你不在任何房间中")
        with room.lock:
            slot = room.player(session.username)
            if slot is None:
                raise ServerError("观战者不能准备")
            if room.status == ROOM_PLAYING:
                raise ServerError("对局进行中")
            slot.ready = ready
            started = self._can_start(room)
            if started:
                self._start_game(room)
            snapshot = room.snapshot(session.username)
        self.push_room(room)
        if started:
            self._room_event(room, "game_start",
                             {"game_serial": room.game.serial, "room_id": room.id})
        return {"snapshot": snapshot}

    def handle_room_chat(self, session, data, conn):
        text = _text(data, "text", 1, 200)
        room = self._current_room(session)
        if room is None:
            raise ServerError("你不在任何房间中")
        now = time.time()
        session.chat_times = [t for t in session.chat_times
                              if now - t <= CHAT_INTERVAL]
        if len(session.chat_times) >= CHAT_LIMIT:
            raise ServerError("发言过于频繁，请稍后再试")
        session.chat_times.append(now)
        with room.lock:
            room.add_chat(session.username, text)
            message = room.chats[-1]
            members = room.members()
        payload = {"room_id": room.id, "message": message}
        for name in members:
            sess = self.online.get(name)
            if sess is not None and sess.online:
                sess.send({"type": "event", "event": "chat", "data": payload})
        return {"time": message["time"]}

    # ==================== 对局 ====================

    def handle_game_move(self, session, data, conn):
        row = _int(data, "row", 0, BOARD_SIZE - 1)
        col = _int(data, "col", 0, BOARD_SIZE - 1)
        room = self._current_room(session)
        if room is None:
            raise ServerError("你不在任何房间中")
        with room.lock:
            slot = room.player(session.username)
            if slot is None:
                raise ServerError("观战者不能落子")
            if room.game is None or room.game.finished:
                raise ServerError("当前没有进行中的对局")
            room.game.apply_move(slot.seat, row, col)
            over = self._settle_if_needed(room)
            snapshot = room.snapshot(session.username)
        self.push_room(room)
        if over:
            self._room_event(room, "game_over", over)
        return {"snapshot": snapshot}

    def handle_game_resign(self, session, data, conn):
        room = self._current_room(session)
        if room is None:
            raise ServerError("你不在任何房间中")
        with room.lock:
            slot = room.player(session.username)
            if slot is None:
                raise ServerError("观战者不能认输")
            if room.game is None or room.game.finished:
                raise ServerError("当前没有进行中的对局")
            room.game.forfeit(slot.seat, "对手认输")
            over = self._settle_if_needed(room)
            snapshot = room.snapshot(session.username)
        self.push_room(room)
        if over:
            self._room_event(room, "game_over", over)
        return {"snapshot": snapshot}

    def handle_ranking(self, session, data, conn):
        limit = _int(data, "limit", 1, 100) if "limit" in data else 20
        return {"ranking": self.db.leaderboard(limit),
                "matches": self.db.recent_matches(10)}

    @staticmethod
    def handle_ping(session, data, conn):
        return {}

    # ==================== 房间内部逻辑 ====================

    def _current_room(self, session):
        if session is None or session.room_id is None:
            return None
        with self.lock:
            return self.rooms.get(session.room_id)

    def _room_list(self):
        with self.lock:
            rooms = list(self.rooms.values())
        infos = [room.public_info() for room in rooms]
        infos.sort(key=lambda r: r["id"])
        return infos

    def _can_start(self, room):
        """调用方需持有 room.lock。"""
        return (len(room.players) == MAX_PLAYERS
                and all(slot.ready for slot in room.players)
                and room.status != ROOM_PLAYING)

    def _start_game(self, room):
        """调用方需持有 room.lock。奇数局 0 号席位执黑，偶数局 1 号执黑。"""
        room.game_serial += 1
        black_seat = 0 if room.game_serial % 2 == 1 else 1
        room.game = GomokuGame(black_seat=black_seat, serial=room.game_serial)
        for slot in room.players:
            slot.ready = False

    def _settle_if_needed(self, room):
        """调用方需持有 room.lock：对局结束后结算积分，返回 game_over 数据。"""
        game = room.game
        if game is None or not game.finished or game.settled:
            return None
        game.settled = True
        black = next((s for s in room.players if s.seat == game.black_seat), None)
        white = next((s for s in room.players if s.seat != game.black_seat), None)
        if black is None or white is None:
            return {"room_id": room.id, "game_serial": game.serial,
                    "winner_stone": game.winner, "winner_name": "",
                    "reason": game.reason, "scores": {}, "move_count": len(game.moves)}
        scores = self.db.settle_game(black.username, white.username, game.winner,
                                     room.name, len(game.moves), game.reason)
        users = {}
        for slot in (black, white):
            if slot.username in scores:
                slot.score = scores[slot.username]["score"]
                user = self.db.get_user(slot.username)
                if user is not None:
                    users[slot.username] = user
                    sess = self.online.get(slot.username)
                    if sess is not None:
                        sess.user = user
        if game.winner == 3:
            winner_name = "平局"
        else:
            winner_name = (black if game.winner == BLACK else white).username
        return {
            "room_id": room.id,
            "game_serial": game.serial,
            "winner_stone": game.winner,
            "winner_name": winner_name,
            "reason": game.reason,
            "scores": scores,
            "users": users,
            "move_count": len(game.moves),
        }

    def _delete_room(self, room, reason):
        with self.lock:
            self.rooms.pop(room.id, None)
        self._room_event(room, "room_closed", {"room_id": room.id, "reason": reason})
        self.broadcast_lobby()

    def push_room(self, room):
        """向房间内每个在线成员推送完整快照（不含聊天历史）。"""
        with room.lock:
            room.revision += 1
            snapshots = {name: room.snapshot(name, include_chat=False)
                         for name in room.members()}
        for name, snap in snapshots.items():
            sess = self.online.get(name)
            if sess is not None and sess.online:
                sess.send({"type": "event", "event": "room_state", "data": snap})
        self.broadcast_lobby()

    def _room_event(self, room, event, data):
        """向房间内所有在线成员广播一条事件。"""
        with room.lock:
            members = room.members()
        for name in members:
            sess = self.online.get(name)
            if sess is not None and sess.online:
                sess.send({"type": "event", "event": event, "data": data})

    def broadcast_lobby(self):
        """通知所有在线客户端刷新大厅房间列表。"""
        rooms = self._room_list()
        with self.lock:
            sessions = list(self.online.values())
        payload = {"type": "event", "event": "rooms_changed", "data": {"rooms": rooms}}
        for sess in sessions:
            if sess.online:
                sess.send(payload)

    # ==================== 断线与会话回收 ====================

    def _on_connection_closed(self, conn):
        """TCP 断开：标记离线并保留席位，等待客户端在宽限期内重连。"""
        with self.lock:
            session = self.conn_sessions.pop(conn, None)
            if session is None or session.conn is not conn:
                return
            session.mark_offline()
        room = self._current_room(session)
        if room is not None:
            with room.lock:
                slot = room.player(session.username)
                if slot is not None:
                    slot.connected = False
            self.push_room(room)
        print("[服务器] 用户掉线，等待重连: %s（%d 秒）" % (session.username, GRACE_SECONDS))

    def _maintenance(self):
        """巡检线程：宽限期已过仍未重连的会话，判负并清理。"""
        while self.running:
            time.sleep(5)
            now = time.time()
            with self.lock:
                expired = [s for s in self.sessions.values()
                           if s.offline_since is not None
                           and now - s.offline_since > GRACE_SECONDS]
            for sess in expired:
                self._destroy_session(sess, "掉线超时")

    def _destroy_session(self, sess, reason):
        """销毁会话：退出房间、必要时判负结算，再从会话表清理。"""
        if sess is None:
            return
        with self.lock:
            if sess.closed:
                return
            sess.closed = True
            room = self.rooms.get(sess.room_id) if sess.room_id else None

        over = None
        if room is not None:
            with room.lock:
                slot = room.player(sess.username)
                if slot is not None and room.game is not None and not room.game.finished:
                    room.game.forfeit(slot.seat, "对手掉线")
                    over = self._settle_if_needed(room)
                room.remove_member(sess.username)
                empty = not room.players and not room.spectators
            sess.room_id = None
            if empty:
                self._delete_room(room, "房间已解散")
            else:
                self.push_room(room)
                if over:
                    self._room_event(room, "game_over", over)
                self._room_event(room, "room_notice",
                                 {"room_id": room.id,
                                  "text": "%s 已离开房间" % sess.username})
        if reason != "主动退出登录":
            sess.send({"type": "event", "event": "kicked",
                       "data": {"reason": reason}})
        with self.lock:
            self.sessions.pop(sess.token, None)
            if self.online.get(sess.username) is sess:
                self.online.pop(sess.username, None)
            if sess.conn is not None:
                self.conn_sessions.pop(sess.conn, None)
        sess.close()
        self.broadcast_lobby()


def main(argv=None):
    parser = argparse.ArgumentParser(description="网络对战游戏系统（五子棋）服务器")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 9527")
    parser.add_argument("--db", default="data/gomoku.json", help="JSON 数据文件路径")
    args = parser.parse_args(argv)

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = args.db if os.path.isabs(args.db) else os.path.join(base, args.db)
    server = GameServer(args.host, args.port, db_path)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
        print("\n[服务器] 收到中断信号，已退出")


if __name__ == "__main__":
    main()