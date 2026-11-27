# -*- coding: utf-8 -*-
"""网络对战游戏系统 —— 服务器主程序。

一、并发模型
    主线程只负责 accept，每个客户端连接派生一个处理线程
    （thread-per-connection），线程结束即回收，无需线程池。
    连接数受课程设计场景限制（数十个），该模型实现简单且隔离性好：
    某个连接阻塞在 readline 不会影响其他连接。

二、共享状态与锁
    服务器级锁 self.lock 保护三张索引表（房间表、会话表、在线表）与
    每个会话的 room_id；房间锁 room.lock 保护房间内的玩家、观战者、
    对局与聊天记录。
    锁顺序固定为：先服务器锁、后房间锁，任何路径都不反向获取，
    从而杜绝死锁。所有状态修改与快照生成都在锁内完成，
    保证任一瞬间导出的快照都是一致的。

三、服务器权威
    客户端只上报「我要在 (row, col) 落子」这类意图；
    合法性、轮次、胜负、积分全部由服务器判定。
    每次状态变化都下发完整房间快照，客户端整屏重绘，
    因此丢包、乱序、重复都不会让客户端进入错误状态。
    revision 单调递增，客户端据此丢弃迟到的旧快照。

四、断线重连
    登录签发的 token 是跨连接的身份凭据。TCP 断开后服务器不清除会话，
    而是保留房间席位 GRACE_SECONDS 秒；客户端用 token 调用 resume
    即可绑定新连接并拿到所在房间的完整快照。
    超过宽限期仍未重连，则对局判负、席位释放、会话销毁。

五、请求分发
    handlers 字典把请求类型映射到处理函数，新增功能只需注册一项。
    处理函数返回的字典自动作为响应 data；业务异常（ServerError /
    MoveError）转换为 ok=false 的响应，连接保持可用。

六、报文格式（换行分隔的 JSON）
    请求：{"type": "game.move", "id": 7, "data": {"row": 7, "col": 8}}
    响应：{"type": "resp", "id": 7, "ok": true, "data": {...}, "error": null}
    事件：{"type": "event", "event": "room_state", "data": {...}}
    id 由客户端自增，用于把响应与请求配对；event 无 id，由服务器主动推送。
"""
import argparse
import os
import re
import secrets
import socket
import threading
import time
import traceback
from collections import deque

from common.protocol import (
    BOARD_SIZE, DEFAULT_HOST, DEFAULT_PORT, GRACE_SECONDS, MAX_LINE_BYTES,
    ProtocolError, decode_message, send_message, set_keepalive,
)
from server.database import Database, DuplicateUser
from server.game import GomokuGame, MoveError
from server.room import Room
from server.session import Session

# 用户名：2~16 位中英文、数字或下划线
USERNAME_RE = re.compile(r"^[\w一-龥]{2,16}$")
CHAT_INTERVAL = 2.0      # 聊天限速窗口（秒）
CHAT_LIMIT = 6           # 窗口内最多消息数
IDLE_TIMEOUT = 150       # 连接空闲超时（秒），客户端心跳间隔 15 秒


class ServerError(Exception):
    """可直接反馈给用户的业务错误。

    与程序缺陷区分开：ServerError 的文本会原样出现在 ok=false 响应中，
    而其他异常只记录堆栈、对客户端统一回复「服务器内部错误」。
    """


def _text(data, key, min_len=1, max_len=200):
    """取出并校验字符串字段。"""
    value = data.get(key)
    if not isinstance(value, str):
        raise ServerError("参数 %s 必须是字符串" % key)
    value = value.strip()
    if not min_len <= len(value) <= max_len:
        raise ServerError("参数 %s 长度应为 %d~%d" % (key, min_len, max_len))
    return value


def _int(data, key, minimum=None, maximum=None):
    """取出并校验整数字段。bool 是 int 的子类，需要单独排除。"""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ServerError("参数 %s 必须是整数" % key)
    if minimum is not None and value < minimum:
        raise ServerError("参数 %s 过小" % key)
    if maximum is not None and value > maximum:
        raise ServerError("参数 %s 过大" % key)
    return value


class GameServer:
    """五子棋对战服务器。

    生命周期
        __init__ 构造数据库与索引表 -> start() 开始 accept ->
        stop() 停止监听并关闭会话。
    线程
        主线程 accept；每连接一个处理线程；另有 reaper 巡检线程。
    对外状态
        rooms / sessions / online / conn_sessions 四张表，全部由 self.lock 保护。
    """

    # 无需登录即可调用的请求：注册、登录、重连、心跳（其余请求要求会话）
    AUTH_FREE = {"register", "login", "resume", "ping"}

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, db_path="data/gomoku.json"):
        self.host = host
        self.port = port
        self.db = Database(db_path)     # JSON 文件持久化：账号、积分与对局记录
        # 三张会话索引表分别服务于不同查找方向：
        #   rooms          房间号 -> 房间对象
        #   sessions       token  -> 会话（断线重连时按 token 找回）
        #   online         用户名 -> 会话（事件推送、重复登录检测）
        #   conn_sessions  连接   -> 会话（把 TCP 连接上的请求归入对应用户）
        self.rooms = {}                 # room_id -> Room
        self.sessions = {}              # token -> Session
        self.online = {}                # username -> Session
        self.conn_sessions = {}         # socket -> Session（已登录连接）
        self.lock = threading.RLock()   # 服务器级锁：房间表/会话表
        self.listener = None
        self.running = False
        # 请求类型 -> 处理函数；新增功能只需注册到这里。
        # 每个处理函数签名统一为 (session, data, conn) -> dict，
        # 字典会作为响应 data 原样返回，无需额外封装。
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
        """启动监听并阻塞运行，直到 stop() 被调用。

        accept 循环本身很轻：只做「接受连接 → 设置参数 → 启动线程」。
        任何业务处理都在连接线程中进行，因此一个新连接不会拖慢其他连接。
        """
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR 便于调试时快速重启，避免端口处于 TIME_WAIT 无法绑定
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((self.host, self.port))
        self.listener.listen(128)
        # 设置 accept 超时，使主循环能周期性检查 running 标志，正常退出
        self.listener.settimeout(1.0)
        self.running = True
        print("[服务器] 已启动，监听 %s:%d" % (self.host, self.port))
        # 后台巡检线程：回收超时会话、清理长时间无心跳的死连接
        threading.Thread(target=self._reaper, name="reaper", daemon=True).start()
        while self.running:
            try:
                conn, addr = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            # 连接空闲超过 IDLE_TIMEOUT 视为失联，readline 会抛超时异常
            conn.settimeout(IDLE_TIMEOUT)
            set_keepalive(conn)
            # thread-per-connection：每个客户端独立线程，互不阻塞
            threading.Thread(target=self._client_loop, args=(conn, addr),
                             name="client-%s" % (addr[1],), daemon=True).start()
        print("[服务器] 已停止")

    def stop(self):
        """停止监听并关闭所有会话（进程退出/收到中断时调用）。"""
        self.running = False
        # 关闭监听套接字会让 accept 立即抛错，主循环随即退出
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass
        # 先在锁内取会话快照，再逐个关闭；关闭属于阻塞 I/O，不宜持锁进行
        with self.lock:
            sessions = list(self.sessions.values())
        for sess in sessions:
            sess.close()

    def _client_loop(self, conn, addr):
        """单个客户端连接的读取循环：按行读取 JSON 请求并分发。"""
        peer = "%s:%s" % addr
        print("[服务器] 客户端接入 %s" % peer)
        # makefile 缓冲区按行读取，天然处理 TCP 粘包/半包：
        # 一次 readline 一定对应一条以 \n 结尾的完整报文
        reader = conn.makefile("rb")
        try:
            while self.running:
                line = reader.readline(MAX_LINE_BYTES + 2)
                if not line:
                    break       # 对端关闭连接（EOF）
                if len(line) > MAX_LINE_BYTES:
                    # 超长报文直接断开，防止恶意客户端耗尽服务器内存
                    self._raw_send(conn, {"type": "resp", "id": None, "ok": False,
                                          "error": "报文超过长度限制"})
                    break
                try:
                    msg = decode_message(line)
                except ProtocolError as exc:
                    # 单条报文格式错误不影响连接，返回错误后继续读取下一条
                    self._raw_send(conn, {"type": "resp", "id": None, "ok": False,
                                          "error": str(exc)})
                    continue
                # 分发可能耗时（数据库、广播），但不持有任何锁进入，
                # 因此其他连接的处理线程仍可并发推进
                self._dispatch(conn, msg)
        except (OSError, ValueError):
            # 连接异常（对端关闭、读超时等），走统一的断开处理
            pass
        finally:
            # 无论正常关闭还是异常，都必须注销连接并启动重连宽限期
            try:
                reader.close()
            except OSError:
                pass
            self._on_connection_closed(conn)
            print("[服务器] 客户端断开 %s" % peer)

    def _dispatch(self, conn, msg):
        """解析请求 → 调用处理器 → 回送响应。

        所有业务异常都转换成 ok=false 的响应，连接本身保持可用；
        只有未知异常才打印堆栈，便于定位服务器缺陷。
        分发阶段不持有服务器锁，处理器内部按需要自行加锁。
        """
        req_id = msg.get("id")
        msg_type = msg.get("type")
        data = msg.get("data")
        # data 必须是对象；缺失或类型错误时按空字典处理，由处理器校验字段
        if not isinstance(data, dict):
            data = {}
        try:
            if not isinstance(msg_type, str):
                raise ServerError("报文缺少 type 字段")
            handler = self.handlers.get(msg_type)
            if handler is None:
                raise ServerError("未知请求类型: %s" % msg_type)
            session = self.conn_sessions.get(conn)
            # 注册/登录/重连/心跳允许在未登录连接上调用
            if msg_type not in self.AUTH_FREE:
                if session is None:
                    raise ServerError("请先登录")
            if session is not None:
                # 记录活跃时间，供巡检线程判断连接是否空闲超时
                session.last_seen = time.time()
            # 处理器返回的 dict 原样作为响应 data；None 统一视为空对象
            result = handler(session, data, conn) or {}
            self._reply(conn, req_id, True, result)
        except (ServerError, MoveError) as exc:
            # 业务错误：原样返回给客户端展示
            self._reply(conn, req_id, False, None, str(exc))
        except Exception:
            # 未预期异常：记录堆栈，但不把内部细节暴露给客户端
            traceback.print_exc()
            self._reply(conn, req_id, False, None, "服务器内部错误")

    def _reply(self, conn, req_id, ok, data=None, error=None):
        """发送响应。已登录连接统一走会话锁，保证与事件推送不交错。

        事件推送和响应可能来自不同线程；若同时直接写 socket，
        两条 JSON 可能交错导致客户端解析失败。session.send 内的
        send_lock 让它们串行化。
        """
        resp = {"type": "resp", "id": req_id, "ok": ok, "data": data, "error": error}
        session = self.conn_sessions.get(conn)
        if session is not None and session.conn is conn:
            session.send(resp)
        else:
            # 未登录连接（注册/登录失败）没有会话，直接发送
            self._raw_send(conn, resp)

    @staticmethod
    def _raw_send(conn, obj):
        """在尚未绑定会话的连接上直接发送（登录前、协议错误提示等）。"""
        try:
            send_message(conn, obj)
        except OSError:
            pass

    # ==================== 认证 ====================

    def handle_register(self, session, data, conn):
        """注册新账号；密码由数据库层加盐哈希后存储，明文不落库。"""
        username = _text(data, "username", 2, 16)
        password = _text(data, "password", 4, 32)
        # 用户名同时用作数据库主键与大厅显示名，需限制字符集避免
        # 换行符、空白等破坏界面排版
        if not USERNAME_RE.match(username):
            raise ServerError("用户名只能包含中英文、数字和下划线（2~16 位）")
        try:
            user = self.db.create_user(username, password)
        except DuplicateUser as exc:
            raise ServerError(str(exc)) from exc
        return {"user": user}

    def handle_login(self, session, data, conn):
        """校验账号密码，创建会话并下发 token（重连凭证）。"""
        if session is not None:
            raise ServerError("当前连接已登录")
        username = _text(data, "username", 2, 16)
        password = _text(data, "password", 4, 32)
        # 密码校验包含 12 万次 PBKDF2 迭代，放在服务器锁外执行，
        # 避免单个登录请求拖住其他连接的全部请求
        user = self.db.verify_user(username, password)
        if user is None:
            raise ServerError("用户名或密码错误")

        with self.lock:
            old = self.online.get(username)
            # 同一账号只允许一个在线会话，避免两个客户端争抢同一席位
            if old is not None and old.connected:
                raise ServerError("该账号已在其他客户端登录")
            if old is not None:
                # 旧会话处于掉线保留期，新登录直接接管
                self._destroy_session(old, "账号重新登录")
            # token 随机生成且不可预测；它是断线重连时唯一的身份凭证
            token = secrets.token_hex(16)
            new_session = Session(token, user, conn)
            self.sessions[token] = new_session
            self.online[username] = new_session
            self.conn_sessions[conn] = new_session
        print("[服务器] 用户登录 %s" % username)
        return {"token": token, "user": user}

    def handle_resume(self, session, data, conn):
        """断线重连：凭 token 恢复会话并返回所在房间的完整快照。"""
        token = _text(data, "token", 8, 64)
        # token 是断线期间唯一的身份凭证：宽限期内会话一直保留，
        # 无需再次提供密码即可恢复；过期则要求重新登录
        with self.lock:
            sess = self.sessions.get(token)
            if sess is None or sess.closed:
                raise ServerError("会话已过期，请重新登录")
            # 同一 token 只允许一个连接：正常重连时旧连接已断开
            if sess.connected and sess.conn is not conn:
                raise ServerError("该会话已在线")
            old_conn = sess.conn
            if old_conn is not None:
                self.conn_sessions.pop(old_conn, None)
            # rebind 会取消宽限期定时器并绑定新连接
            sess.rebind(conn)
            self.conn_sessions[conn] = sess
            # 宽限期内可能有其他对局结算，重新读取最新积分
            user = self.db.get_user(sess.username)
            if user is not None:
                sess.refresh_user(user)
            # 只记录房间引用，快照稍后在房间锁内生成，两把锁不会长时间同时持有
            room = self.rooms.get(sess.room_id) if sess.room_id else None

        # 房间锁与服务器锁分开获取，维持「先服务器、后房间」的固定顺序
        snapshot = None
        if room is not None:
            with room.lock:
                slot = room.player(sess.username)
                if slot is not None:
                    slot.connected = True
                snapshot = room.snapshot(sess.username)
            # 通知同桌玩家「对手已回来」
            self.push_room(room)
        print("[服务器] 用户重连 %s" % sess.username)
        return {"token": token, "user": sess.user, "snapshot": snapshot}

    def handle_logout(self, session, data, conn):
        """主动登出：销毁会话并从所在房间移除（对局中按认输处理）。"""
        self._destroy_session(session, "主动退出登录")
        return {}

    # ==================== 房间 ====================

    def handle_room_list(self, session, data, conn):
        """返回大厅房间列表（公开信息，不含棋盘与聊天）。"""
        with self.lock:
            rooms = [room.public_info() for room in self.rooms.values()]
        # 按房间号升序输出，客户端刷新大厅时列表顺序保持稳定
        rooms.sort(key=lambda r: r["id"])
        return {"rooms": rooms}

    def handle_room_create(self, session, data, conn):
        """创建房间：创建者自动占据 0 号玩家席位。"""
        name = _text(data, "name", 1, 20)
        with self.lock:
            if session.room_id is not None:
                raise ServerError("你已在房间中，请先离开")
            room = Room(name, session.username)
            # Room 在构造时分配自增 id 与独立的房间锁，登记后即可被加入
            self.rooms[room.id] = room
        with room.lock:
            # 取数据库中的最新积分，用于房间内展示与结算基准；
            # 若账号在会话期间被删除则回退到初始分
            user = self.db.get_user(session.username)
            room.add_player(session.username, user["score"] if user else 1000)
        # 先登记会话所在房间，再广播；客户端收到快照时 room_id 已可查
        session.room_id = room.id
        self.push_room(room)
        return {"room_id": room.id}

    def handle_room_join(self, session, data, conn):
        """以玩家或观战者身份加入房间，响应中附带加入后的首份完整快照。"""
        room_id = _int(data, "room_id", 1)
        role = data.get("as", "player")
        if role not in ("player", "spectator"):
            raise ServerError("未知的加入方式")
        with self.lock:
            room = self.rooms.get(room_id)
            if room is None:
                raise ServerError("房间不存在")
            if session.room_id == room_id:
                raise ServerError("你已在该房间中")
            if session.room_id is not None:
                raise ServerError("请先离开当前房间")
            # 加入判定与写入在同一把房间锁内完成，避免两个客户端同时抢最后一个席位
            with room.lock:
                if room.player(session.username) is not None or room.is_spectator(session.username):
                    raise ServerError("你已在该房间中")
                if role == "player":
                    # 对局开始后不再接受新玩家，只能观战
                    if room.status == "playing":
                        raise ServerError("对局已开始，请选择观战")
                    if len(room.players) >= room.max_players:
                        raise ServerError("房间已满，请选择观战")
                    user = self.db.get_user(session.username)
                    room.add_player(session.username, user["score"] if user else 1000)
                else:
                    if session.username in room.spectators:
                        raise ServerError("你已在观战")
                    room.spectators.append(session.username)
                session.room_id = room_id
                snapshot = room.snapshot(session.username)
        self.push_room(room)
        return {"room_id": room_id, "snapshot": snapshot}

    def handle_room_leave(self, session, data, conn):
        """离开房间；对局进行中不允许主动离开，避免绕过判负结算。

        玩家离席与观战者离席走同一入口，区别只在 remove_member 内部：
        玩家离席会触发席位重排，观战者只从列表移除。
        """
        room = self._current_room(session)
        if room is None:
            raise ServerError("你不在任何房间中")
        with room.lock:
            slot = room.player(session.username)
            # 对局进行中离席等价于弃赛，必须经认输或断线超时走正式结算，
            # 否则积分与胜负记录会出现缺口
            if slot is not None and room.game is not None and not room.game.finished:
                raise ServerError("对局进行中，请先认输或直接退出客户端（判负）")
            room.remove_member(session.username)
            empty = not room.players and not room.spectators
            remaining = [s.username for s in room.players] + list(room.spectators)
        # 会话与房间解除关联后，后续请求将不再把该用户视为房间成员
        session.room_id = None
        if empty:
            # 空房间立即回收，大厅列表随之刷新
            self._delete_room(room, "房间已解散")
        else:
            self.push_room(room)
            # 房间仍有人时逐条通知，便于客户端在聊天区显示离开提示
            for name in remaining:
                sess = self.online.get(name)
                if sess is not None and sess.connected:
                    sess.send({"type": "event", "event": "room_notice",
                               "data": {"room_id": room.id, "text": "%s 离开了房间" % session.username}})
        return {}

    def handle_room_ready(self, session, data, conn):
        """切换准备状态；两名玩家都已准备时立即开局。

        准备状态只对玩家有意义，观战者调用会被拒绝；
        对局中重复点击准备也返回错误，客户端同时会禁用按钮。
        """
        ready = bool(data.get("ready", True))
        room = self._current_room(session)
        if room is None:
            raise ServerError("你不在任何房间中")
        with room.lock:
            slot = room.player(session.username)
            if slot is None:
                raise ServerError("观战者不能准备")
            if room.status == "playing":
                raise ServerError("对局进行中")
            slot.ready = ready
            started = False
            # 开局判定与准备状态修改必须在同一把房间锁内完成，
            # 否则两个客户端同时准备时可能重复开局。
            # _start_game 会轮换先手并把双方 ready 清零。
            if self._can_start(room):
                self._start_game(room)
                started = True
            snapshot = room.snapshot(session.username)
        self.push_room(room)
        if started:
            # 开局事件携带局号，客户端可据此提示「第 N 局开始」
            self._room_event(room, "game_start", {"game_serial": room.game.serial,
                                                  "room_id": room.id})
        return {"snapshot": snapshot}

    def handle_room_chat(self, session, data, conn):
        """房间聊天：先做滑动窗口限速，再写入房间历史并广播给全体成员。"""
        text = _text(data, "text", 1, 200)
        room = self._current_room(session)
        if room is None:
            raise ServerError("你不在任何房间中")
        # 简易聊天限速，防止刷屏
        # chat_times 只保留最近 CHAT_INTERVAL 秒内的发送时间，形成滑动窗口；
        # 限速按会话（用户）而非房间计算，换房间不能绕过限制
        now = time.time()
        with self.lock:
            while session.chat_times and now - session.chat_times[0] > CHAT_INTERVAL:
                session.chat_times.popleft()
            if len(session.chat_times) >= CHAT_LIMIT:
                raise ServerError("发言过于频繁，请稍后再试")
            session.chat_times.append(now)
        with room.lock:
            room.add_chat(session.username, text)
            # 取回房间统一生成的消息对象（带服务器时间），保证各端显示一致；
            # deque 超过上限会自动丢弃最旧记录，内存占用恒定
            message = room.chats[-1]
            members = [s.username for s in room.players] + list(room.spectators)
        # 聊天走独立事件增量推送，不触发整份房间快照，减少重复报文
        payload = {"room_id": room.id, "message": message}
        for name in members:
            sess = self.online.get(name)
            if sess is not None and sess.connected:
                sess.send({"type": "event", "event": "chat", "data": payload})
        return {"time": message["time"]}

    # ==================== 对局 ====================

    def handle_game_move(self, session, data, conn):
        """落子：服务器校验轮次、坐标与胜负，客户端只负责上报坐标。

        整个「校验 + 落子 + 判胜 + 结算 + 快照」在同一把房间锁内完成，
        多个玩家并发落子时不会出现两人都通过轮次校验的情况。
        """
        # 坐标在协议层先做范围校验，越界直接拒绝，避免棋盘数组越界
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
            # apply_move 内部校验「是否轮到该席位」「该点是否已被占用」，
            # 违规时抛 MoveError，由 _dispatch 统一转成错误响应
            room.game.apply_move(slot.seat, row, col)
            over = self._settle_if_needed(room)
            snapshot = room.snapshot(session.username)
        # 先广播新状态，再发结算事件，客户端总能先拿到终局棋盘再弹结果；
        # 顺序反过来会出现「结算框已弹出但棋盘缺少最后一手」的观感问题
        self.push_room(room)
        if over:
            self._room_event(room, "game_over", over)
        return {"snapshot": snapshot}

    def handle_game_resign(self, session, data, conn):
        """认输：判该席位负，随即触发结算。"""
        room = self._current_room(session)
        if room is None:
            raise ServerError("你不在任何房间中")
        with room.lock:
            slot = room.player(session.username)
            if slot is None:
                raise ServerError("观战者不能认输")
            if room.game is None or room.game.finished:
                raise ServerError("当前没有进行中的对局")
            # resign 只设置胜负与原因，积分结算统一由 _settle_if_needed 完成，
            # 保证认输、五连、掉线三条路径的结算逻辑完全一致
            room.game.resign(slot.seat)
            over = self._settle_if_needed(room)
            snapshot = room.snapshot(session.username)
        self.push_room(room)
        if over:
            self._room_event(room, "game_over", over)
        return {"snapshot": snapshot}

    def handle_ranking(self, session, data, conn):
        """排行榜与最近对局记录。

        limit 可选，默认 20、上限 100；并发测试通过放大上限，
        确保所有参赛者都能出现在结果中，避免被前 20 名截断。
        """
        # 排行榜条数可调，最近对局固定 10 条，避免响应报文无限增长
        limit = _int(data, "limit", 1, 100) if "limit" in data else 20
        return {"ranking": self.db.leaderboard(limit),
                "matches": self.db.recent_matches(10)}

    @staticmethod
    def handle_ping(session, data, conn):
        """心跳：客户端每 HEARTBEAT_INTERVAL 秒调用一次，刷新 last_seen。

        心跳不承载业务，仅向巡检线程证明连接仍然存活；
        界面闲置时客户端也会保持同样的发送节奏。
        """
        return {"time": time.time()}

    # ==================== 房间状态与结算 ====================

    def _current_room(self, session):
        """按会话记录的 room_id 查房间；不在房间时返回 None。

        房间可能在会话仍记录 room_id 时被回收（如刚成为空房），
        因此这里返回 None 而不是假设一定存在，调用方统一按「不在房间」处理。
        """
        if session is None or session.room_id is None:
            return None
        with self.lock:
            return self.rooms.get(session.room_id)

    def _can_start(self, room):
        """调用方需持有 room.lock：两名玩家已就位且都已准备。

        finished 状态允许双方重新准备开始下一局，因此只排除 playing；
        playing 期间即使 ready 字段为真也不能再次开局。
        """
        return (len(room.players) == 2
                and all(slot.ready for slot in room.players)
                and room.status != "playing")

    def _start_game(self, room):
        """调用方需持有 room.lock：开局并轮换先手。

        先手轮换规则：第 1、3、5… 局 0 号席位执黑，第 2、4、6… 局 1 号执黑，
        保证同一房间连续对局时双方机会均等。颜色只由局号决定，与时间无关，
        多桌并发时各房间互不影响，连续对局的轮换关系也确定可复现。
        """
        room.game_serial += 1
        black_seat = 0 if room.game_serial % 2 == 1 else 1
        room.game = GomokuGame(black_seat=black_seat, serial=room.game_serial)
        # 开局后清零准备状态，下一局需要重新准备，避免立即自动连开
        for slot in room.players:
            slot.ready = False

    def _settle_if_needed(self, room):
        """调用方需持有 room.lock：对局结束后结算积分，返回 game_over 事件。

        settled_serial 保证同一局只结算一次：落子、认输、掉线判负都可能
        触发本函数，重复结算会造成积分被加两次。
        """
        game = room.game
        if game is None or not game.finished or room.settled_serial == game.serial:
            return None
        room.settled_serial = game.serial
        # 找到执黑/执白两个席位（席位与颜色每局可能轮换）
        black_slot = next((s for s in room.players if s.seat == game.black_seat), None)
        white_slot = next((s for s in room.players if s.seat != game.black_seat), None)
        if black_slot is None or white_slot is None:
            # 结算前有人离席（理论上不该发生）：只广播结果，不动积分
            return {"room_id": room.id, "game_serial": game.serial,
                    "winner_stone": game.winner, "winner_name": "",
                    "reason": game.reason, "scores": {}, "move_count": len(game.moves)}
        # 数据库层完成积分更新、胜负场次更新与对局记录落库（同一次原子写入）
        scores = self.db.settle_game(black_slot.username, white_slot.username,
                                     game.winner, room.name, len(game.moves), game.reason)
        users_info = {}
        if scores:
            # 把新积分写回房间席位，下一次快照即可显示最新分数
            if black_slot.username in scores:
                black_slot.score = scores[black_slot.username]["score"]
            if white_slot.username in scores:
                white_slot.score = scores[white_slot.username]["score"]
            # 同步在线会话缓存，logout 后再次登录也能读到最新战绩
            for name in scores:
                user = self.db.get_user(name)
                if user is not None:
                    users_info[name] = user
                    sess = self.online.get(name)
                    if sess is not None:
                        sess.refresh_user(user)
        if game.winner == 3:
            winner_name = "平局"
        else:
            winner_slot = black_slot if game.winner == 1 else white_slot
            winner_name = winner_slot.username
        return {
            "room_id": room.id,
            "game_serial": game.serial,
            "winner_stone": game.winner,
            "winner_name": winner_name,
            "reason": game.reason,
            "scores": scores,
            "users": users_info,
            "move_count": len(game.moves),
        }

    def _delete_room(self, room, reason):
        """删除空房间，并通知可能仍在房间内的成员。

        先从房间表移除，避免新请求再次加入正在销毁的房间；
        随后广播 room_closed（成员可能还有未刷新的旧界面）并刷新大厅。
        pop 使用默认值，保证对已删除房间重复调用也安全。
        """
        with self.lock:
            self.rooms.pop(room.id, None)
        self._room_event(room, "room_closed", {"room_id": room.id, "reason": reason})
        self.broadcast_lobby()

    def _room_members(self, room):
        """当前房间内全部成员的用户名（玩家在前、观战者在后）。"""
        with room.lock:
            return [s.username for s in room.players] + list(room.spectators)

    def push_room(self, room):
        """向房间内每个成员推送完整快照（不含聊天历史）。

        快照在房间锁内一次性生成，保证同一轮广播里
        玩家看到的棋盘与观战者看到的棋盘完全一致。
        网络发送移到锁外，慢客户端不会拖住其他线程。
        """
        with room.lock:
            # 每次广播递增版本号，保证客户端能识别并丢弃迟到的旧快照
            room.revision += 1
            members = [s.username for s in room.players] + list(room.spectators)
            snapshots = {name: room.snapshot(name, include_chat=False) for name in members}
        for name, snap in snapshots.items():
            sess = self.online.get(name)
            # 离线成员跳过：其快照由重连时的 resume 响应补发
            if sess is not None and sess.connected:
                sess.send({"type": "event", "event": "room_state", "data": snap})
        # 房间状态可能影响大厅列表（如「等待中」变「对战中」）
        self.broadcast_lobby()

    def _room_event(self, room, event, data):
        """向房间内所有在线成员广播一条业务事件（不刷新整份快照）。"""
        for name in self._room_members(room):
            sess = self.online.get(name)
            # 离线成员不推送：重连时通过快照恢复，事件本身不持久化
            if sess is not None and sess.connected:
                sess.send({"type": "event", "event": event, "data": data})

    def broadcast_lobby(self):
        """通知所有在线连接刷新大厅房间列表。

        每次房间增删、状态变化都会触发全量广播；房间数量很小（课程设计
        场景为个位数），全量推送比维护增量订阅更简单可靠。
        """
        with self.lock:
            rooms = [room.public_info() for room in self.rooms.values()]
            sessions = list(self.online.values())
        rooms.sort(key=lambda r: r["id"])
        payload = {"type": "event", "event": "rooms_changed", "data": {"rooms": rooms}}
        # 网络发送在锁外进行，避免某个慢客户端阻塞整个服务器
        for sess in sessions:
            if sess.connected:
                sess.send(payload)

    # ==================== 断线与会话回收 ====================

    def _on_connection_closed(self, conn):
        """TCP 连接断开：标记离线并启动宽限计时器，等待客户端重连。

        触发路径有三条：客户端正常关闭、读写异常、巡检线程关闭死连接；
        它们都会汇聚到这里，因此本函数必须可重入且幂等。
        """
        with self.lock:
            # 用 pop 而非 get：同一连接只处理一次，后到者拿不到会话直接返回
            session = self.conn_sessions.pop(conn, None)
            if session is None:
                return
            # 会话可能已在重连时改绑到新连接，此时旧连接关闭不应影响它
            if session.conn is not conn:
                return
            session.mark_offline()
        room = self._current_room_by_user(session)
        if room is not None:
            with room.lock:
                slot = room.player(session.username)
                if slot is not None:
                    # 只标记掉线、不移除席位，这样重连后可直接恢复原座位与棋子颜色
                    slot.connected = False
            # 让同桌玩家看到「对手掉线」提示
            self.push_room(room)
        # 宽限计时器到点后若仍未重连，由 _grace_expired 正式清理并判负
        timer = threading.Timer(GRACE_SECONDS, self._grace_expired, args=(session.token,))
        timer.daemon = True
        with self.lock:
            session.grace_timer = timer
        timer.start()
        # 宽限期长度需大于客户端重连间隔，又要足够短以便对手不必久等
        print("[服务器] 用户掉线，保留会话等待重连: %s（%d 秒）" % (session.username, GRACE_SECONDS))

    def _current_room_by_user(self, session):
        """按会话记录查其所在房间（服务器锁内读取，与 _current_room 等价）。"""
        if session.room_id is None:
            return None
        with self.lock:
            return self.rooms.get(session.room_id)

    def _grace_expired(self, token):
        """宽限期结束仍离线：退出房间；若对局未结束则判负。

        由 threading.Timer 在独立线程触发，因此所有共享状态访问都要加锁；
        先快速判断会话是否已被重连/销毁，避免无谓地进入房间处理。
        """
        with self.lock:
            sess = self.sessions.get(token)
            # 已重连或已销毁的会话无需处理（计时器可能在重连之后才触发）
            if sess is None or sess.closed or sess.connected:
                return
            room = self.rooms.get(sess.room_id) if sess.room_id else None
        over = None
        if room is not None:
            with room.lock:
                slot = room.player(sess.username)
                if slot is not None and room.game is not None and not room.game.finished:
                    # 掉线方视为弃权，对手获胜；再进行一次正式积分结算
                    room.game.forfeit(slot.seat)
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
                # 通知留房成员：掉线者已超时离开
                self._room_event(room, "room_notice", {
                    "room_id": room.id,
                    "text": "%s 掉线超时，已离开房间" % sess.username,
                })
        self._destroy_session(sess, "掉线超时")
        print("[服务器] 会话过期: %s" % sess.username)

    def _destroy_session(self, sess, reason):
        """销毁会话：先从房间移除，再从会话表清理并关闭连接。

        登出、被踢、宽限期到期、巡检清理都会走这里，因此必须幂等：
        第一步在服务器锁内检查并设置 closed 标记，后到者直接返回。
        """
        if sess is None:
            return
        with self.lock:
            if sess.closed:
                return
            # 先置标记，避免登出与过期定时器同时销毁同一会话
            sess.closed = True
        room = None
        with self.lock:
            room = self.rooms.get(sess.room_id) if sess.room_id else None
        over = None
        if room is not None:
            with room.lock:
                slot = room.player(sess.username)
                # 对局未结束就销毁会话，等价于弃权：先判负并结算，再释放席位
                if slot is not None and room.game is not None and not room.game.finished:
                    room.game.forfeit(slot.seat)
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
        # 主动登出无需通知；被动踢下线时先告知原因，客户端可据此停止自动重连
        if reason != "主动退出登录":
            sess.send({"type": "event", "event": "kicked", "data": {"reason": reason}})
        # 三张索引表同步清理，防止 token / 用户名残留导致重连到已销毁会话
        with self.lock:
            self.sessions.pop(sess.token, None)
            if self.online.get(sess.username) is sess:
                self.online.pop(sess.username, None)
            if sess.conn is not None:
                self.conn_sessions.pop(sess.conn, None)
        sess.close()
        self.broadcast_lobby()

    def _reaper(self):
        """后台巡检：关闭长时间无心跳的连接，清理超时会话。"""
        while self.running:
            # 10 秒巡检一次：约为 15 秒心跳间隔的 2/3，
            # 半开连接最迟在一个心跳周期左右就能被发现
            time.sleep(10)
            now = time.time()
            # 先在锁内快照出待处理对象，避免在持有服务器锁时做网络 I/O
            with self.lock:
                stale_conns = []
                for sess in list(self.online.values()):
                    # 半开连接（对端已掉线但未收到 FIN）只能靠心跳判断
                    if sess.connected and sess.conn is not None and now - sess.last_seen > IDLE_TIMEOUT:
                        stale_conns.append(sess.conn)
                # 兜底清理：宽限计时器理论上会处理，这里防止计时器丢失导致会话永久残留
                expired = [s for s in self.sessions.values()
                           if not s.connected and s.disconnected_at
                           and now - s.disconnected_at > GRACE_SECONDS + 5]
            # 关闭死连接会触发其读取线程的断开处理，进而进入宽限期
            for conn in stale_conns:
                try:
                    conn.close()
                except OSError:
                    pass
            # 已过期的会话直接销毁；_destroy_session 内部幂等，重复调用安全
            for sess in expired:
                self._destroy_session(sess, "会话超时")


def main(argv=None):
    """命令行入口：解析参数、构造服务器并阻塞运行。"""
    parser = argparse.ArgumentParser(description="网络对战游戏系统（五子棋）服务器")
    # 默认只监听回环地址，满足课程设计「本机多进程模拟网络对战」的要求
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 9527")
    parser.add_argument("--db", default="data/gomoku.json", help="JSON 数据文件路径")
    args = parser.parse_args(argv)

    # 相对路径统一相对项目根目录，保证从任意工作目录启动都能找到数据库
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