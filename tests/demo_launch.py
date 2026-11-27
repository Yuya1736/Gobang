# -*- coding: utf-8 -*-
"""批量启动/关闭演示客户端（用于自动截图，非正常运行入口）。"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
PID_FILE = os.path.join(BASE, "tests", "results", "demo_pids.json")

ROLES = [
    ("creator", "棋手甲"),
    ("joiner", "棋手乙"),
    ("spectator", "观战者丙"),
    ("lobby", "大厅访客"),
    ("login", ""),
]

DEMO_PASSWORD = "123456"


def seed_users(port):
    """确保演示账号存在（重复注册忽略即可）。"""
    from client.net import NetClient, NetworkError

    for _role, user in ROLES:
        if not user:
            continue
        net = NetClient()
        try:
            net.connect("127.0.0.1", port)
            try:
                net.request("register", {"username": user, "password": DEMO_PASSWORD})
                print("已注册演示账号:", user)
            except NetworkError as exc:
                print("演示账号已存在:", user, "(%s)" % exc)
        except NetworkError as exc:
            print("注册演示账号失败:", user, exc)
        finally:
            net.close()


def warmup(port):
    """先进行一局无界面热身对局，让排行榜出现真实积分差异。"""
    from tests.agent import Agent

    a = b = None
    try:
        a = Agent("127.0.0.1", port, username="棋手甲", password=DEMO_PASSWORD)
        b = Agent("127.0.0.1", port, username="棋手乙", password=DEMO_PASSWORD)
        room_id = a.request("room.create", {"name": "热身对局"})["room_id"]
        b.request("room.join", {"room_id": room_id, "as": "player"})
        a.request("room.ready", {"ready": True})
        b.request("room.ready", {"ready": True})
        script = [((7, 4), (0, 0)), ((7, 5), (0, 1)), ((7, 6), (0, 2)),
                  ((7, 7), (0, 3)), ((7, 8), None)]
        for black, white in script:
            a.request("game.move", {"row": black[0], "col": black[1]})
            if white is not None:
                b.request("game.move", {"row": white[0], "col": white[1]})
        time.sleep(0.3)
        a.request("room.leave")
        b.request("room.leave")
        print("热身对局完成，积分已更新")
    except Exception as exc:      # noqa: BLE001 - 演示环境容错
        print("热身对局跳过:", exc)
    finally:
        for agent in (a, b):
            if agent is not None:
                agent.close()


def start(port):
    seed_users(port)
    warmup(port)
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    pids = {}
    for role, user in ROLES:
        cmd = [sys.executable, "-u", "-X", "utf8",
               os.path.join(BASE, "tests", "demo_client.py"),
               "--role", role, "--port", str(port), "--lifetime", "180"]
        if user:
            cmd += ["--user", user]
        log_path = os.path.join(BASE, "tests", "results", "demo_%s.log" % role)
        log_file = open(log_path, "w", encoding="utf-8")
        proc = subprocess.Popen(cmd, cwd=BASE, stdout=log_file, stderr=subprocess.STDOUT)
        pids[role] = proc.pid
        time.sleep(0.35)
    with open(PID_FILE, "w", encoding="utf-8") as fh:
        json.dump(pids, fh)
    print("已启动演示客户端:", pids)


def stop():
    try:
        with open(PID_FILE, "r", encoding="utf-8") as fh:
            pids = json.load(fh)
    except (OSError, ValueError):
        print("没有记录在案的演示进程")
        return
    for role, pid in pids.items():
        try:
            os.kill(pid, signal.SIGTERM)
            print("已关闭 %s (pid %s)" % (role, pid))
        except OSError:
            pass
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop"])
    parser.add_argument("--port", type=int, default=9600)
    args = parser.parse_args()
    if args.action == "start":
        start(args.port)
    else:
        stop()


if __name__ == "__main__":
    main()