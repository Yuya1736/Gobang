# -*- coding: utf-8 -*-
"""演示用客户端：自动登录、建房/加入/准备、按脚本走棋并聊天。

仅用于课程设计演示与截图，正常使用请运行 run_client.py。
    py tests/demo_client.py --role creator --user 棋手甲
"""
import argparse
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from client import ui  # noqa: E402
from client.ui import GomokuApp, LobbyView, LoginView  # noqa: E402

# 双方交替落子的演示棋谱（不会在 18 手内分出胜负，画面更美观）
SCRIPT_MOVES = [
    (7, 7), (7, 8), (6, 8), (8, 8), (6, 6), (8, 6), (6, 7), (8, 7),
    (5, 7), (9, 7), (5, 8), (9, 8), (4, 8), (10, 8), (5, 6), (9, 6),
    (4, 7), (10, 7), (7, 6), (7, 9),
]

CHAT_SCRIPT = {
    "creator": [(3.5, "大家好，欢迎来到五子棋交流房～"), (9.0, "这局我执黑先行，请多指教")],
    "joiner": [(4.5, "收到，我们开始吧"), (10.0, "这一步走得漂亮！")],
    "spectator": [(5.5, "观战中，学习一下高手的走法")],
}

ROLE_TITLES = {
    "creator": "五子棋 · 网络对战 - 棋手甲",
    "joiner": "五子棋 · 网络对战 - 棋手乙",
    "spectator": "五子棋 · 网络对战 - 观战者丙",
    "lobby": "五子棋 · 网络对战 - 大厅",
    "login": "五子棋 · 网络对战 - 登录",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True,
                        choices=["creator", "joiner", "spectator", "lobby", "login"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9600)
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="123456")
    parser.add_argument("--room", default="五子棋交流房")
    parser.add_argument("--lifetime", type=float, default=60)
    parser.add_argument("--max-moves", type=int, default=18)
    args = parser.parse_args()

    temp_dir = os.path.join(os.environ.get("TEMP", "."), "gomoku_demo")
    os.makedirs(temp_dir, exist_ok=True)
    config_path = os.path.join(temp_dir, "client_%s.json" % args.role)
    try:
        os.remove(config_path)
    except OSError:
        pass
    GomokuApp.CONFIG_PATH = config_path

    app = GomokuApp()
    app.title(ROLE_TITLES[args.role])
    start = time.time()
    state = {"login_started": False, "created": False, "joined": False,
             "ready_sent": False, "chat_index": 0, "ranking": False, "closed": False}

    if args.role == "login":
        view = app.view
        view.user_var.set("棋手甲")
        view.pass_var.set("123456")
        view.set_status("输入用户名与密码登录，或用同一账号在本机多开客户端对战")

    def do_chat(delay, text):
        if not state["closed"] and app.room_view is not None and time.time() - start >= delay:
            app.request("room.chat", {"text": text})

    def try_move():
        view = app.room_view
        if view is None or view.snapshot is None:
            return
        snap = view.snapshot
        game = snap.get("game")
        you = snap.get("you") or {}
        if snap["status"] != "playing" or not game or you.get("role") != "player":
            return
        if view.move_pending or game["moves"] >= args.max_moves:
            return
        my_stone = 1 if game["black_seat"] == you.get("seat") else 2
        if game["turn"] != my_stone or game["moves"] >= len(SCRIPT_MOVES):
            return
        row, col = SCRIPT_MOVES[game["moves"]]
        view.move_pending = True
        log("发送落子 %d,%d (第 %d 手)" % (row, col, game["moves"] + 1))

        def ok(data):
            view.move_pending = False
            moves = (data.get("snapshot", {}).get("game") or {}).get("moves")
            log("落子成功 -> 手数=%s" % moves)
            if data.get("snapshot"):
                app.apply_snapshot(data["snapshot"])

        def err(_exc):
            view.move_pending = False
            log("落子失败: %s" % _exc)

        app.request("game.move", {"row": row, "col": col}, on_ok=ok, on_err=err)

    last_view = [None]
    last_status = [None]

    def log(message):
        print("[%s %.1fs] %s" % (args.role, time.time() - start, message), flush=True)

    def tick():
        if state["closed"]:
            return
        elapsed = time.time() - start
        vtype = type(app.view).__name__
        if vtype != last_view[0]:
            last_view[0] = vtype
            log("视图 -> %s" % vtype)
        if args.role != "login" and not state["login_started"] \
                and isinstance(app.view, LoginView) and elapsed >= 0.4:
            state["login_started"] = True
            log("发起登录 %s" % args.user)
            app.login(args.host, args.port, args.user, args.password)

        if isinstance(app.view, LobbyView):
            view = app.view
            if args.role == "creator" and not state["created"]:
                state["created"] = True
                app.create_room(args.room)
            elif args.role in ("joiner", "spectator") and not state["joined"]:
                # 同名房间可能残留，优先选择最新创建的房间
                candidates = [r for r in view.rooms.values() if r["name"] == args.room]
                room = max(candidates, key=lambda r: r["id"], default=None)
                if room is not None:
                    state["joined"] = True
                    app.join_room(room["id"], "player" if args.role == "joiner" else "spectator")

        view = app.room_view
        if view is not None and view.snapshot is not None:
            snap = view.snapshot
            if snap["status"] != last_status[0]:
                last_status[0] = snap["status"]
                log("房间 %s 状态=%s 手数=%s" % (
                    snap["name"], snap["status"],
                    (snap.get("game") or {}).get("moves")))
            you = snap.get("you") or {}
            if args.role in ("creator", "joiner") and not state["ready_sent"] and \
                    snap["status"] == "waiting" and len(snap["players"]) == 2:
                state["ready_sent"] = True
                app.request("room.ready", {"ready": True})
            if args.role in ("creator", "joiner"):
                try_move()
        # 按时间顺序发聊天（每个角色最多两条）
        script = CHAT_SCRIPT.get(args.role, [])
        while state["chat_index"] < len(script) and elapsed >= script[state["chat_index"]][0]:
            text = script[state["chat_index"]][1]
            state["chat_index"] += 1
            do_chat(0, text)

        if args.role == "creator" and not state["ranking"] and elapsed >= 14:
            state["ranking"] = True
            app.open_ranking()

        app.after(180, tick)

    def close():
        state["closed"] = True
        try:
            app.on_close()
        except Exception:
            pass

    app.after(300, tick)
    app.after(int(args.lifetime * 1000), close)
    app.mainloop()


if __name__ == "__main__":
    main()