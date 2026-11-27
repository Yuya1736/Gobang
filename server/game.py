# -*- coding: utf-8 -*-
"""五子棋对局规则与胜负判定（服务器权威实现）。

服务器是唯一裁判：客户端只上报落子坐标，合法性校验、轮次控制、
胜负判定与最终状态全部在服务器完成，客户端仅负责渲染。

规则要点
    * 15×15 棋盘，黑棋先行，双方轮流落子；
    * 横、竖、两条对角线任一方向连续同色棋子 >=5 即获胜；
    * 棋盘下满且无人五连判平局；
    * 认输或掉线超时判对手获胜。

胜利判定只从「最后落子点」向四个方向延伸，复杂度 O(1)，
无需每步全盘扫描。
"""
from common.protocol import BOARD_SIZE, EMPTY, BLACK, WHITE

# 四个方向：横、竖、主对角线、副对角线。
# 每个方向只需正反各扫一次即可覆盖整条直线，避免重复计数。
DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))


class MoveError(Exception):
    """非法落子。"""


class GomokuGame:
    """一局五子棋的完整状态。

    seat 0 固定为第一位玩家、seat 1 为第二位玩家；
    每局开始时由服务器决定两席分别执黑还是执白（轮换先手）。
    """

    WIN = "win"
    DRAW = "draw"

    def __init__(self, black_seat=0, serial=1):
        self.board = [[EMPTY for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
        self.black_seat = black_seat          # 本局执黑的席位
        self.serial = serial                  # 对局序号（房间内第几局）
        self.turn = BLACK                     # 黑棋先行
        self.moves = []                       # [(r, c, stone), ...] 按时间顺序
        self.last_move = None                 # (r, c)
        self.winner = 0                       # 0=未结束，BLACK/WHITE=胜者，3=平局
        self.win_line = []                    # 构成五连的坐标，用于高亮
        self.reason = ""                      # 结束原因：五连/认输/掉线/棋盘已满

    # ---------- 席位与棋子映射 ----------

    def stone_of(self, seat):
        """返回席位对应的棋子颜色。"""
        return BLACK if seat == self.black_seat else WHITE

    def seat_of(self, stone):
        """返回棋子颜色对应的席位。"""
        return self.black_seat if stone == BLACK else 1 - self.black_seat

    @property
    def finished(self):
        return self.winner != 0

    # ---------- 落子 ----------

    def apply_move(self, seat, row, col):
        """校验并执行一次落子，返回本次落子结果描述。

        校验内容：对局未结束、轮到该席位、坐标在棋盘内、目标点为空。
        校验失败抛出 MoveError，由上层转换为错误响应。
        """
        if self.finished:
            raise MoveError("对局已结束")
        if seat not in (0, 1):
            raise MoveError("席位非法")
        stone = self.stone_of(seat)
        if stone != self.turn:
            raise MoveError("还没轮到你落子")
        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
            raise MoveError("落子位置超出棋盘")
        if self.board[row][col] != EMPTY:
            raise MoveError("该位置已有棋子")

        # 落子并记录
        self.board[row][col] = stone
        self.moves.append((row, col, stone))
        self.last_move = (row, col)

        # 判定五连
        line = self._find_five(row, col, stone)
        if line:
            self.winner = stone
            self.win_line = line
            self.reason = "五连获胜"
            return {"result": self.WIN, "stone": stone, "line": line}

        # 棋盘下满判平局
        if len(self.moves) >= BOARD_SIZE * BOARD_SIZE:
            self.winner = 3
            self.reason = "棋盘已满"
            return {"result": self.DRAW}

        # 交换行棋方
        self.turn = WHITE if self.turn == BLACK else BLACK
        return {"result": "continue"}

    def resign(self, seat):
        """认输：由对手获胜。

        只设置终局字段，不直接写数据库；积分结算由上层统一处理。
        """
        if self.finished:
            raise MoveError("对局已结束")
        winner = self.stone_of(1 - seat)
        self.winner = winner
        self.reason = "对手认输"
        return {"result": self.WIN, "stone": winner}

    def forfeit(self, seat):
        """掉线判负：由对手获胜。

        与 resign 的区别是幂等：对局已结束时返回 None 而非抛异常，
        因为掉线判负可能被宽限期定时器与巡检线程重复触发。
        """
        if self.finished:
            return None
        winner = self.stone_of(1 - seat)
        self.winner = winner
        self.reason = "对手掉线"
        return {"result": self.WIN, "stone": winner}

    # ---------- 胜负判定 ----------

    def _find_five(self, row, col, stone):
        """从落子点沿四个方向统计连续同色棋子，长度 >=5 即获胜。

        返回构成五连的坐标列表（用于客户端高亮），否则返回空列表。
        """
        for dr, dc in DIRECTIONS:
            cells = [(row, col)]
            # 正向延伸
            r, c = row + dr, col + dc
            while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and self.board[r][c] == stone:
                cells.append((r, c))
                r, c = r + dr, c + dc
            # 反向延伸
            r, c = row - dr, col - dc
            while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and self.board[r][c] == stone:
                cells.insert(0, (r, c))
                r, c = r - dr, c - dc
            if len(cells) >= 5:
                return cells
        return []

    # ---------- 状态导出 ----------

    def snapshot(self):
        """导出可 JSON 序列化的对局快照，供客户端渲染。

        完整快照而非增量更新，客户端每次用快照整体重绘，
        从根本上避免丢包/乱序导致的界面状态不一致。
        """
        return {
            "board": self.board,
            "turn": self.turn,
            "black_seat": self.black_seat,
            "serial": self.serial,
            "last_move": list(self.last_move) if self.last_move else None,
            "winner": self.winner,            # 0=进行中, 1/2=黑/白胜, 3=平局
            "win_line": [list(p) for p in self.win_line],
            "moves": len(self.moves),
            "reason": self.reason,
            "finished": self.finished,
        }