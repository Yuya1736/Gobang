# -*- coding: utf-8 -*-
"""房间数据结构与房间状态快照。

房间是最小的对局组织单位：2 个玩家席位 + 若干观战者 + 对局对象 + 聊天记录。

席位规则
    seat 固定为 0/1，玩家离开后重新紧凑编号；棋子颜色（黑/白）不绑定席位，
    而是每局由服务器轮换决定，这样同一房间连打多局时先手公平。

并发约束
    所有对状态的修改都必须持有 room.lock，快照也在同一把锁内生成，
    保证任一瞬间导出的状态都是一致的（不会出现「玩家已更新但棋盘未更新」）。

快照策略
    快照是「以观察者为中心」的完整视图：同一房间的玩家与观战者拿到的
    棋盘相同，但 you 字段不同，客户端据此决定是否显示准备/认输按钮。
    聊天历史只在加入/重连时随快照下发，之后由独立的 chat 事件增量推送。
"""
import itertools
import threading
import time
from collections import deque

from common.protocol import BLACK, ROOM_FINISHED, ROOM_PLAYING, ROOM_WAITING, WHITE

MAX_PLAYERS = 2
MAX_CHAT_HISTORY = 100


class PlayerSlot:
    """玩家席位。seat 0/1 固定不变，棋子颜色每局轮换。

    使用 __slots__ 限制字段，既减少内存占用，也防止处理函数
    误给席位挂上临时属性。
    """

    __slots__ = ("username", "seat", "ready", "connected", "score")

    def __init__(self, username, seat, score=1000):
        self.username = username
        self.seat = seat
        self.ready = False
        self.connected = True
        self.score = score


class Room:
    """一个对局房间。所有公开方法都要求调用方遵守锁约定，见模块说明。"""

    # 进程内自增的房间号；单服务器场景无需持久化，重启后重新编号
    _id_counter = itertools.count(1)
    max_players = MAX_PLAYERS

    def __init__(self, name, owner):
        self.id = next(Room._id_counter)
        self.name = name
        self.owner = owner
        self.lock = threading.RLock()
        self.players = []                 # [PlayerSlot]，最多 2 个，保持 seat 递增
        self.spectators = []              # [username]，保持加入顺序
        self.chats = deque(maxlen=MAX_CHAT_HISTORY)
        self.game = None
        # 房间内第几局，用于轮换先手；settled_serial 记录已结算局号，
        # 使落子/认输/掉线判负多条路径不会重复给同一局加分
        self.game_serial = 0
        self.settled_serial = 0
        # 状态版本号：每次广播递增，客户端据此丢弃迟到的旧快照。
        # 典型场景：join 响应比随后到达的 room_state 事件更晚，若无版本号
        # 客户端会用旧快照覆盖新快照，表现为棋盘「回退」。
        self.revision = 0
        self.created_at = time.time()

    # ---------- 状态 ----------

    @property
    def status(self):
        """由对局对象推导房间状态：无对局=等待，对局结束=已结束，否则=对战中。"""
        if self.game is None:
            return ROOM_WAITING
        return ROOM_FINISHED if self.game.finished else ROOM_PLAYING

    def player(self, username):
        """按用户名查找玩家席位；观战者与游客返回 None。"""
        for slot in self.players:
            if slot.username == username:
                return slot
        return None

    def is_spectator(self, username):
        """观战者只读参与，不占用玩家席位，也不影响开局条件。"""
        return username in self.spectators

    def is_member(self, username):
        """是否为本房间成员（玩家或观战者）。"""
        return self.player(username) is not None or self.is_spectator(username)

    def add_player(self, username, score=1000):
        """加入玩家席位，返回分配的席位号；满员返回 None。

        席位优先补 0 号空位，保证两名玩家的 seat 始终是 0/1 且递增。
        """
        if len(self.players) >= MAX_PLAYERS:
            return None
        used = {slot.seat for slot in self.players}
        seat = 0 if 0 not in used else 1
        slot = PlayerSlot(username, seat, score)
        self.players.append(slot)
        self.players.sort(key=lambda s: s.seat)
        return seat

    def remove_member(self, username):
        """移除玩家或观战者；玩家被移除后重新紧凑编号席位。"""
        slot = self.player(username)
        if slot is not None:
            self.players.remove(slot)
            # 席位必须连续（0..n-1），否则后续 add_player 的补位逻辑会失效
            for i, s in enumerate(self.players):
                s.seat = i
            if self.game is not None:
                # 席位变化后旧对局无法继续，清空对局回到等待状态
                self.game = None
            # 房主离开时顺延给剩余的第一位玩家，房主不会落在一个不存在的人身上
            if self.owner == username:
                self.owner = self.players[0].username if self.players else ""
            return True
        if username in self.spectators:
            self.spectators.remove(username)
            return True
        return False

    def add_chat(self, username, text):
        """追加一条聊天记录（有长度上限，避免内存无限增长）。

        历史只在加入/重连时随快照下发，之后由 chat 事件逐条增量推送，
        因此这里无需考虑历史记录的同步问题。
        """
        self.chats.append({"user": username, "text": text, "time": time.strftime("%H:%M:%S")})

    def public_info(self):
        """大厅房间列表所需的公开信息，不含棋盘。

        每次都为全部在线客户端序列化，因此保持精简字段；
        读取同样在房间锁内完成，避免读到「玩家刚离开」的中间态。
        棋盘与聊天属于房间内可见内容，绝不能放进大厅列表。
        """
        with self.lock:
            return {
                "id": self.id,
                "name": self.name,
                "owner": self.owner,
                "status": self.status,
                "player_count": len(self.players),
                "max_players": MAX_PLAYERS,
                "players": [slot.username for slot in self.players],
                "spectator_count": len(self.spectators),
            }

    def snapshot(self, viewer, include_chat=True):
        """为指定观察者生成完整房间快照。

        内部自行获取 room.lock；若调用方已持有（RLock 可重入）也安全，

        包含棋盘、轮次、双方状态与「我」的角色，客户端收到后整屏重绘，
        实现服务器主导的状态同步。聊天记录只在加入/重连时下发，
        之后由独立的 chat 事件增量推送，避免每条状态报文重复携带历史。
        """
        with self.lock:
            game = self.game
            if game is not None:
                black_seat = game.black_seat
                serial = game.serial
            else:
                # 尚未开局时预显示下一局的执黑席位（轮换先手）
                black_seat = 0 if (self.game_serial + 1) % 2 == 1 else 1
                serial = self.game_serial + 1

            # 座位顺序固定为 0、1；stone 根据本局执黑席位实时换算
            players = []
            for slot in self.players:
                stone = BLACK if slot.seat == black_seat else WHITE
                players.append({
                    "username": slot.username,
                    "seat": slot.seat,
                    "ready": slot.ready,
                    "connected": slot.connected,
                    "stone": stone,
                    "score": slot.score,
                })

            # 每个观察者拿到的是一份「以自己为中心」的快照：
            # you 字段决定客户端显示玩家视角还是观战视角
            me = self.player(viewer)
            role = "player" if me else ("spectator" if self.is_spectator(viewer) else "none")
            return {
                "room_id": self.id,
                "revision": self.revision,
                "name": self.name,
                "owner": self.owner,
                "status": self.status,
                "players": players,
                "max_players": MAX_PLAYERS,
                "spectators": list(self.spectators),
                "game": game.snapshot() if game is not None else None,
                "next_serial": serial,
                "chat": list(self.chats) if include_chat else None,
                "you": {
                    "username": viewer,
                    "role": role,
                    "seat": me.seat if me else None,
                    "ready": me.ready if me else False,
                },
            }