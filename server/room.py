# -*- coding: utf-8 -*-
"""房间：2 个玩家席位 + 观战者 + 当前对局 + 聊天记录。"""
import itertools
import threading
import time
from collections import deque

from common.protocol import (
    BLACK, ROOM_FINISHED, ROOM_PLAYING, ROOM_WAITING, WHITE,
)

MAX_PLAYERS = 2
MAX_CHAT_HISTORY = 100


class PlayerSlot:
    __slots__ = ("username", "seat", "ready", "connected", "score")

    def __init__(self, username, seat, score=1000):
        self.username = username
        self.seat = seat
        self.ready = False
        self.connected = True
        self.score = score


class Room:
    _id_counter = itertools.count(1)

    def __init__(self, name, owner):
        self.id = next(Room._id_counter)
        self.name = name
        self.owner = owner
        self.lock = threading.RLock()
        self.players = []                 # [PlayerSlot]，seat 始终为 0..n-1
        self.spectators = []              # [username]
        self.chats = deque(maxlen=MAX_CHAT_HISTORY)
        self.game = None
        self.game_serial = 0              # 房间内已进行的局数
        self.revision = 0                 # 快照版本号，客户端据此丢弃旧快照

    @property
    def status(self):
        if self.game is None:
            return ROOM_WAITING
        return ROOM_FINISHED if self.game.finished else ROOM_PLAYING

    def player(self, username):
        for slot in self.players:
            if slot.username == username:
                return slot
        return None

    def is_spectator(self, username):
        return username in self.spectators

    def members(self):
        """房间内全部成员的用户名（玩家在前，观战者在后）。"""
        return [slot.username for slot in self.players] + list(self.spectators)

    def add_player(self, username, score=1000):
        """加入玩家席位，返回席位号；满员返回 None。"""
        if len(self.players) >= MAX_PLAYERS:
            return None
        seat = len(self.players)      # 席位是连续的 0..n-1
        self.players.append(PlayerSlot(username, seat, score))
        return seat

    def remove_member(self, username):
        """移除玩家或观战者；玩家离席后重新编号并清空当前对局。"""
        slot = self.player(username)
        if slot is not None:
            self.players.remove(slot)
            for i, s in enumerate(self.players):
                s.seat = i
            self.game = None
            if self.owner == username:
                self.owner = self.players[0].username if self.players else ""
            return True
        if username in self.spectators:
            self.spectators.remove(username)
            return True
        return False

    def add_chat(self, username, text):
        self.chats.append({
            "user": username,
            "text": text,
            "time": time.strftime("%H:%M:%S"),
        })

    def public_info(self):
        """大厅房间列表使用的公开信息（不含棋盘与聊天）。"""
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
        """为指定观察者生成完整房间快照，客户端据此整屏重绘。"""
        with self.lock:
            game = self.game
            # 尚未开局时预显示下一局的执黑席位（先手轮换）
            black_seat = game.black_seat if game else (0 if (self.game_serial + 1) % 2 else 1)
            serial = game.serial if game else self.game_serial + 1
            players = [{
                "username": slot.username,
                "seat": slot.seat,
                "ready": slot.ready,
                "connected": slot.connected,
                "stone": BLACK if slot.seat == black_seat else WHITE,
                "score": slot.score,
            } for slot in self.players]
            me = self.player(viewer)
            if me:
                role = "player"
            elif self.is_spectator(viewer):
                role = "spectator"
            else:
                role = "none"
            return {
                "room_id": self.id,
                "revision": self.revision,
                "name": self.name,
                "owner": self.owner,
                "status": self.status,
                "players": players,
                "max_players": MAX_PLAYERS,
                "spectators": list(self.spectators),
                "game": game.snapshot() if game else None,
                "next_serial": serial,
                "chat": list(self.chats) if include_chat else None,
                "you": {
                    "username": viewer,
                    "role": role,
                    "seat": me.seat if me else None,
                    "ready": me.ready if me else False,
                },
            }