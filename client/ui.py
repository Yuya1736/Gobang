# -*- coding: utf-8 -*-
"""Tkinter 图形客户端：登录、大厅、房间三个视图。

客户端只负责渲染服务器下发的房间快照，并把用户操作转成请求。
网络事件与后台线程的回调统一经 _pump 回到 Tk 主线程执行。
"""
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from client.net import NetClient, NetworkError
from common.protocol import BLACK, BOARD_SIZE, EMPTY, STONE_NAME, WHITE

# 配色
APP_BG = "#0f1720"
CARD_BG = "#18222e"
CARD_BG_ALT = "#141c26"
INPUT_BG = "#0b1219"
TEXT = "#e6edf3"
MUTED = "#8b98a5"
ACCENT = "#3ea6ff"
ACCENT_DARK = "#1f6fbf"
SUCCESS = "#42d392"
DANGER = "#ff6b6b"
BORDER = "#243140"
BOARD_BG = "#e2b76a"
BOARD_LINE = "#6b543c"

# 字体
FONT = ("Microsoft YaHei", 10)
FONT_BOLD = ("Microsoft YaHei", 10, "bold")
FONT_SMALL = ("Microsoft YaHei", 9)
FONT_TITLE = ("Microsoft YaHei", 26, "bold")
FONT_SUB = ("Microsoft YaHei", 11)
FONT_H1 = ("Microsoft YaHei", 15, "bold")

# 棋盘绘制参数
BOARD_PX = 600
MARGIN = 36
STEP = (BOARD_PX - 2 * MARGIN) / (BOARD_SIZE - 1)
STONE_R = 15


def make_button(parent, text, command, kind="normal", width=None):
    """统一风格的按钮；kind: normal / accent / danger。"""
    palette = {
        "normal": (CARD_BG_ALT, TEXT, "#243140"),
        "accent": (ACCENT, "#06121f", "#57b4ff"),
        "danger": ("#3a1c22", DANGER, "#51242c"),
    }
    bg, fg, hover = palette[kind]
    btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                    activebackground=hover, activeforeground=fg,
                    relief="flat", bd=0, padx=14, pady=7, cursor="hand2",
                    font=FONT_BOLD, width=width, highlightthickness=0)
    btn.bind("<Enter>", lambda e: btn.config(bg=hover))
    btn.bind("<Leave>", lambda e: btn.config(bg=bg))
    return btn


def make_entry(parent, width=24, show=None):
    """统一风格的输入框；show="*" 用于密码框。"""
    return tk.Entry(parent, bg=INPUT_BG, fg=TEXT, insertbackground=TEXT,
                    relief="flat", width=width, font=FONT, show=show,
                    highlightthickness=1, highlightbackground=BORDER,
                    highlightcolor=ACCENT)


class LoginView(tk.Frame):
    """登录 / 注册界面。"""

    def __init__(self, app):
        super().__init__(app, bg=APP_BG)
        cfg = app.config_data
        outer = tk.Frame(self, bg=APP_BG)
        outer.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(outer, text="●  ●", font=("Microsoft YaHei", 20), fg=ACCENT,
                 bg=APP_BG).pack()
        tk.Label(outer, text="五子棋 · 网络对战", font=FONT_TITLE,
                 fg=TEXT, bg=APP_BG).pack(pady=(2, 4))
        tk.Label(outer, text="计算机网络课程设计 · 题目 12 · C/S 架构",
                 font=FONT_SUB, fg=MUTED, bg=APP_BG).pack(pady=(0, 22))

        card = tk.Frame(outer, bg=CARD_BG, padx=30, pady=26,
                        highlightthickness=1, highlightbackground=BORDER)
        card.pack()
        form = tk.Frame(card, bg=CARD_BG)
        form.pack()

        tk.Label(form, text="服务器地址", font=FONT_SMALL, fg=MUTED,
                 bg=CARD_BG).grid(row=0, column=0, sticky="w", pady=(0, 4))
        addr = tk.Frame(form, bg=CARD_BG)
        addr.grid(row=1, column=0, sticky="we", pady=(0, 12))
        self.host_var = tk.StringVar(value=cfg.get("host", "127.0.0.1"))
        self.port_var = tk.StringVar(value=str(cfg.get("port", 9527)))
        host_entry = make_entry(addr, width=20)
        host_entry.pack(side="left")
        host_entry.config(textvariable=self.host_var)
        tk.Label(addr, text=" : ", font=FONT_BOLD, fg=MUTED, bg=CARD_BG).pack(side="left")
        port_entry = make_entry(addr, width=6)
        port_entry.pack(side="left")
        port_entry.config(textvariable=self.port_var)

        tk.Label(form, text="用户名", font=FONT_SMALL, fg=MUTED,
                 bg=CARD_BG).grid(row=2, column=0, sticky="w", pady=(0, 4))
        self.user_var = tk.StringVar(value=cfg.get("username", ""))
        user_entry = make_entry(form, width=31)
        user_entry.grid(row=3, column=0, sticky="we", pady=(0, 12))
        user_entry.config(textvariable=self.user_var)

        tk.Label(form, text="密码", font=FONT_SMALL, fg=MUTED,
                 bg=CARD_BG).grid(row=4, column=0, sticky="w", pady=(0, 4))
        self.pass_var = tk.StringVar()
        pass_entry = make_entry(form, width=31, show="*")
        pass_entry.grid(row=5, column=0, sticky="we", pady=(0, 6))
        pass_entry.config(textvariable=self.pass_var)
        pass_entry.bind("<Return>", lambda e: self.on_login())

        buttons = tk.Frame(form, bg=CARD_BG)
        buttons.grid(row=6, column=0, sticky="we", pady=(10, 6))
        self.login_btn = make_button(buttons, "登  录", self.on_login, "accent")
        self.login_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))
        self.reg_btn = make_button(buttons, "注册账号", self.on_register)
        self.reg_btn.pack(side="left", expand=True, fill="x", padx=(5, 0))

        self.status_label = tk.Label(
            card, text="请登录，或使用同一账号在本机多开客户端对战",
            font=FONT_SMALL, fg=MUTED, bg=CARD_BG, wraplength=350, justify="left")
        self.status_label.pack(pady=(10, 0))

        tk.Label(outer, text="提示：所有客户端通过回环地址 127.0.0.1 连接，\n"
                             "先运行 run_server.py，再运行多个 run_client.py 即可模拟多人对战。",
                 font=FONT_SMALL, fg=MUTED, bg=APP_BG, justify="center").pack(pady=(18, 0))

    def set_status(self, text, color=MUTED):
        self.status_label.config(text=text, fg=color)

    def set_busy(self, busy, text=None):
        state = "disabled" if busy else "normal"
        self.login_btn.config(state=state)
        self.reg_btn.config(state=state)
        if text:
            self.set_status(text, MUTED)

    def _fields(self):
        """读取并校验表单，返回 (host, port, user, password) 或 None。"""
        host = self.host_var.get().strip()
        user = self.user_var.get().strip()
        password = self.pass_var.get()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            port = 0
        if not host or not (0 < port < 65536):
            self.set_status("服务器地址或端口不正确", DANGER)
            return None
        if len(user) < 2 or len(password) < 4:
            self.set_status("用户名至少 2 位、密码至少 4 位", DANGER)
            return None
        return host, port, user, password

    def on_login(self):
        fields = self._fields()
        if fields:
            self.app.login(*fields, register=False)

    def on_register(self):
        fields = self._fields()
        if fields:
            self.app.login(*fields, register=True)


class LobbyView(tk.Frame):
    """大厅：房间列表与创建/加入/观战、排行榜入口。"""

    STATUS_TEXT = {"waiting": "等待中", "playing": "对战中", "finished": "已结束"}

    def __init__(self, app):
        super().__init__(app, bg=APP_BG)
        self.app = app
        self.rooms = {}

        header = tk.Frame(self, bg=CARD_BG, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="五子棋 · 大厅", font=FONT_H1, fg=TEXT,
                 bg=CARD_BG).pack(side="left", padx=24)
        self.user_label = tk.Label(header, text="", font=FONT, fg=MUTED, bg=CARD_BG)
        self.user_label.pack(side="left", padx=(6, 0))
        make_button(header, "退出登录", self.app.logout).pack(side="right", padx=(6, 18), pady=12)
        make_button(header, "排行榜", self.open_ranking).pack(side="right", padx=6, pady=12)
        make_button(header, "刷新", self.refresh, "accent").pack(side="right", padx=6, pady=12)

        body = tk.Frame(self, bg=APP_BG, padx=24, pady=18)
        body.pack(fill="both", expand=True)

        toolbar = tk.Frame(body, bg=APP_BG)
        toolbar.pack(fill="x", pady=(0, 10))
        tk.Label(toolbar, text="房间列表", font=FONT_BOLD, fg=TEXT,
                 bg=APP_BG).pack(side="left")
        self.count_label = tk.Label(toolbar, text="", font=FONT_SMALL, fg=MUTED, bg=APP_BG)
        self.count_label.pack(side="left", padx=10)
        make_button(toolbar, "观战", self.join_spectator).pack(side="right", padx=(6, 0))
        make_button(toolbar, "加入房间", self.join_player, "accent").pack(side="right", padx=6)
        make_button(toolbar, "创建房间", self.create_room).pack(side="right")

        table_frame = tk.Frame(body, bg=CARD_BG, highlightthickness=1,
                               highlightbackground=BORDER)
        table_frame.pack(fill="both", expand=True)
        columns = ("id", "name", "status", "players", "spectators", "owner")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                                 selectmode="browse")
        headings = {"id": "房间号", "name": "房间名称", "status": "状态",
                    "players": "玩家", "spectators": "观战", "owner": "房主"}
        widths = {"id": 70, "name": 240, "status": 90, "players": 230,
                  "spectators": 70, "owner": 120}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="w")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.enter_selected())

        self.hint = tk.Label(body, text="双击房间可直接进入；对局中的房间自动以观战身份进入",
                             font=FONT_SMALL, fg=MUTED, bg=APP_BG)
        self.hint.pack(anchor="w", pady=(8, 0))
        self._update_user_label()

    def _update_user_label(self):
        user = self.app.user or {}
        self.user_label.config(
            text="%s   积分 %s   胜 %d / 负 %d / 平 %d" % (
                user.get("username", ""), user.get("score", ""),
                user.get("wins", 0), user.get("losses", 0), user.get("draws", 0)))

    def set_rooms(self, rooms):
        """整表重绘房间列表。"""
        selected = self.selected_room_id()
        self.rooms = {room["id"]: room for room in rooms}
        for item in self.tree.get_children():
            self.tree.delete(item)
        for room in rooms:
            players = "  ".join(room["players"]) if room["players"] else "—"
            self.tree.insert("", "end", iid=str(room["id"]), values=(
                room["id"], room["name"],
                self.STATUS_TEXT.get(room["status"], room["status"]),
                players, room["spectator_count"], room["owner"]))
        if selected and str(selected) in self.tree.get_children():
            self.tree.selection_set(str(selected))
        self.count_label.config(text="共 %d 个房间" % len(rooms))
        self._update_user_label()

    def selected_room_id(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def refresh(self):
        self.app.fetch_rooms()

    def create_room(self):
        name = simpledialog.askstring(
            "创建房间", "请输入房间名称：", parent=self,
            initialvalue="%s的房间" % self.app.user.get("username", ""))
        if name:
            self.app.create_room(name.strip())

    def join_player(self):
        room_id = self.selected_room_id()
        if room_id is None:
            self.hint.config(text="请先在列表中选择一个房间", fg=DANGER)
            return
        self.app.join_room(room_id, "player")

    def join_spectator(self):
        room_id = self.selected_room_id()
        if room_id is None:
            self.hint.config(text="请先在列表中选择一个房间", fg=DANGER)
            return
        self.app.join_room(room_id, "spectator")

    def enter_selected(self):
        room_id = self.selected_room_id()
        if room_id is None:
            return
        room = self.rooms.get(room_id)
        if room is not None and room["status"] == "playing":
            self.app.join_room(room_id, "spectator")
        else:
            self.app.join_room(room_id, "player")

    def open_ranking(self):
        self.app.open_ranking()


class RoomView(tk.Frame):
    """房间界面：棋盘 + 双方信息 + 操作按钮 + 聊天。"""

    def __init__(self, app, snapshot):
        super().__init__(app, bg=APP_BG)
        self.app = app
        self.snapshot = None
        self.move_pending = False
        self.shown_results = set()
        self.hover_item = None

        top = tk.Frame(self, bg=CARD_BG, height=56)
        top.pack(fill="x")
        top.pack_propagate(False)
        make_button(top, "← 返回大厅", self.leave_room).pack(side="left",
                                                           padx=(16, 10), pady=10)
        self.title_label = tk.Label(top, text="房间", font=FONT_H1, fg=TEXT, bg=CARD_BG)
        self.title_label.pack(side="left")
        self.id_label = tk.Label(top, text="", font=FONT_SMALL, fg=MUTED, bg=CARD_BG)
        self.id_label.pack(side="left", padx=8)
        self.role_label = tk.Label(top, text="", font=FONT_SMALL, fg=ACCENT, bg=CARD_BG)
        self.role_label.pack(side="right", padx=18)

        body = tk.Frame(self, bg=APP_BG, padx=18, pady=14)
        body.pack(fill="both", expand=True)

        board_card = tk.Frame(body, bg=CARD_BG, padx=14, pady=14,
                              highlightthickness=1, highlightbackground=BORDER)
        board_card.pack(side="left", fill="y")
        self.board = tk.Canvas(board_card, width=BOARD_PX, height=BOARD_PX,
                               bg=BOARD_BG, highlightthickness=0, cursor="hand2")
        self.board.pack()
        self.board.bind("<Button-1>", self.on_board_click)
        self.board.bind("<Motion>", self.on_board_motion)
        self.board.bind("<Leave>", lambda e: self._clear_hover())

        side = tk.Frame(body, bg=APP_BG)
        side.pack(side="left", fill="both", expand=True, padx=(16, 0))

        status_card = tk.Frame(side, bg=CARD_BG, padx=16, pady=12,
                               highlightthickness=1, highlightbackground=BORDER)
        status_card.pack(fill="x")
        self.status_label = tk.Label(status_card, text="", font=FONT_BOLD, fg=ACCENT,
                                     bg=CARD_BG, anchor="w", justify="left",
                                     wraplength=340)
        self.status_label.pack(fill="x")
        self.turn_label = tk.Label(status_card, text="", font=FONT_SMALL, fg=MUTED,
                                   bg=CARD_BG, anchor="w")
        self.turn_label.pack(fill="x", pady=(4, 0))

        players_card = tk.Frame(side, bg=APP_BG)
        players_card.pack(fill="x", pady=(12, 0))
        self.player_cards = {}
        for seat in (0, 1):
            card = tk.Frame(players_card, bg=CARD_BG, padx=14, pady=10,
                            highlightthickness=1, highlightbackground=BORDER)
            card.pack(fill="x", pady=(0, 8))
            icon = tk.Canvas(card, width=30, height=30, bg=CARD_BG, highlightthickness=0)
            icon.pack(side="left")
            info = tk.Frame(card, bg=CARD_BG)
            info.pack(side="left", fill="x", expand=True, padx=10)
            name = tk.Label(info, text="等待玩家…", font=FONT_BOLD, fg=TEXT,
                            bg=CARD_BG, anchor="w")
            name.pack(fill="x")
            sub = tk.Label(info, text="", font=FONT_SMALL, fg=MUTED,
                           bg=CARD_BG, anchor="w")
            sub.pack(fill="x")
            badge = tk.Label(card, text="", font=FONT_SMALL, fg=MUTED, bg=CARD_BG)
            badge.pack(side="right")
            self.player_cards[seat] = {"frame": card, "icon": icon,
                                       "name": name, "sub": sub, "badge": badge}

        controls = tk.Frame(side, bg=APP_BG)
        controls.pack(fill="x", pady=(4, 8))
        for i in range(3):
            controls.columnconfigure(i, weight=1)
        self.ready_btn = make_button(controls, "准备", self.toggle_ready, "accent")
        self.ready_btn.grid(row=0, column=0, sticky="we", padx=(0, 4))
        self.resign_btn = make_button(controls, "认输", self.resign, "danger")
        self.resign_btn.grid(row=0, column=1, sticky="we", padx=4)
        self.leave_btn = make_button(controls, "离开房间", self.leave_room)
        self.leave_btn.grid(row=0, column=2, sticky="we", padx=(4, 0))

        self.spectator_label = tk.Label(side, text="观战者：无", font=FONT_SMALL,
                                        fg=MUTED, bg=APP_BG, anchor="w",
                                        wraplength=360, justify="left")
        self.spectator_label.pack(fill="x", pady=(2, 10))

        chat_card = tk.Frame(side, bg=CARD_BG, padx=10, pady=10,
                             highlightthickness=1, highlightbackground=BORDER)
        chat_card.pack(fill="both", expand=True)
        tk.Label(chat_card, text="房间聊天", font=FONT_BOLD, fg=TEXT,
                 bg=CARD_BG).pack(anchor="w")
        chat_box = tk.Frame(chat_card, bg=CARD_BG)
        chat_box.pack(fill="both", expand=True, pady=(6, 6))
        self.chat_text = tk.Text(chat_box, height=9, bg=INPUT_BG, fg=TEXT,
                                 font=FONT_SMALL, relief="flat", wrap="word",
                                 state="disabled", insertbackground=TEXT,
                                 padx=8, pady=6, highlightthickness=0)
        chat_scroll = ttk.Scrollbar(chat_box, orient="vertical",
                                    command=self.chat_text.yview)
        self.chat_text.configure(yscrollcommand=chat_scroll.set)
        self.chat_text.pack(side="left", fill="both", expand=True)
        chat_scroll.pack(side="right", fill="y")
        self.chat_text.tag_config("sys", foreground=MUTED)
        self.chat_text.tag_config("me", foreground=ACCENT)
        entry_row = tk.Frame(chat_card, bg=CARD_BG)
        entry_row.pack(fill="x")
        self.chat_entry = make_entry(entry_row, width=24)
        self.chat_entry.pack(side="left", fill="x", expand=True)
        self.chat_entry.bind("<Return>", lambda e: self.send_chat())
        make_button(entry_row, "发送", self.send_chat, "accent").pack(side="left", padx=(6, 0))

        self.apply(snapshot)

    # ================== 状态渲染 ==================

    def apply(self, snapshot):
        """应用一份完整快照并重绘界面；旧版本的快照直接丢弃。"""
        if snapshot is None:
            return
        current = self.snapshot
        if (current is not None
                and current.get("room_id") == snapshot.get("room_id")
                and snapshot.get("revision", 0) < current.get("revision", 0)):
            return
        self.snapshot = snapshot
        game = snapshot.get("game")
        you = snapshot.get("you") or {}
        role = you.get("role")
        my_seat = you.get("seat")

        self.title_label.config(text=snapshot["name"])
        self.id_label.config(text="房间号 %d · 房主 %s"
                                  % (snapshot["room_id"], snapshot["owner"]))
        if role == "spectator":
            role_text = "你是观战者"
        elif role == "player":
            if game:
                role_text = "你是玩家（%s）" % self.stone_text(game, my_seat)
            else:
                role_text = "你是玩家（席位 %d）" % (my_seat + 1)
        else:
            role_text = ""
        self.role_label.config(text=role_text)

        self._render_players(snapshot, my_seat)
        self._render_status(snapshot, game, role, my_seat)
        self._render_buttons(snapshot, role)
        self._draw_board(snapshot)

        spectators = snapshot.get("spectators") or []
        self.spectator_label.config(
            text="观战者（%d）：%s" % (len(spectators),
                                      "、".join(spectators) if spectators else "无"))
        if snapshot.get("chat") is not None:
            self._set_chat(snapshot["chat"])
        if role == "player" and my_seat is not None:
            for player in snapshot.get("players", []):
                if player["seat"] == my_seat:
                    self.app.update_user_score(player.get("score"))

    @staticmethod
    def stone_text(game, seat):
        if seat is None:
            return "—"
        if game:
            return "黑棋" if game["black_seat"] == seat else "白棋"
        return "黑方" if seat == 0 else "白方"

    def _render_players(self, snapshot, my_seat):
        players = {p["seat"]: p for p in snapshot.get("players", [])}
        for seat in (0, 1):
            widgets = self.player_cards[seat]
            player = players.get(seat)
            icon = widgets["icon"]
            icon.delete("all")
            if player is None:
                icon.create_oval(5, 5, 25, 25, outline=BORDER, width=1, dash=(3, 3))
                widgets["name"].config(text="等待玩家加入…", fg=MUTED)
                widgets["sub"].config(text="")
                widgets["badge"].config(text="")
                widgets["frame"].config(highlightbackground=BORDER)
                continue
            stone = player["stone"]
            fill = "#111111" if stone == BLACK else "#f5f5f5"
            outline = "#000000" if stone == BLACK else "#b9b9b9"
            icon.create_oval(4, 4, 26, 26, fill=fill, outline=outline)
            is_me = player["seat"] == my_seat
            widgets["name"].config(text=player["username"] + ("（你）" if is_me else ""),
                                   fg=ACCENT if is_me else TEXT)
            widgets["sub"].config(text="%s · 积分 %s"
                                  % (STONE_NAME[stone], player["score"]))
            if not player["connected"]:
                badge_text, color = "离线", DANGER
            elif snapshot["status"] == "playing":
                badge_text, color = "对局中", SUCCESS
            elif player["ready"]:
                badge_text, color = "已准备", SUCCESS
            else:
                badge_text, color = "已就位", MUTED
            widgets["badge"].config(text=badge_text, fg=color)
            widgets["frame"].config(highlightbackground=ACCENT if is_me else BORDER)

    def _render_status(self, snapshot, game, role, my_seat):
        status = snapshot["status"]
        players = snapshot.get("players", [])
        if status == "waiting":
            if len(players) < 2:
                main = "等待对手加入房间…"
            elif all(p.get("ready") for p in players):
                main = "双方已准备，即将开局"
            else:
                main = "等待双方点击「准备」后开始对局"
            sub = "双方就绪后服务器自动开局，黑棋先行，每局轮换先手"
        elif status == "playing":
            turn = game["turn"]
            turn_slot = next((p for p in players if p["stone"] == turn), None)
            turn_name = turn_slot["username"] if turn_slot else "—"
            main = "对局进行中 · 轮到%s（%s）" % (turn_name, STONE_NAME[turn])
            sub = ""
            if role == "player" and my_seat is not None:
                my_stone = BLACK if game["black_seat"] == my_seat else WHITE
                if my_stone == turn:
                    main = "轮到你落子（%s）" % STONE_NAME[turn]
                    sub = "点击棋盘交叉点落子"
                else:
                    sub = "等待对手落子…"
            elif role == "spectator":
                sub = "观战模式 · 棋盘实时同步"
            if not sub:
                sub = "已落子 %d 手" % (game.get("moves", 0) if game else 0)
        else:
            winner = game["winner"] if game else 0
            if winner == 3:
                main = "对局结束 · 平局（%s）" % (game.get("reason") or "棋盘已满")
            elif winner in (1, 2):
                slot = next((p for p in players if p["stone"] == winner), None)
                name = slot["username"] if slot else STONE_NAME[winner]
                main = "对局结束 · %s（%s）获胜" % (name, STONE_NAME[winner])
            else:
                main = "对局已结束"
            sub = "原因：%s · 点击「准备」可开始下一局（先手轮换）" % (
                game.get("reason") or "—")
        color = SUCCESS if status == "finished" else (ACCENT if status == "playing" else TEXT)
        self.status_label.config(text=main, fg=color)
        self.turn_label.config(text=sub)

    def _render_buttons(self, snapshot, role):
        status = snapshot["status"]
        you = snapshot.get("you") or {}
        if role == "player":
            self.ready_btn.grid(row=0, column=0, sticky="we", padx=(0, 4))
            self.resign_btn.grid(row=0, column=1, sticky="we", padx=4)
            self.ready_btn.config(text="取消准备" if you.get("ready") else "准备",
                                  state="disabled" if status == "playing" else "normal")
            self.resign_btn.config(state="normal" if status == "playing" else "disabled")
            self.leave_btn.grid(row=0, column=2, sticky="we", padx=(4, 0))
            self.leave_btn.config(text="离开房间",
                                  state="disabled" if status == "playing" else "normal")
        else:
            self.ready_btn.grid_remove()
            self.resign_btn.grid_remove()
            self.leave_btn.grid(row=0, column=0, columnspan=3, sticky="we")
            self.leave_btn.config(text="离开房间", state="normal")

    # ================== 棋盘绘制 ==================

    def _draw_board(self, snapshot):
        canvas = self.board
        canvas.delete("all")
        game = snapshot.get("game")
        canvas.create_rectangle(8, 8, BOARD_PX - 8, BOARD_PX - 8,
                                fill=BOARD_BG, outline="#8a6a3f", width=2)
        for i in range(BOARD_SIZE):
            pos = MARGIN + i * STEP
            width = 2 if i in (0, BOARD_SIZE - 1) else 1
            canvas.create_line(MARGIN, pos, BOARD_PX - MARGIN, pos,
                               fill=BOARD_LINE, width=width)
            canvas.create_line(pos, MARGIN, pos, BOARD_PX - MARGIN,
                               fill=BOARD_LINE, width=width)
        for r, c in ((3, 3), (3, 11), (11, 3), (11, 11), (7, 7)):
            x, y = MARGIN + c * STEP, MARGIN + r * STEP
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=BOARD_LINE, outline="")
        if not game:
            canvas.create_text(BOARD_PX / 2, BOARD_PX - 18, text="等待开局",
                               font=FONT_SMALL, fill="#7d6341")
            return
        board = game["board"]
        for r in range(BOARD_SIZE):
            for c in range(BOARD_SIZE):
                if board[r][c] != EMPTY:
                    self._draw_stone(r, c, board[r][c])
        if game.get("last_move"):
            lr, lc = game["last_move"]
            x, y = MARGIN + lc * STEP, MARGIN + lr * STEP
            canvas.create_oval(x - 5, y - 5, x + 5, y + 5,
                               fill="#e53e3e" if board[lr][lc] == BLACK else "#c53030",
                               outline="")
        if game.get("win_line"):
            points = [(MARGIN + c * STEP, MARGIN + r * STEP) for r, c in game["win_line"]]
            if len(points) >= 2:
                canvas.create_line(points[0][0], points[0][1],
                                   points[-1][0], points[-1][1],
                                   fill="#e53e3e", width=4)

    def _draw_stone(self, r, c, stone):
        x, y = MARGIN + c * STEP, MARGIN + r * STEP
        if stone == BLACK:
            self.board.create_oval(x - STONE_R + 1, y - STONE_R + 2,
                                   x + STONE_R - 1, y + STONE_R + 2,
                                   fill="#000000", outline="")
            self.board.create_oval(x - STONE_R, y - STONE_R,
                                   x + STONE_R, y + STONE_R,
                                   fill="#1b1b1b", outline="#000000")
            self.board.create_oval(x - 8, y - 10, x - 2, y - 4, fill="#4a4a4a", outline="")
        else:
            self.board.create_oval(x - STONE_R + 1, y - STONE_R + 2,
                                   x + STONE_R - 1, y + STONE_R + 2,
                                   fill="#9a8a6a", outline="")
            self.board.create_oval(x - STONE_R, y - STONE_R,
                                   x + STONE_R, y + STONE_R,
                                   fill="#f7f7f7", outline="#9e9e9e")
            self.board.create_oval(x - 8, y - 10, x - 2, y - 4, fill="#ffffff", outline="")

    # ================== 用户操作 ==================

    def _can_move(self):
        snapshot = self.snapshot
        if not snapshot or snapshot["status"] != "playing" or self.move_pending:
            return False
        game = snapshot.get("game")
        you = snapshot.get("you") or {}
        if not game or game["finished"] or you.get("role") != "player":
            return False
        my_stone = BLACK if game["black_seat"] == you.get("seat") else WHITE
        return my_stone == game["turn"]

    def _cell_at(self, x, y):
        """像素坐标 -> 最近的棋盘交叉点。"""
        if not (MARGIN - STEP / 2 <= x <= BOARD_PX - MARGIN + STEP / 2 and
                MARGIN - STEP / 2 <= y <= BOARD_PX - MARGIN + STEP / 2):
            return None
        c = int(round((x - MARGIN) / STEP))
        r = int(round((y - MARGIN) / STEP))
        if 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE:
            return r, c
        return None

    def on_board_motion(self, event):
        self._clear_hover()
        if not self._can_move():
            return
        cell = self._cell_at(event.x, event.y)
        if cell is None:
            return
        r, c = cell
        if self.snapshot["game"]["board"][r][c] != EMPTY:
            return
        x, y = MARGIN + c * STEP, MARGIN + r * STEP
        self.hover_item = self.board.create_oval(
            x - STONE_R, y - STONE_R, x + STONE_R, y + STONE_R,
            outline="#3ea6ff", width=2, dash=(3, 2))

    def _clear_hover(self):
        if self.hover_item is not None:
            self.board.delete(self.hover_item)
            self.hover_item = None

    def on_board_click(self, event):
        if not self._can_move():
            if self.snapshot and self.snapshot["status"] == "playing":
                self.turn_label.config(text="还没轮到你落子", fg=DANGER)
                self.after(1500, self._restore_turn_hint)
            return
        cell = self._cell_at(event.x, event.y)
        if cell is None:
            return
        r, c = cell
        if self.snapshot["game"]["board"][r][c] != EMPTY:
            self.turn_label.config(text="该位置已有棋子", fg=DANGER)
            self.after(1200, self._restore_turn_hint)
            return
        self.move_pending = True
        self.turn_label.config(text="正在提交落子…", fg=MUTED)
        self.app.request("game.move", {"row": r, "col": c},
                         on_ok=self._move_done, on_err=self._move_failed)

    def _restore_turn_hint(self):
        if self.snapshot:
            you = self.snapshot.get("you") or {}
            self._render_status(self.snapshot, self.snapshot.get("game"),
                                you.get("role"), you.get("seat"))

    def _move_done(self, data):
        self.move_pending = False
        if data.get("snapshot"):
            self.app.apply_snapshot(data["snapshot"])

    def _move_failed(self, exc):
        self.move_pending = False
        self.turn_label.config(text=str(exc), fg=DANGER)
        self.after(1800, self._restore_turn_hint)

    def toggle_ready(self):
        you = (self.snapshot or {}).get("you") or {}
        self.app.request("room.ready", {"ready": not you.get("ready", False)},
                         on_ok=lambda data: self.app.apply_snapshot(data.get("snapshot")),
                         on_err=self._flash_error)

    def resign(self):
        if not messagebox.askyesno("确认认输", "确定要认输吗？本局将判对手获胜。", parent=self):
            return
        self.app.request("game.resign", {},
                         on_ok=lambda data: self.app.apply_snapshot(data.get("snapshot")),
                         on_err=self._flash_error)

    def leave_room(self):
        if (self.snapshot and self.snapshot["status"] == "playing"
                and (self.snapshot.get("you") or {}).get("role") == "player"):
            messagebox.showinfo("提示", "对局进行中不能离开，请先认输。", parent=self)
            return
        self.app.request("room.leave", {}, on_ok=lambda _data: self.app.show_lobby(),
                         on_err=self._flash_error)

    def send_chat(self):
        text = self.chat_entry.get().strip()
        if not text:
            return
        self.chat_entry.delete(0, "end")
        self.app.request("room.chat", {"text": text}, on_err=self._flash_error)

    def _flash_error(self, exc):
        self.turn_label.config(text=str(exc), fg=DANGER)
        self.after(2000, self._restore_turn_hint)

    # ================== 聊天与结果 ==================

    def _set_chat(self, messages):
        self.chat_text.config(state="normal")
        self.chat_text.delete("1.0", "end")
        self.chat_text.config(state="disabled")
        for msg in messages:
            self.append_chat(msg)

    def append_chat(self, message):
        self.chat_text.config(state="normal")
        me = (self.app.user or {}).get("username") == message["user"]
        self.chat_text.insert("end", "[%s] %s：%s\n" % (
            message.get("time", ""), message["user"], message["text"]),
            "me" if me else None)
        self.chat_text.config(state="disabled")
        self.chat_text.see("end")

    def append_system(self, text):
        self.chat_text.config(state="normal")
        self.chat_text.insert("end", "· %s\n" % text, "sys")
        self.chat_text.config(state="disabled")
        self.chat_text.see("end")

    def show_result(self, data):
        """展示结算结果；同一局只弹一次。"""
        serial = data.get("game_serial")
        if serial in self.shown_results:
            return
        self.shown_results.add(serial)
        scores = data.get("scores") or {}
        lines = ["%s（%s）" % (data.get("winner_name") or "—",
                              data.get("reason") or "")]
        for name, info in scores.items():
            delta = info.get("delta", 0)
            lines.append("%s：%s 积分（%s%d）"
                         % (name, info.get("score"), "+" if delta > 0 else "", delta))
        self.append_system("对局结束：" + "；".join(lines))
        messagebox.showinfo("对局结束",
                            "%s\n\n%s" % (lines[0], "\n".join(lines[1:])),
                            parent=self)
        me = (self.app.user or {}).get("username")
        if me and me in scores:
            self.app.update_user_score(scores[me].get("score"))
        for name, user in (data.get("users") or {}).items():
            if me == name:
                self.app.user.update(user)
                self.app._save_config()
        self.app.update_user_score(None)


class GomokuApp(tk.Tk):
    """主窗口：管理连接、视图切换与服务器事件。"""

    CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".gomoku_client.json")
    RECONNECT_SECONDS = 45

    def __init__(self):
        super().__init__()
        self.title("五子棋 · 网络对战")
        self.geometry("1180x780")
        self.minsize(1100, 760)
        self.configure(bg=APP_BG)
        self._init_style()
        self._center_window()

        self.config_data = self._load_config()
        self.net = None
        self.token = None
        self.user = None
        self.host = self.config_data.get("host", "127.0.0.1")
        self.port = int(self.config_data.get("port", 9527))
        self.view = None
        self.room_view = None
        self.room_enter_requested = False
        self.ui_queue = queue.Queue()
        self.reconnecting = False
        self.logging_out = False
        self._closing = False

        self.banner = tk.Label(self, text="", font=FONT_BOLD, fg="#06121f",
                               bg="#f0b429", padx=12, pady=6)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(50, self._pump)
        self.show_login()
        if self.config_data.get("token"):
            self.after(200, self.try_auto_resume)

    def _init_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=CARD_BG, fieldbackground=CARD_BG,
                        foreground=TEXT, font=FONT, rowheight=30,
                        bordercolor=BORDER, borderwidth=0)
        style.configure("Treeview.Heading", background="#22303f", foreground=TEXT,
                        font=FONT_BOLD, relief="flat", padding=6)
        style.map("Treeview.Heading", background=[("active", "#2b3d50")])
        style.map("Treeview", background=[("selected", ACCENT_DARK)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Vertical.TScrollbar", background=CARD_BG, troughcolor=APP_BG,
                        bordercolor=BORDER, arrowcolor=TEXT, relief="flat")

    def _center_window(self):
        self.update_idletasks()
        width, height = 1180, 780
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        self.geometry("%dx%d+%d+%d" % (width, height, max(x, 0), max(y, 0)))

    # ================== 配置 ==================

    def _load_config(self):
        try:
            with open(self.CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_config(self):
        data = {
            "host": self.host,
            "port": self.port,
            "username": (self.user or {}).get(
                "username", self.config_data.get("username", "")),
            "token": self.token,
        }
        try:
            with open(self.CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
        except OSError:
            pass
        self.config_data = data

    # ================== 视图切换 ==================

    def _clear_view(self):
        if self.view is not None:
            self.view.destroy()
            self.view = None
        self.room_view = None

    def show_login(self, prefill_user=None):
        self._clear_view()
        view = LoginView(self)
        if prefill_user:
            view.user_var.set(prefill_user)
        view.pack(fill="both", expand=True)
        self.view = view
        self.update_idletasks()

    def show_lobby(self):
        self.room_enter_requested = False
        self._clear_view()
        view = LobbyView(self)
        view.pack(fill="both", expand=True)
        self.view = view
        self.fetch_rooms()

    def show_room(self, snapshot):
        if (self.room_view is not None and snapshot and self.room_view.snapshot
                and self.room_view.snapshot["room_id"] == snapshot["room_id"]):
            self.room_view.apply(snapshot)
            return
        self._clear_view()
        view = RoomView(self, snapshot)
        view.pack(fill="both", expand=True)
        self.view = view
        self.room_view = view
        self.room_enter_requested = False

    def apply_snapshot(self, snapshot):
        if not snapshot:
            return
        if self.room_view is not None:
            if (self.room_view.snapshot is None
                    or self.room_view.snapshot["room_id"] == snapshot["room_id"]):
                self.room_view.apply(snapshot)
            return
        if self.room_enter_requested:
            self.show_room(snapshot)

    def update_user_score(self, score):
        if score is not None and self.user is not None:
            self.user["score"] = score
        if isinstance(self.view, LobbyView):
            self.view._update_user_label()

    # ================== 网络与异步请求 ==================

    def request(self, msg_type, data=None, on_ok=None, on_err=None):
        """后台线程发请求，回调经 ui_queue 回到主线程。"""
        if self.net is None or not self.net.alive:
            if on_err:
                on_err(NetworkError("连接已断开"))
            return
        net = self.net

        def worker():
            try:
                result = net.request(msg_type, data)
            except Exception as exc:
                self.ui_queue.put((on_err, exc))
            else:
                self.ui_queue.put((on_ok, result))

        threading.Thread(target=worker, daemon=True).start()

    def _pump(self):
        net = self.net
        if net is not None:
            while True:
                try:
                    event = net.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.handle_event(event)
                except Exception as exc:
                    print("[客户端] 事件处理异常:", exc)
        while True:
            try:
                callback, arg = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if callback is None:
                continue
            try:
                callback() if arg is None else callback(arg)
            except Exception as exc:
                print("[客户端] 回调异常:", exc)
        if not self._closing:
            self.after(50, self._pump)

    def _run_worker(self, worker, ok, fail):
        def run():
            try:
                result = worker()
            except Exception as exc:
                self.ui_queue.put((fail, exc))
            else:
                self.ui_queue.put((ok, result))

        threading.Thread(target=run, daemon=True).start()

    # ================== 登录 / 注册 ==================

    def login(self, host, port, username, password, register=False):
        self.host, self.port = host, port
        view = self.view
        if isinstance(view, LoginView):
            view.set_busy(True, "正在连接服务器…")

        def worker():
            net = NetClient()
            try:
                net.connect(host, port)
                if register:
                    net.request("register", {"username": username, "password": password})
                res = net.request("login", {"username": username, "password": password})
                return net, res
            except Exception:
                net.close()
                raise

        def ok(result):
            net, res = result
            self.net = net
            self.token = res["token"]
            self.user = res["user"]
            self._save_config()
            self.show_lobby()

        def fail(exc):
            if isinstance(view, LoginView):
                view.set_busy(False, str(exc))
                view.set_status(str(exc), DANGER)
            else:
                messagebox.showerror("登录失败", str(exc), parent=self)

        self._run_worker(worker, ok, fail)

    def try_auto_resume(self):
        token = self.config_data.get("token")
        if not token:
            return
        if isinstance(self.view, LoginView):
            self.view.set_busy(True, "检测到上次登录信息，正在恢复会话…")

        def worker():
            net = NetClient()
            try:
                net.connect(self.config_data.get("host", "127.0.0.1"),
                            int(self.config_data.get("port", 9527)))
                res = net.request("resume", {"token": token})
                return net, res
            except Exception:
                net.close()
                raise

        def ok(result):
            net, res = result
            self.net = net
            self.token = res.get("token", token)
            self.user = res["user"]
            self._save_config()
            if res.get("snapshot"):
                self.room_enter_requested = True
                self.show_room(res["snapshot"])
            else:
                self.show_lobby()

        def fail(exc):
            self.token = None
            self._save_config()
            if isinstance(self.view, LoginView):
                self.view.set_busy(False, "上次会话已失效，请重新登录")
                self.view.set_status(str(exc), MUTED)

        self._run_worker(worker, ok, fail)

    def logout(self):
        self.logging_out = True
        self.request("logout", {}, on_ok=lambda _data: self._do_logout(),
                     on_err=lambda exc: self._do_logout())
        self.after(600, self._ensure_logout)

    def _ensure_logout(self):
        if self.logging_out:
            self._do_logout()

    def _do_logout(self):
        self.logging_out = False
        if self.net is not None:
            self.net.close()
        self.net = None
        self.token = None
        self.user = None
        self._save_config()
        self.show_login()

    def on_close(self):
        self._closing = True
        if self.net is not None:
            self.net.close()
        self.destroy()

    # ================== 大厅操作 ==================

    def fetch_rooms(self):
        def ok(data):
            if isinstance(self.view, LobbyView):
                self.view.set_rooms(data.get("rooms", []))
        self.request("rooms", {}, on_ok=ok)

    def create_room(self, name):
        self.room_enter_requested = True

        def fail(exc):
            self.room_enter_requested = False
            messagebox.showerror("创建房间失败", str(exc), parent=self)

        self.request("room.create", {"name": name}, on_err=fail)

    def join_room(self, room_id, role):
        self.room_enter_requested = True

        def ok(data):
            if data.get("snapshot"):
                self.show_room(data["snapshot"])

        def fail(exc):
            self.room_enter_requested = False
            if isinstance(self.view, LobbyView):
                self.view.hint.config(text=str(exc), fg=DANGER)

        self.request("room.join", {"room_id": room_id, "as": role},
                     on_ok=ok, on_err=fail)

    def open_ranking(self):
        window = tk.Toplevel(self)
        window.title("积分排行榜")
        window.configure(bg=APP_BG)
        window.geometry("620x520")
        window.transient(self)
        tk.Label(window, text="积分排行榜", font=FONT_H1, fg=TEXT,
                 bg=APP_BG).pack(anchor="w", padx=20, pady=(16, 8))
        columns = ("rank", "username", "score", "wins", "losses", "draws")
        tree = ttk.Treeview(window, columns=columns, show="headings", height=14)
        labels = {"rank": "名次", "username": "用户名", "score": "积分",
                  "wins": "胜", "losses": "负", "draws": "平"}
        widths = {"rank": 60, "username": 200, "score": 90, "wins": 60,
                  "losses": 60, "draws": 60}
        for col in columns:
            tree.heading(col, text=labels[col])
            tree.column(col, width=widths[col],
                        anchor="center" if col != "username" else "w")
        tree.pack(fill="both", expand=True, padx=20, pady=8)
        hint = tk.Label(window, text="正在加载…", font=FONT_SMALL, fg=MUTED, bg=APP_BG)
        hint.pack(anchor="w", padx=20, pady=(0, 12))

        def ok(data):
            for item in tree.get_children():
                tree.delete(item)
            ranking = data.get("ranking", [])
            for row in ranking:
                tree.insert("", "end", values=(row["rank"], row["username"],
                                               row["score"], row["wins"],
                                               row["losses"], row["draws"]))
            me = (self.user or {}).get("username")
            for i, row in enumerate(ranking):
                if row["username"] == me:
                    tree.selection_set(tree.get_children()[i])
                    tree.see(tree.get_children()[i])
            hint.config(text="共 %d 名玩家 · 简单积分制（初始 1000，胜 +1，平/负 +0）"
                             % len(ranking))

        self.request("ranking", {}, on_ok=ok,
                     on_err=lambda exc: hint.config(text=str(exc), fg=DANGER))

    # ================== 服务器事件 ==================

    def handle_event(self, event):
        if event.get("type") == "__disconnected__":
            self.start_reconnect(event.get("reason", "连接断开"))
            return
        name = event.get("event")
        data = event.get("data") or {}
        if name == "rooms_changed":
            if isinstance(self.view, LobbyView):
                self.view.set_rooms(data.get("rooms", []))
        elif name == "room_state":
            self.apply_snapshot(data)
        elif name == "chat":
            if (self.room_view is not None and self.room_view.snapshot
                    and self.room_view.snapshot["room_id"] == data.get("room_id")):
                self.room_view.append_chat(data.get("message") or {})
        elif name == "game_start":
            if self.room_view is not None:
                self.room_view.append_system("对局开始（第 %s 局）"
                                             % data.get("game_serial"))
                self.room_view.shown_results.discard(data.get("game_serial"))
        elif name == "game_over":
            if self.room_view is not None:
                self.room_view.show_result(data)
        elif name == "room_notice":
            if self.room_view is not None:
                self.room_view.append_system(data.get("text", ""))
        elif name == "room_closed":
            if self.room_view is not None:
                messagebox.showinfo("房间关闭", data.get("reason", "房间已解散"),
                                    parent=self)
                self.show_lobby()
        elif name == "kicked":
            self.net = None
            self.token = None
            self._save_config()
            messagebox.showinfo("会话结束", data.get("reason", "会话结束"), parent=self)
            self.show_login()

    # ================== 断线重连 ==================

    def start_reconnect(self, reason):
        """在 RECONNECT_SECONDS 内反复 connect + resume。"""
        if self.reconnecting or self._closing or self.logging_out:
            return
        if not self.token:
            if self.net is not None:
                self.net.close()
            self.net = None
            if not isinstance(self.view, LoginView):
                self.show_login()
            return
        self.reconnecting = True
        self._show_banner("连接已断开（%s），正在尝试重连…" % reason)
        token, host, port = self.token, self.host, self.port

        def worker():
            deadline = time.time() + self.RECONNECT_SECONDS
            attempt = 0
            last_error = ""
            while time.time() < deadline and not self._closing:
                attempt += 1
                self.ui_queue.put((lambda n=attempt: self._show_banner(
                    "连接已断开，正在重连…（第 %d 次）" % n), None))
                net = NetClient()
                try:
                    net.connect(host, port)
                    res = net.request("resume", {"token": token})
                    return net, res
                except Exception as exc:
                    last_error = str(exc)
                    net.close()
                    time.sleep(1.5)
            raise NetworkError("重连失败：%s" % last_error)

        def ok(result):
            net, res = result
            if self.net is not None:
                self.net.close()
            self.net = net
            self.token = res.get("token", token)
            self.user = res["user"]
            self._save_config()
            self.reconnecting = False
            self._hide_banner()
            if res.get("snapshot"):
                self.show_room(res["snapshot"])
            else:
                if self.room_view is not None:
                    self.show_lobby()
                elif isinstance(self.view, LobbyView):
                    self.fetch_rooms()
            if isinstance(self.view, LobbyView):
                self.view._update_user_label()

        def fail(exc):
            self.reconnecting = False
            self._hide_banner()
            if self._closing:
                return
            if isinstance(self.net, NetClient):
                self.net.close()
            self.net = None
            self.token = None
            self._save_config()
            messagebox.showerror("连接已断开", str(exc), parent=self)
            self.show_login()

        self._run_worker(worker, ok, fail)

    def _show_banner(self, text):
        self.banner.config(text=text)
        self.banner.place(relx=0.5, y=6, anchor="n")

    def _hide_banner(self):
        self.banner.place_forget()


def main():
    GomokuApp().mainloop()