# -*- coding: utf-8 -*-
"""JSON 文件持久化：用户、积分与对局记录。

存储格式
    整个数据库是一个 UTF-8 编码的 JSON 对象：
        {
          "version": 1,
          "next_user_id": 3,
          "next_match_id": 5,
          "users":   [ {...}, ... ],
          "matches": [ {...}, ... ]
        }
    用户名唯一性、自增 id 与积分都在内存中维护，每次修改后整体写回文件。

并发策略
    Database 实例被服务器多个连接线程共享，所有读写都在 RLock 内完成，
    因此同一时刻只有一个线程访问数据结构或磁盘。

写入安全
    _flush() 先写同目录的 .tmp 文件并 fsync，再用 os.replace 原子替换正式文件。
    即使进程在写入过程中崩溃，正式文件也只会是「上一个完整版本」，
    不会出现写了一半的 JSON。加载时若正式文件损坏，会抛出明确的异常，
    避免静默丢数据。

安全
    密码采用 PBKDF2-HMAC-SHA256（12 万次迭代、随机盐）存储，
    登录校验使用恒定时间比较，任何接口都不返回 password_hash / salt。
"""
import hashlib
import json
import os
import secrets
import threading
import time

# 初始积分与简单积分规则
INITIAL_SCORE = 1000
POINTS_WIN = 1
POINTS_DRAW = 0
POINTS_LOSS = 0

# 数据格式版本，便于将来迁移
DATA_VERSION = 1

# 原子写入使用的临时文件后缀（同目录，保证 os.replace 不跨卷）
TMP_SUFFIX = ".tmp"


class DuplicateUser(Exception):
    """用户名已存在。"""


def _now():
    """统一的本地时间字符串，写入 created_at / ended_at 等字段。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password, salt=None):
    """PBKDF2 加盐派生，返回 (salt_hex, hash_hex)。"""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000
    )
    return salt, digest.hex()


class Database:
    """JSON 文件数据库，所有方法线程安全。"""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.lock = threading.RLock()
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        # 先放一份空结构，_load() 再覆盖；文件不存在时据此写出初始文件
        self._data = self._empty()
        self._load()

    # ---------- 文件读写 ----------

    @staticmethod
    def _empty():
        """全新数据库的内存结构。"""
        return {
            "version": DATA_VERSION,
            "next_user_id": 1,
            "next_match_id": 1,
            "users": [],
            "matches": [],
        }

    def _load(self):
        """加载数据文件；不存在则创建空文件。"""
        if not os.path.exists(self.path):
            self._flush()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise RuntimeError("无法读取数据文件 %s：%s" % (self.path, exc)) from exc
        if not isinstance(data, dict):
            raise RuntimeError("数据文件格式错误：根节点必须是 JSON 对象")
        if not isinstance(data.get("users"), list) or not isinstance(data.get("matches"), list):
            raise RuntimeError("数据文件格式错误：users / matches 必须是数组")
        # 兼容手工编辑或旧版本文件：补齐缺失字段，并修正自增 id
        data["version"] = DATA_VERSION
        users = data["users"]
        matches = data["matches"]
        for user in users:
            if not isinstance(user, dict) or not isinstance(user.get("username"), str):
                raise RuntimeError("数据文件格式错误：用户记录缺少 username")
            if "password_hash" not in user or "salt" not in user:
                raise RuntimeError("数据文件格式错误：用户 %s 缺少密码字段" % user["username"])
            # 补齐统计字段，兼容手工编辑过的文件
            user.setdefault("id", 0)
            user.setdefault("score", INITIAL_SCORE)
            user.setdefault("wins", 0)
            user.setdefault("losses", 0)
            user.setdefault("draws", 0)
            user.setdefault("created_at", "")
            user.setdefault("last_login", None)
        data["next_user_id"] = self._next_sequence(data.get("next_user_id"), users)
        data["next_match_id"] = self._next_sequence(data.get("next_match_id"), matches)
        self._data = data

    @staticmethod
    def _next_sequence(stored, records):
        """保证自增 id 大于已有记录的最大 id。

        文件被手工删改后，存储的 next_*_id 可能落后于实际最大 id，
        这里统一取 max(存储值, 最大 id + 1)，避免主键冲突。
        """
        max_id = max([record.get("id", 0) for record in records] + [0])
        return max(int(stored or 1), max_id + 1)

    def _flush(self):
        """原子写回整个数据文件。

        调用方必须持有 self.lock。先写临时文件并 fsync，再 os.replace，
        保证任何时刻正式文件都是完整的 JSON。
        """
        tmp_path = self.path + TMP_SUFFIX
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.path)
        except OSError:
            # 写入失败时清理临时文件，正式文件保持上一个完整版本
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    # ---------- 内部查询 ----------

    def _find_user(self, username):
        """按用户名查找内部用户记录；不存在返回 None。"""
        for user in self._data["users"]:
            if user.get("username") == username:
                return user
        return None

    def _find_user_by_id(self, uid):
        """按主键查找内部用户记录。"""
        for user in self._data["users"]:
            if user.get("id") == uid:
                return user
        return None

    # ---------- 用户 ----------

    def create_user(self, username, password):
        """注册新用户，返回公开信息字典。

        用户名唯一性在锁内先查后插；因为所有写入都串行化，
        两个并发注册同名请求只有一个能成功。
        """
        salt, pwd_hash = hash_password(password)
        with self.lock:
            if self._find_user(username) is not None:
                raise DuplicateUser("用户名已存在")
            now = _now()
            uid = self._data["next_user_id"]
            user = {
                "id": uid,
                "username": username,
                "password_hash": pwd_hash,
                "salt": salt,
                "score": INITIAL_SCORE,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "created_at": now,
                "last_login": now,
            }
            self._data["users"].append(user)
            self._data["next_user_id"] = uid + 1
            try:
                self._flush()
            except OSError:
                # 落盘失败时回滚内存，避免出现「文件里没有、内存里有」的幽灵账号
                self._data["users"].remove(user)
                self._data["next_user_id"] = uid
                raise
            return self._public(user)

    def verify_user(self, username, password):
        """校验用户名与密码，成功返回公开信息，失败返回 None。"""
        with self.lock:
            user = self._find_user(username)
            if user is None:
                return None
            # 用该用户存储的盐重新派生，再以恒定时间比较避免时序侧信道
            expect = hash_password(password, user["salt"])[1]
            if not secrets.compare_digest(expect, user["password_hash"]):
                return None
            user["last_login"] = _now()
            self._flush()
            return self._public(user)

    def get_user(self, username):
        """按用户名查询公开信息；不存在返回 None。"""
        with self.lock:
            user = self._find_user(username)
            return self._public(user) if user is not None else None

    def get_user_by_id(self, uid):
        """按主键查询公开信息。"""
        with self.lock:
            user = self._find_user_by_id(uid)
            return self._public(user) if user is not None else None

    @staticmethod
    def _public(user):
        """只暴露非敏感字段，绝不把密码哈希返回给客户端。"""
        return {
            "id": user["id"],
            "username": user["username"],
            "score": user["score"],
            "wins": user["wins"],
            "losses": user["losses"],
            "draws": user["draws"],
            "created_at": user["created_at"],
        }

    # ---------- 对局结算 ----------

    def settle_game(self, black_user, white_user, winner_stone, room_name,
                    move_count, reason):
        """对局结束后更新双方积分与战绩，并写入对局记录。

        采用简单积分：胜方 +1，负方 +0，平局双方 +0（初始 1000）。
        不参考对手强度，积分只反映累计胜场。
        返回 {用户名: {"score": 新积分, "delta": 变化量}}。
        """
        with self.lock:
            b = self._find_user(black_user)
            w = self._find_user(white_user)
            if b is None or w is None:
                return {}
            rb, rw = b["score"], w["score"]
            if winner_stone == 1:          # 黑胜
                delta_b, delta_w = POINTS_WIN, POINTS_LOSS
                winner_name = black_user
                bw, ww = 1, 0
            elif winner_stone == 2:        # 白胜
                delta_b, delta_w = POINTS_LOSS, POINTS_WIN
                winner_name = white_user
                bw, ww = 0, 1
            else:                          # 平局
                delta_b = delta_w = POINTS_DRAW
                winner_name = "平局"
                bw = ww = 0
            new_b = rb + delta_b
            new_w = rw + delta_w
            b["score"], b["wins"], b["losses"], b["draws"] = (
                new_b, b["wins"] + bw, b["losses"] + ww,
                b["draws"] + (1 if winner_stone == 3 else 0),
            )
            w["score"], w["wins"], w["losses"], w["draws"] = (
                new_w, w["wins"] + ww, w["losses"] + bw,
                w["draws"] + (1 if winner_stone == 3 else 0),
            )
            match = {
                "id": self._data["next_match_id"],
                "room_name": room_name,
                "black_user": black_user,
                "white_user": white_user,
                "winner": winner_name,
                "move_count": move_count,
                "reason": reason,
                "ended_at": _now(),
            }
            self._data["matches"].append(match)
            self._data["next_match_id"] += 1
            try:
                self._flush()
            except OSError:
                # 落盘失败：撤销积分与记录，保持内存与文件一致
                b["score"], b["wins"], b["losses"], b["draws"] = (
                    rb, b["wins"] - bw, b["losses"] - ww,
                    b["draws"] - (1 if winner_stone == 3 else 0),
                )
                w["score"], w["wins"], w["losses"], w["draws"] = (
                    rw, w["wins"] - ww, w["losses"] - bw,
                    w["draws"] - (1 if winner_stone == 3 else 0),
                )
                self._data["matches"].pop()
                self._data["next_match_id"] -= 1
                raise
            return {
                black_user: {"score": new_b, "delta": new_b - rb},
                white_user: {"score": new_w, "delta": new_w - rw},
            }

    # ---------- 排行榜 ----------

    def leaderboard(self, limit=20):
        """按积分降序的排行榜；同分时胜场多者靠前，再按用户名稳定排序。"""
        with self.lock:
            users = sorted(
                self._data["users"],
                key=lambda u: (-u.get("score", 0), -u.get("wins", 0), u["username"]),
            )
            return [
                {
                    "rank": i + 1,
                    "username": u["username"],
                    "score": u["score"],
                    "wins": u["wins"],
                    "losses": u["losses"],
                    "draws": u["draws"],
                }
                for i, u in enumerate(users[:limit])
            ]

    def recent_matches(self, limit=10):
        """最近对局记录，按 id 倒序返回。"""
        with self.lock:
            matches = sorted(
                self._data["matches"], key=lambda m: m.get("id", 0), reverse=True
            )
            return [dict(m) for m in matches[:limit]]