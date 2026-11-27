# -*- coding: utf-8 -*-
"""JSON 文件持久化：账号、积分与对局记录。"""
import hashlib
import json
import os
import secrets
import threading
import time

INITIAL_SCORE = 1000
POINTS_WIN = 1


class DuplicateUser(Exception):
    """用户名已存在。"""


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password, salt):
    """PBKDF2-HMAC-SHA256 加盐哈希。"""
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000
    )
    return digest.hex()


class Database:
    """JSON 文件数据库，所有方法线程安全。"""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.lock = threading.RLock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.data = {
            "next_user_id": 1,
            "next_match_id": 1,
            "users": [],
            "matches": [],
        }
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as fh:
                self.data.update(json.load(fh))
        else:
            self._save()

    def _save(self):
        """整表写回；先写临时文件再替换，避免写到一半断电留下坏文件。"""
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _find_user(self, username):
        for user in self.data["users"]:
            if user["username"] == username:
                return user
        return None

    @staticmethod
    def _public(user):
        """只返回非敏感字段。"""
        return {
            "id": user["id"],
            "username": user["username"],
            "score": user["score"],
            "wins": user["wins"],
            "losses": user["losses"],
            "draws": user["draws"],
        }

    def create_user(self, username, password):
        """注册新用户；用户名重复时抛 DuplicateUser。"""
        with self.lock:
            if self._find_user(username) is not None:
                raise DuplicateUser("用户名已存在")
            now = _now()
            uid = self.data["next_user_id"]
            salt = secrets.token_hex(16)
            user = {
                "id": uid,
                "username": username,
                "password_hash": hash_password(password, salt),
                "salt": salt,
                "score": INITIAL_SCORE,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "created_at": now,
                "last_login": now,
            }
            self.data["users"].append(user)
            self.data["next_user_id"] = uid + 1
            self._save()
            return self._public(user)

    def verify_user(self, username, password):
        """校验用户名与密码，成功返回公开信息，失败返回 None。"""
        with self.lock:
            user = self._find_user(username)
            if user is None:
                return None
            expected = hash_password(password, user["salt"])
            if not secrets.compare_digest(expected, user["password_hash"]):
                return None
            user["last_login"] = _now()
            self._save()
            return self._public(user)

    def get_user(self, username):
        with self.lock:
            user = self._find_user(username)
            return self._public(user) if user is not None else None

    def settle_game(self, black_user, white_user, winner_stone, room_name,
                    move_count, reason):
        """对局结束：更新双方积分与战绩，写入对局记录。胜方 +1 分，平/负 +0。"""
        with self.lock:
            black = self._find_user(black_user)
            white = self._find_user(white_user)
            if black is None or white is None:
                return {}
            old = {black_user: black["score"], white_user: white["score"]}
            if winner_stone == 3:
                black["draws"] += 1
                white["draws"] += 1
                winner_name = "平局"
            else:
                winner, loser = (
                    (black, white) if winner_stone == 1 else (white, black)
                )
                winner["wins"] += 1
                winner["score"] += POINTS_WIN
                loser["losses"] += 1
                winner_name = winner["username"]
            self.data["matches"].append({
                "id": self.data["next_match_id"],
                "room_name": room_name,
                "black_user": black_user,
                "white_user": white_user,
                "winner": winner_name,
                "move_count": move_count,
                "reason": reason,
                "ended_at": _now(),
            })
            self.data["next_match_id"] += 1
            self._save()
            return {
                black_user: {"score": black["score"],
                             "delta": black["score"] - old[black_user]},
                white_user: {"score": white["score"],
                             "delta": white["score"] - old[white_user]},
            }

    def leaderboard(self, limit=20):
        """按积分降序排名，同分时胜场多者靠前。"""
        with self.lock:
            users = sorted(self.data["users"],
                           key=lambda u: (-u["score"], -u["wins"], u["username"]))
            return [{
                "rank": i + 1,
                "username": u["username"],
                "score": u["score"],
                "wins": u["wins"],
                "losses": u["losses"],
                "draws": u["draws"],
            } for i, u in enumerate(users[:limit])]

    def recent_matches(self, limit=10):
        """最近的对局记录。"""
        with self.lock:
            matches = sorted(self.data["matches"],
                             key=lambda m: m["id"], reverse=True)
            return [dict(m) for m in matches[:limit]]