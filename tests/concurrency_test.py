# -*- coding: utf-8 -*-
"""多桌并发对局测试：验证服务器在多房间同时进行时的状态一致性。

每桌由 2 名玩家 + 1 名观战者组成，所有桌并发开局、并发落子：
    * 每桌的战绩（手数、胜负、棋盘）在三方客户端中必须完全一致；
    * 其中一桌在对局中途模拟断线，凭 token 重连后继续完成对局；
    * 结束后检查房间回收、排行榜积分与胜负场次是否符合简单积分结算结果。
运行方式：
    py tests/concurrency_test.py [--tables 4] [--port 9620]
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from tests.agent import Agent  # noqa: E402

HOST = "127.0.0.1"
WIN_MOVES = 5          # 黑方每桌连成五子的手数
DROP_TABLE = 0         # 该桌在开局后进行断线重连测试


def fingerprint(snapshot):
    """提取用于一致性比对的房间状态指纹。"""
    game = snapshot.get("game") or {}
    return {
        "room_id": snapshot["room_id"],
        "revision": snapshot.get("revision"),
        "status": snapshot["status"],
        "moves": game.get("moves"),
        "winner": game.get("winner"),
        "win_line": game.get("win_line"),
        "board": game.get("board"),
        "players": [
            (p["username"], p["seat"], p["stone"], p["score"], p["connected"])
            for p in snapshot["players"]
        ],
        "spectators": snapshot["spectators"],
    }


def play_table(index, barrier, results, errors):
    """单桌完整流程：建房 → 加入/观战 → 准备 → 并发落子 → 校验。"""
    tag = "%02d" % index
    black = white = spectator = None
    latencies = []
    try:
        black = Agent(HOST, PORT, "并发%s黑" % tag)
        white = Agent(HOST, PORT, "并发%s白" % tag)
        spectator = Agent(HOST, PORT, "并发%s观" % tag)

        room_id = black.request("room.create", {"name": "并发对局%s" % tag})["room_id"]
        spectator.request("room.join", {"room_id": room_id, "as": "spectator"})
        white.request("room.join", {"room_id": room_id, "as": "player"})
        black.request("room.ready", {"ready": True})
        white.request("room.ready", {"ready": True})
        black.wait_state(lambda s: s["status"] == "playing")

        # 每桌使用不同的纵列构成黑方五连，互不干扰
        column = 3 + index
        script = [((3 + i, column), (0, i)) for i in range(WIN_MOVES)]
        started = time.perf_counter()

        def move(player, row, col):
            t0 = time.perf_counter()
            snap = player.request("game.move", {"row": row, "col": col})["snapshot"]
            latencies.append((time.perf_counter() - t0) * 1000)
            return snap

        barrier.wait()
        resumed = None
        for i, (black_move, white_move) in enumerate(script):
            if index == DROP_TABLE and i == 2:
                # 中途掉线：直接断开套接字，再用 token 恢复会话
                mark = white.mark()
                token = black.token
                black.disconnect()
                white.wait_state(lambda s, name="并发%s黑" % tag: (
                    s["status"] == "playing"
                    and not next(p for p in s["players"] if p["username"] == name)["connected"]
                ), since=mark, timeout=5)
                time.sleep(0.4)
                resumed = Agent(HOST, PORT, token=token)
                black = resumed
                snap = black.latest_state()
                assert snap["game"]["moves"] == 4, "重连后手数不一致: %r" % snap["game"]["moves"]
            move(black, *black_move)
            if i < WIN_MOVES - 1:
                move(white, *white_move)
        elapsed = time.perf_counter() - started

        states = {
            "black": black.wait_state(lambda s: s["status"] == "finished"),
            "white": white.wait_state(lambda s: s["status"] == "finished"),
            "spectator": spectator.wait_state(lambda s: s["status"] == "finished"),
        }
        prints = {name: fingerprint(snap) for name, snap in states.items()}
        if not (prints["black"] == prints["white"] == prints["spectator"]):
            raise AssertionError("三方快照不一致: %r" % prints)

        over = spectator.wait_event("game_over", timeout=5)
        if over.get("winner_stone") != 1 or over.get("reason") != "五连获胜":
            raise AssertionError("结算事件异常: %r" % over)
        scores = over.get("scores") or {}
        if scores.get("并发%s黑" % tag, {}).get("delta") != 1 \
                or scores.get("并发%s白" % tag, {}).get("delta") != 0:
            raise AssertionError("积分结算异常: %r" % scores)

        for agent in (black, white, spectator):
            agent.request("room.leave")

        results.append({
            "table": index,
            "room_id": room_id,
            "moves": prints["black"]["moves"],
            "winner": "并发%s黑" % tag,
            "reconnect": bool(resumed),
            "revision": prints["black"]["revision"],
            "seconds": round(elapsed, 3),
            "avg_latency_ms": round(statistics.mean(latencies), 2),
            "max_latency_ms": round(max(latencies), 2),
        })
    except Exception as exc:      # noqa: BLE001 - 汇总到主线程统一报告
        errors.append("第 %d 桌失败: %s" % (index, exc))
    finally:
        for agent in (black, white, spectator):
            if agent is not None:
                try:
                    agent.close()
                except Exception:
                    pass


def percentile(values, ratio):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(ratio * len(ordered)))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tables", type=int, default=4)
    parser.add_argument("--port", type=int, default=9620)
    args = parser.parse_args()
    global PORT
    PORT = args.port

    out_dir = os.path.join(BASE, "tests", "results")
    os.makedirs(out_dir, exist_ok=True)
    db_path = os.path.join(out_dir, "concurrency.json")
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

    results, errors = [], []
    max_rooms = 0
    total_started = time.perf_counter()
    lobby = None
    try:
        lobby = Agent(HOST, PORT, "并发大厅")
        barrier = threading.Barrier(args.tables + 1)
        threads = [threading.Thread(target=play_table,
                                    args=(i, barrier, results, errors),
                                    name="table-%d" % i, daemon=True)
                   for i in range(args.tables)]
        for thread in threads:
            thread.start()
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError:
            errors.append("有桌面在开局阶段失败，并发屏障被中断")
        for thread in threads:
            thread.join(timeout=60)
        if any(thread.is_alive() for thread in threads):
            errors.append("存在超时未结束的桌面对局")

        if errors:
            raise SystemExit("\n".join(errors))

        total = time.perf_counter() - total_started
        results.sort(key=lambda row: row["table"])
        rooms_seen = [len(e.get("data", {}).get("rooms") or [])
                      for _seq, e in list(lobby.events)
                      if e.get("event") == "rooms_changed"]
        max_rooms = max(rooms_seen or [0])

        empty = lobby.request("rooms", {})["rooms"]
        if empty:
            raise AssertionError("对局结束后仍有房间未回收: %r" % empty)

        ranking = lobby.request("ranking", {"limit": 100})["ranking"]
        rows = {row["username"]: row for row in ranking}
        matches = lobby.request("ranking", {"limit": 100})["matches"]
        for row in results:
            winner = row["winner"]
            loser = winner.replace("黑", "白")
            assert rows[winner]["score"] == 1001 and rows[winner]["wins"] == 1, rows.get(winner)
            assert rows[loser]["score"] == 1000 and rows[loser]["losses"] == 1, rows.get(loser)
        if len(matches) < args.tables:
            raise AssertionError("对局记录数量不足: %d" % len(matches))

        latencies = [row["avg_latency_ms"] for row in results]
        avg_latency = statistics.mean(latencies)
        report = {
            "tables": args.tables,
            "players": args.tables * 2,
            "spectators": args.tables,
            "total_moves": sum(row["moves"] for row in results),
            "max_concurrent_rooms": max_rooms,
            "total_seconds": round(total, 3),
            "avg_move_latency_ms": round(avg_latency, 2),
            "p95_table_avg_latency_ms": round(percentile(latencies, 0.95), 2),
            "consistency": "全部一致",
            "match_records": len(matches),
            "results": results,
        }
        report_path = os.path.join(out_dir, "concurrency_report.json")
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

        print("[并发测试] %d 桌 / %d 名玩家 / %d 名观战者，共 %d 手，总耗时 %.2fs"
              % (args.tables, args.tables * 2, args.tables,
                 report["total_moves"], total))
        print("[并发测试] 每桌平均落子往返 %.1fms，同时在线房间峰值 %d"
              % (avg_latency, max_rooms))
        for row in results:
            print("  第 %d 桌: 房间 %d，%d 手，%s%s，版本号 %s，耗时 %.2fs"
                  % (row["table"] + 1, row["room_id"], row["moves"], row["winner"],
                     "（含断线重连）" if row["reconnect"] else "",
                     row["revision"], row["seconds"]))
        print("[一致性] 每桌玩家与观战者的最终快照完全一致")
        print("[排行榜] 胜者 1001 分/胜场 1，负者 1000 分/负场 1，对局记录 %d 条"
              % len(matches))
        print("[房间回收] 全部房间已正常解散")
        print("报告已写入 %s" % report_path)
        return 0
    finally:
        if lobby is not None:
            try:
                lobby.close()
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