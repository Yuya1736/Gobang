# -*- coding: utf-8 -*-
"""五子棋规则与胜负判定（服务器裁定）。"""
from common.protocol import BLACK, BOARD_SIZE, EMPTY, WHITE

# 横、竖、主对角线、副对角线
DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))


class MoveError(Exception):
    """非法落子。"""


class GomokuGame:
    """一局五子棋。seat 0/1 是固定席位，黑白颜色每局轮换。"""

    def __init__(self, black_seat, serial):
        self.board = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.black_seat = black_seat
        self.serial = serial          # 房间内第几局
        self.turn = BLACK             # 黑棋先行
        self.moves = []               # [(row, col, stone), ...]
        self.last_move = None
        self.winner = 0               # 0=进行中 1=黑胜 2=白胜 3=平局
        self.win_line = []            # 构成五连的坐标
        self.reason = ""
        self.settled = False          # 积分是否已结算

    @property
    def finished(self):
        return self.winner != 0

    def stone_of(self, seat):
        return BLACK if seat == self.black_seat else WHITE

    def apply_move(self, seat, row, col):
        """校验并落子；非法落子抛出 MoveError。"""
        if self.finished:
            raise MoveError("对局已结束")
        if seat not in (0, 1):
            raise MoveError("席位非法")
        if self.stone_of(seat) != self.turn:
            raise MoveError("还没轮到你落子")
        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
            raise MoveError("落子位置超出棋盘")
        if self.board[row][col] != EMPTY:
            raise MoveError("该位置已有棋子")

        stone = self.turn
        self.board[row][col] = stone
        self.moves.append((row, col, stone))
        self.last_move = (row, col)

        line = self._find_five(row, col, stone)
        if line:
            self.winner = stone
            self.win_line = line
            self.reason = "五连获胜"
        elif len(self.moves) >= BOARD_SIZE * BOARD_SIZE:
            self.winner = 3
            self.reason = "棋盘已满"
        else:
            self.turn = WHITE if self.turn == BLACK else BLACK

    def forfeit(self, seat, reason):
        """该席位判负（认输或掉线）；对局已结束时不做处理。"""
        if self.finished:
            return
        self.winner = self.stone_of(1 - seat)
        self.reason = reason

    def _find_five(self, row, col, stone):
        """从落子点向四个方向延伸，返回构成五连的坐标，否则返回空列表。"""
        for dr, dc in DIRECTIONS:
            cells = [(row, col)]
            r, c = row + dr, col + dc
            while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and self.board[r][c] == stone:
                cells.append((r, c))
                r, c = r + dr, c + dc
            r, c = row - dr, col - dc
            while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and self.board[r][c] == stone:
                cells.insert(0, (r, c))
                r, c = r - dr, c - dc
            if len(cells) >= 5:
                return cells
        return []

    def snapshot(self):
        """导出给客户端渲染的对局快照。"""
        return {
            "board": self.board,
            "turn": self.turn,
            "black_seat": self.black_seat,
            "serial": self.serial,
            "last_move": list(self.last_move) if self.last_move else None,
            "winner": self.winner,
            "win_line": [list(p) for p in self.win_line],
            "moves": len(self.moves),
            "reason": self.reason,
            "finished": self.finished,
        }