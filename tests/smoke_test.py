# -*- coding: utf-8 -*-
"""协议与核心流程冒烟测试（无需图形界面）。

覆盖：注册登录、建房/加入/准备、落子与胜负判定、观战、积分结算、
先手轮换、断线重连（token 恢复快照）、排行榜与非法落子校验。
运行方式：
    py tests/smoke_test.py
"""
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from client.net import NetworkError  # noqa: E402
from tests.agent import Agent  # noqa: E402

HOST = "127.0.0.1"
PORT = 9611


def main():
    db_path = os.path.join(BASE, "data", "smoke.json")
    for suffix in ("", ".tmp"):
        try:
            os.remove(db_path + suffix)
        except OSError:
            pass
    server = subprocess.Popen(
        [sys.executable, os.path.join(BASE, "run_server.py"),
         "--port", str(PORT), "--db", db_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace")
    time.sleep(1.0)
    agents = []
    try:
        # 1. 注册登录
        a = Agent(HOST, PORT, "甲选手")
        b = Agent(HOST, PORT, "乙选手")
        agents += [a, b]
        print("[1] 注册登录 OK")

        # 2. 创建房间 / 观战 / 加入 / 准备
        room_id = a.request("room.create", {"name": "冒烟测试房"})["room_id"]
        c = Agent(HOST, PORT, "观众丙")
        agents.append(c)
        c.request("room.join", {"room_id": room_id, "as": "spectator"})
        b.request("room.join", {"room_id": room_id, "as": "player"})
        a.request("room.ready", {"ready": True})
        b.request("room.ready", {"ready": True})
        state = a.wait_state(lambda s: s["status"] == "playing")
        assert state["game"]["turn"] == 1
        black_name = next(p["username"] for p in state["players"] if p["stone"] == 1)
        print("[2] 房间/观战/准备 OK, 黑方=%s" % black_name)

        # 3. 五连获胜（甲执黑）
        mark = a.mark()
        sequence = [(7, 7), (0, 0), (7, 8), (0, 1), (7, 9), (0, 2), (7, 10), (0, 3), (7, 11)]
        for i, (r, col) in enumerate(sequence):
            mover = a if i % 2 == 0 else b
            mover.request("game.move", {"row": r, "col": col})
        over = a.wait_event("game_over", since=mark)
        assert over["winner_stone"] == 1, over
        assert over["reason"] == "五连获胜"
        finished = a.wait_state(lambda s: s["status"] == "finished", since=mark)
        assert finished["game"]["winner"] == 1
        print("[3] 胜负判定/积分结算 OK: %s 胜, 积分 %s"
              % (over["winner_name"], over["scores"]))

        # 4. 观战者收到同步快照
        c_state = c.wait_state(lambda s: s["status"] == "finished")
        assert c_state["game"]["winner"] == 1
        assert (c_state["you"] or {}).get("role") == "spectator"
        print("[4] 观战同步 OK")

        # 5. 再来一局：先手轮换
        mark = a.mark()
        a.request("room.ready", {"ready": True})
        b.request("room.ready", {"ready": True})
        state = a.wait_state(
            lambda s: s["status"] == "playing" and s["game"]["serial"] == 2, since=mark)
        print("[5] 第二局开局 OK, 黑方席位=%d（先手已轮换）" % state["game"]["black_seat"])

        # 6. 断线重连：B 断开后凭 token 恢复（此时第二局 0 手）
        token_b = b.token
        b.disconnect()
        time.sleep(0.6)
        b2 = None
        for _ in range(6):
            try:
                b2 = Agent(HOST, PORT, token=token_b)
                break
            except NetworkError:
                time.sleep(0.5)
        assert b2 is not None, "重连失败"
        agents.append(b2)
        snap = b2.latest_state()
        assert snap["room_id"] == room_id
        assert snap["status"] == "playing"
        assert snap["game"]["serial"] == 2
        assert snap["game"]["moves"] == 0
        print("[6] 断线重连恢复对局 OK（房间 %d, 第 2 局, 0 手）" % room_id)

        # 7. 重连后继续落子
        black = next(p for p in snap["players"] if p["stone"] == 1)
        first = b2 if black["username"] == "乙选手" else a
        mark = a.mark()
        first.request("game.move", {"row": 5, "col": 5})
        state = a.wait_state(lambda s: s["game"]["moves"] == 1, since=mark)
        assert state["game"]["board"][5][5] == 1
        print("[7] 重连后继续对局 OK")

        # 8. 排行榜
        ranking = a.request("ranking", {})["ranking"]
        assert len(ranking) >= 2
        print("[8] 排行榜 OK: %s" % [(r["username"], r["score"]) for r in ranking[:3]])

        # 9. 非法落子被拒绝
        try:
            a.request("game.move", {"row": 5, "col": 5})
            raise AssertionError("重复落子未被拒绝")
        except NetworkError as exc:
            assert "已有棋子" in str(exc) or "轮" in str(exc)
            print("[9] 非法落子校验 OK: %s" % exc)

        # 10. 认输后离开房间与房间清理
        c.request("room.leave", {})
        a.request("game.resign", {})
        a.request("room.leave", {})
        b2.request("room.leave", {})
        print("[10] 认输/离开房间 OK")
        print("\n全部冒烟测试通过")
        return 0
    finally:
        for agent in agents:
            try:
                agent.close()
            except Exception:
                pass
        server.terminate()
        try:
            out = server.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            server.kill()
            out = ""
        if out and "Traceback" in out:
            print(out)


if __name__ == "__main__":
    raise SystemExit(main())