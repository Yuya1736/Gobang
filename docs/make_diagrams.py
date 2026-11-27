# -*- coding: utf-8 -*-
"""生成说明书插图：系统架构图、通信时序图、断线重连状态图。

运行：py docs/make_diagrams.py
输出：docs/images/00_architecture.png / 00_sequence.png / 00_reconnect.png
"""
import os

from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "docs", "images")
FONT_PATH = "C:/Windows/Fonts/msyh.ttc"

BG = "#ffffff"
INK = "#1f2933"
MUTED = "#52606d"
CARD = "#f0f4f8"
CARD2 = "#e4ebf2"
ACCENT = "#1f6fbf"
ACCENT_BG = "#dbeafe"
GREEN_BG = "#dcfce7"
GREEN = "#15803d"
AMBER_BG = "#fef3c7"
AMBER = "#b45309"
RED = "#b91c1c"
RED_BG = "#fee2e2"
BORDER = "#9aa5b1"


def font(size, bold=False):
    # msyh.ttc 包含常规与粗体字族，index=1 为粗体
    return ImageFont.truetype(FONT_PATH, size, index=1 if bold else 0)


def box(draw, xy, text, size=20, fill=CARD, outline=BORDER, color=INK,
        bold=False, radius=10, width=2):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)
    draw.text(((xy[0] + xy[2]) / 2, (xy[1] + xy[3]) / 2), text,
              font=font(size, bold), fill=color, anchor="mm", align="center")


def label(draw, xy, text, size=18, color=INK, bold=False, anchor="mm"):
    draw.text(xy, text, font=font(size, bold), fill=color, anchor=anchor)


def label_bg(draw, xy, text, size=18, color=INK, bold=False, anchor="mm", pad=3):
    """带白色底衬的标签，避免与生命线/箭头交叠时难以辨认。"""
    f = font(size, bold)
    bbox = draw.textbbox((0, 0), text, font=f, anchor=anchor)
    x, y = xy
    rect = (x + bbox[0] - pad, y + bbox[1] - pad,
            x + bbox[2] + pad, y + bbox[3] + pad)
    draw.rectangle(rect, fill=BG)
    draw.text(xy, text, font=f, fill=color, anchor=anchor)


def save_trimmed(img, filename, top=42):
    """裁掉顶部预留的标题空白，图题由 Word 图注统一给出。"""
    img = img.crop((0, top, img.width, img.height))
    img.save(os.path.join(OUT, filename))


def arrow(draw, start, end, color=ACCENT, width=2, dash=False, head=10):
    x1, y1 = start
    x2, y2 = end
    if dash:
        # 手绘虚线：按距离等分
        import math
        dist = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(dist // 12))
        for i in range(steps):
            t0, t1 = i / steps, (i + 0.5) / steps
            draw.line((x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0,
                       x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1), fill=color, width=width)
    else:
        draw.line((x1, y1, x2, y2), fill=color, width=width)
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    p1 = (x2 - head * math.cos(angle - 0.45), y2 - head * math.sin(angle - 0.45))
    p2 = (x2 - head * math.cos(angle + 0.45), y2 - head * math.sin(angle + 0.45))
    draw.polygon([(x2, y2), p1, p2], fill=color)


def architecture():
    img = Image.new("RGB", (1280, 820), BG)
    d = ImageDraw.Draw(img)
    # 三个客户端进程
    label(d, (210, 88), "客户端进程（可多开）", 23, MUTED, True)
    for i, name in enumerate(("客户端 A（黑方）", "客户端 B（白方）", "客户端 C（观战者）")):
        x = 40 + i * 0
        y = 112 + i * 118
        d.rounded_rectangle((40, y, 380, y + 100), radius=10, fill=CARD, outline=BORDER, width=2)
        label(d, (210, y + 20), name, 23, INK, True)
        d.rounded_rectangle((56, y + 38, 364, y + 68), radius=6, fill=ACCENT_BG, outline=ACCENT, width=1)
        label(d, (210, y + 53), "Tkinter 界面  client/ui.py", 19, ACCENT)
        d.rounded_rectangle((56, y + 72, 364, y + 98), radius=6, fill="#ffffff", outline=BORDER, width=1)
        label(d, (210, y + 85), "网络层  client/net.py", 19, MUTED)

    # 协议通道
    d.rounded_rectangle((470, 112, 730, 460), radius=12, fill="#ffffff", outline=ACCENT, width=2)
    label(d, (600, 140), "TCP 连接", 24, ACCENT, True)
    label(d, (600, 172), "127.0.0.1:9527", 23, INK, True)
    label(d, (600, 206), "单行 JSON 协议", 21, MUTED)
    label(d, (600, 236), "请求 / 响应 / 事件", 19, MUTED)
    label(d, (600, 270), "长度上限 1 MB", 19, MUTED)
    label(d, (600, 304), "请求 id 关联响应", 19, MUTED)
    label(d, (600, 338), "事件由服务器推送", 19, MUTED)
    label(d, (600, 380), "心跳 ping / 15s", 19, MUTED)
    label(d, (600, 412), "断线 60s 宽限", 19, AMBER)
    for y in (162, 280, 398):
        arrow(d, (382, y), (468, y), ACCENT, 2)
        arrow(d, (732, y), (818, y), ACCENT, 2)

    # 服务器
    label(d, (1080, 88), "服务器进程  run_server.py", 23, MUTED, True)
    d.rounded_rectangle((820, 112, 1240, 500), radius=12, fill="#ffffff", outline=ACCENT, width=2)
    layers = [
        ("主线程：accept 循环", 132, CARD2, MUTED),
        ("连接线程 ×N（每连接一线程）", 186, CARD2, MUTED),
        ("请求分发 handlers（app.py）", 240, ACCENT_BG, ACCENT),
        ("房间 / 对局 / 会话管理", 312, CARD, INK),
        ("房间锁 room.lock + 服务器锁", 366, CARD, MUTED),
        ("JSON 文件：账号 · 积分 · 对局记录", 432, GREEN_BG, GREEN),
    ]
    for text, y, fill, color in layers:
        h = 40 if y != 312 and y != 366 else 42
        d.rounded_rectangle((838, y, 1222, y + h), radius=8, fill=fill, outline=BORDER, width=1)
        label(d, (1030, y + h / 2), text, 19, color)

    # 服务器并行服务多桌
    label(d, (640, 560), "服务器为每个房间维护独立的对局与锁，多桌并发互不阻塞", 19, MUTED)
    label(d, (640, 590), "所有客户端只能访问 127.0.0.1，满足课程设计的本机运行要求", 19, MUTED)

    # 底部职责说明
    d.rounded_rectangle((40, 630, 1240, 790), radius=12, fill=CARD, outline=BORDER, width=1)
    label(d, (70, 662), "职责划分", 21, INK, True, "lm")
    lines = [
        "客户端：渲染服务器下发的完整房间快照；把准备、落子、认输、聊天等操作转成请求。",
        "服务器：校验轮次与坐标、判定胜负、计算积分、广播快照；是唯一的状态权威。",
        "一致性：每次广播递增 revision，客户端丢弃迟到的旧快照，避免棋盘回退。",
    ]
    for i, line in enumerate(lines):
        label(d, (70, 702 + i * 31), line, 18, MUTED, anchor="lm")

    save_trimmed(img, "00_architecture.png")


def sequence():
    img = Image.new("RGB", (1280, 1000), BG)
    d = ImageDraw.Draw(img)

    actors = ["客户端 A\n（黑）", "客户端 B\n（白）", "观战者 C", "服务器"]
    xs = [180, 470, 720, 1010]
    top, bottom = 132, 940
    for x, name in zip(xs, actors):
        fill = ACCENT_BG if name == "服务器" else CARD
        d.rounded_rectangle((x - 78, top - 58, x + 78, top), radius=8, fill=fill, outline=BORDER, width=2)
        label(d, (x, top - 29), name, 21, INK, True)
        d.line((x, top, x, bottom), fill=BORDER, width=2)

    # (src, dsts, text, y, dashed) —— dsts 为元组表示一次广播，只画一次标签
    steps = [
        (0, (3,), "register / login → 返回 token", 172, False),
        (0, (3,), "room.create", 208, False),
        (1, (3,), "room.join（玩家）", 244, False),
        (2, (3,), "room.join（观战者）", 280, False),
        (3, (0, 1), "room_state 快照（含新玩家）", 316, True),
        (0, (3,), "room.ready", 352, False),
        (1, (3,), "room.ready", 382, False),
        (3, (0, 1, 2), "game_start + room_state（黑方先行）", 420, True),
        (0, (3,), "game.move（第 1 手）", 462, False),
        (3, (0, 1, 2), "room_state（含落子）", 500, True),
        (1, (3,), "game.move（第 2 手）", 542, False),
        (3, (0, 1, 2), "room_state", 578, True),
        (0, (3,), "… 交替落子直至五连 …", 618, False),
        (3, (0, 1, 2), "room_state（终局：五连获胜）", 658, True),
        (3, (0, 1, 2), "game_over（胜者 / 积分 +1）", 698, True),
        (0, (3,), "ranking 请求", 742, False),
        (3, (0,), "ranking 响应（积分 / 胜负 / 记录）", 780, True),
    ]
    for src, dsts, text, y, dashed in steps:
        color = GREEN if dashed else ACCENT
        x1 = xs[src]
        for dst in dsts:
            arrow(d, (x1, y), (xs[dst], y), color, 2, dash=dashed)
        # 标签居中于首末目标之间，白色底衬避免生命线穿透文字
        mid = (x1 + xs[dsts[0]]) / 2
        label_bg(d, (mid, y - 15), text, 19, color)

    label(d, (640, 900), "实线：客户端请求    虚线：服务器事件 / 响应广播", 18, MUTED)
    label(d, (640, 930), "服务器在锁内生成快照并以单调递增的 revision 广播，三方状态始终一致", 18, MUTED)
    save_trimmed(img, "00_sequence.png")


def reconnect():
    img = Image.new("RGB", (1120, 720), BG)
    d = ImageDraw.Draw(img)

    box(d, (70, 120, 330, 200), "在线\nconnected=True", 22, GREEN_BG, GREEN, GREEN, True)
    box(d, (430, 120, 690, 200), "TCP 断开\n标记离线", 22, AMBER_BG, AMBER, AMBER, True)
    box(d, (790, 120, 1050, 200), "宽限期 60s\n保留席位与对局", 22, CARD, BORDER, INK, True)
    box(d, (790, 330, 1050, 410), "重连成功\nresume(token)", 22, GREEN_BG, GREEN, GREEN, True)
    box(d, (430, 330, 690, 410), "宽限期满\n仍离线", 22, RED_BG, RED, RED, True)
    box(d, (430, 540, 690, 620), "判负 + 结算积分\n释放席位", 22, RED_BG, RED, RED, True)
    box(d, (70, 540, 330, 620), "会话销毁\n回到登录页", 22, CARD, BORDER, INK, True)

    arrow(d, (332, 160), (428, 160), AMBER)
    arrow(d, (692, 160), (788, 160), AMBER)
    arrow(d, (920, 202), (920, 328), GREEN)
    arrow(d, (790, 370), (692, 370), RED)
    arrow(d, (560, 412), (560, 538), RED)
    arrow(d, (430, 580), (332, 580), RED)
    arrow(d, (240, 622), (240, 205), ACCENT, 2, dash=True)

    label(d, (380, 142), "连接异常", 18, AMBER)
    label(d, (908, 262), "用本地 token 重连", 18, GREEN, anchor="rm")
    label(d, (742, 350), "超时", 18, RED)
    label(d, (575, 475), "对局未结束则判负", 18, RED, anchor="lm")
    label(d, (381, 602), "回登录页", 18, RED)
    label(d, (255, 470), "重新登录", 18, ACCENT, anchor="lm")
    label(d, (255, 330), "用户输入账号密码", 18, ACCENT, anchor="lm")

    d.rounded_rectangle((70, 660, 1050, 700), radius=8, fill=CARD, outline=BORDER, width=1)
    label(d, (560, 680), "关键点：宽限期内会话与房间席位一直保留，重连可恢复到原棋盘与棋子颜色", 17, MUTED)
    save_trimmed(img, "00_reconnect.png")


def main():
    os.makedirs(OUT, exist_ok=True)
    architecture()
    sequence()
    reconnect()
    print("已生成：00_architecture.png / 00_sequence.png / 00_reconnect.png")


if __name__ == "__main__":
    main()