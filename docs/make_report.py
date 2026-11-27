# -*- coding: utf-8 -*-
"""生成《计算机网络课程设计说明书》（题目 12：网络对战游戏系统）。

以 docs/_template.docx 为版式基础（由官方 .doc 模板转换而来），
填充封面/执行计划书/成绩评定表，插入自动目录域，
并按模板章节结构生成摘要、正文、参考文献与附录。

运行：py -X utf8 docs/make_report.py
输出：docs/计算机网络课程设计说明书.docx
"""
import json
import os

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(BASE, "docs")
IMG = os.path.join(DOCS, "images")
TEMPLATE = os.path.join(DOCS, "_template.docx")
OUTPUT = os.path.join(DOCS, "计算机网络课程设计说明书.docx")

FONT_SONG = "宋体"
FONT_HEI = "黑体"
FONT_MONO = "Consolas"

TITLE = "题目 12：网络对战游戏系统（五子棋）"


# ==================== 底层格式工具 ====================

def style_run(run, east=FONT_SONG, size=12, bold=False, color=None, mono=False):
    """统一设置中西文字体、字号与颜色（eastAsia 必须单独指定）。"""
    ascii_name = FONT_MONO if mono else "Times New Roman"
    run.font.name = ascii_name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), ascii_name)
    rfonts.set(qn("w:hAnsi"), ascii_name)
    rfonts.set(qn("w:eastAsia"), east)
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def set_cell_text(cell, text, size=10.5, bold=False,
                  align=WD_ALIGN_PARAGRAPH.LEFT):
    """覆盖单元格文字，保留首个 run 的格式。"""
    p = cell.paragraphs[0]
    p.alignment = align
    pf = p.paragraph_format
    pf.space_before = Pt(1)
    pf.space_after = Pt(1)
    pf.line_spacing = 1.0
    if p.runs:
        p.runs[0].text = text
        for extra in p.runs[1:]:
            extra._r.getparent().remove(extra._r)
    else:
        run = p.add_run(text)
        style_run(run, size=size, bold=bold)


def shade_cell(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def set_table_borders(table):
    """模板没有 Table Grid 样式，手动写入单线边框。"""
    tblpr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement("w:" + edge)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "7F7F7F")
        borders.append(el)
    tblpr.append(borders)


def replace_cell_phrase(cell, old, new):
    """在单元格段落中替换一段文字（合并单元格同样适用）。"""
    for p in cell.paragraphs:
        if old in p.text:
            full = p.text.replace(old, new)
            if p.runs:
                p.runs[0].text = full
                for extra in p.runs[1:]:
                    extra._r.getparent().remove(extra._r)
            else:
                p.add_run(full)
            return True
    return False


def replace_paragraph_text(paragraph, text):
    if paragraph.runs:
        paragraph.runs[0].text = text
        for extra in paragraph.runs[1:]:
            extra._r.getparent().remove(extra._r)
    else:
        paragraph.add_run(text)


def add_field(paragraph, instr, placeholder=""):
    """插入一个简单域（PAGE / TOC），placeholder 为域结果占位文字。"""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    run._r.append(begin)

    run = paragraph.add_run()
    instr_el = OxmlElement("w:instrText")
    instr_el.set(qn("xml:space"), "preserve")
    instr_el.text = " %s " % instr
    run._r.append(instr_el)

    run = paragraph.add_run()
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    run._r.append(sep)

    if placeholder:
        run = paragraph.add_run(placeholder)
        style_run(run, size=10.5)

    run = paragraph.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(end)


# ==================== 正文构件 ====================

def heading(text, level=1, center=False, page_break=False):
    if page_break:
        doc.add_page_break()
    p = doc.add_paragraph(style="Heading %d" % level)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
    pf = p.paragraph_format
    pf.space_before = Pt(14 if level == 1 else 8)
    pf.space_after = Pt(8 if level == 1 else 4)
    pf.line_spacing = 1.3
    pf.first_line_indent = Pt(0)
    run = p.add_run(text)
    style_run(run, east=FONT_HEI, size=16 if level == 1 else 14,
              bold=True, color=RGBColor(0, 0, 0))
    return p


def para(text="", indent=True, size=12, bold=False, align=WD_ALIGN_PARAGRAPH.LEFT,
         east=FONT_SONG, space_after=0, line=20):
    p = doc.add_paragraph()
    p.alignment = align
    pf = p.paragraph_format
    pf.line_spacing = Pt(line)
    pf.space_before = Pt(0)
    pf.space_after = Pt(space_after)
    pf.first_line_indent = Pt(24) if indent else Pt(0)
    if text:
        run = p.add_run(text)
        style_run(run, east=east, size=size, bold=bold)
    return p


def caption(text, before=6, after=4):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = 1.0
    run = p.add_run(text)
    style_run(run, size=10.5)
    return p


def add_figure(filename, cap, width_cm=14.0):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = p.paragraph_format
    pf.space_before = Pt(6)
    pf.space_after = Pt(2)
    pf.line_spacing = 1.0
    # 图与图注视为一个整体，避免图注被挤到下一页成为孤行
    pf.keep_with_next = True
    run = p.add_run()
    run.add_picture(os.path.join(IMG, filename), width=Cm(width_cm))
    caption(cap, before=2, after=8)


def add_table(headers, rows, widths, cap=None, size=10.5,
              center_cols=()):
    if cap:
        caption(cap, before=6, after=3).paragraph_format.keep_with_next = True
    table = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_table_borders(table)
    for j, text in enumerate(headers):
        cell = table.rows[0].cells[j]
        set_cell_text(cell, text, size=size, bold=True,
                      align=WD_ALIGN_PARAGRAPH.CENTER)
        shade_cell(cell, "D9E2F3")
    # 跨页时重复表头，续表不丢失列名
    trpr = table.rows[0]._tr.get_or_add_trPr()
    trpr.append(OxmlElement("w:tblHeader"))
    for i, row in enumerate(rows, start=1):
        for j, text in enumerate(row):
            align = (WD_ALIGN_PARAGRAPH.CENTER if j in center_cols
                     else WD_ALIGN_PARAGRAPH.LEFT)
            set_cell_text(table.rows[i].cells[j], str(text), size=size,
                          align=align)
    for row in table.rows:
        for j, width in enumerate(widths):
            row.cells[j].width = Cm(width)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def code_block(text):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.first_line_indent = Pt(0)
    pf.left_indent = Pt(12)
    pf.right_indent = Pt(6)
    pf.space_before = Pt(3)
    pf.space_after = Pt(6)
    pf.line_spacing = Pt(11.5)
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    ppr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F3F4F6")
    ppr.append(shd)
    lines = text.strip("\n").split("\n")
    for i, line in enumerate(lines):
        run = p.add_run(line)
        style_run(run, east=FONT_SONG, size=9, mono=True,
                  color=RGBColor(0x33, 0x33, 0x33))
        if i < len(lines) - 1:
            run.add_break()
    return p


# ==================== 前置部分 ====================

def fill_front_matter():
    """填写封面、执行计划书与成绩评定表。"""
    cover = doc.tables[0]
    # 封面题目栏宽约 8cm，12pt 冒号后放不下，改为在括号前主动换行
    set_cell_text(cover.cell(0, 2), "题目 12：网络对战游戏系统\n（五子棋）",
                  size=12, bold=True)
    set_cell_text(cover.cell(5, 2), "****", size=14)
    set_cell_text(cover.cell(6, 2), "****", size=14)
    set_cell_text(cover.cell(12, 0), "2026年12月15日", size=12,
                  align=WD_ALIGN_PARAGRAPH.CENTER)

    plan = doc.tables[1]
    set_cell_text(plan.cell(1, 2), TITLE, size=10.5, bold=True)
    replacements = [
        ("姓名1、姓名2、姓名3......", "组员一、组员二、组员三、组员四（4 人组）"),
        ("指导教师：***", "指导教师：蔡威林"),
        ("学生人数：***（班级人数）", "学生人数：4（本组）"),
    ]
    # 合并单元格会在多行/多列中重复出现，逐格尝试替换；
    # 首次命中后旧串即不存在，重复访问自然空转
    for row in plan.rows:
        for cell in row.cells:
            for old, new in replacements:
                replace_cell_phrase(cell, old, new)

    grade = doc.tables[2]
    set_cell_text(grade.cell(0, 2), "****", size=10.5)
    set_cell_text(grade.cell(0, 5), "****", size=10.5)
    set_cell_text(grade.cell(1, 3), TITLE, size=10.5, bold=True)

    # 模板由其他课程复制而来，修正计划书标题中的课程名称
    for p in doc.paragraphs:
        if p.text.strip() == "计算机组成原理课程设计执行计划书":
            replace_paragraph_text(p, "《计算机网络》课程设计执行计划书")
            break


def build_toc():
    """把模板中的静态目录替换为可自动更新的 TOC 域。"""
    dir_para = None
    for p in doc.paragraphs:
        if p.text.strip() == "目录":
            dir_para = p
            break
    if dir_para is None:
        raise RuntimeError("模板中未找到“目录”段落")

    toc_para = doc.add_paragraph()
    dir_para._p.addnext(toc_para._p)
    toc_para.paragraph_format.space_before = Pt(6)
    toc_para.paragraph_format.line_spacing = 1.0
    add_field(toc_para, 'TOC \\o "1-2" \\h \\z \\u',
              "（目录尚未更新，右键选择“更新域”或按 F9 生成）")

    for p in list(doc.paragraphs):
        if p.style is not None and p.style.name.startswith("toc"):
            p._p.getparent().remove(p._p)
    return toc_para


def trim_body(toc_para):
    """删除模板中目录之后的示例正文，保留最终 sectPr。"""
    body = doc.element.body
    sectpr = body.find(qn("w:sectPr"))
    node = toc_para._p.getnext()
    while node is not None and node is not sectpr:
        nxt = node.getnext()
        body.remove(node)
        node = nxt


# ==================== 摘要与正文 ====================

def build_body():
    # ---------- 摘要 ----------
    heading("摘要", 1, center=True)
    para("本课程设计完成了一个基于 C/S 架构的五子棋网络对战系统，服务器与多个客户端进程均"
         "运行于回环地址 127.0.0.1。服务器采用“主线程 accept + 每连接一线程”的并发模型，"
         "在 TCP 字节流之上定义了单行 JSON 应用层协议：请求报文携带自增 id 用于响应关联，"
         "服务器通过事件报文主动推送房间状态，并以单调递增的 revision 标识快照版本。"
         "每间房拥有独立的房间锁，快照在锁内原子生成、在锁外发送，既保证同一轮广播中"
         "玩家与观战者看到的棋盘完全一致，也避免慢客户端拖住其他线程。客户端基于 Tkinter "
         "实现登录、大厅、房间三层界面，网络读取线程、心跳线程与界面主线程通过队列解耦，"
         "主线程每 50 ms 排空事件队列并整体重绘服务器快照。系统实现了注册登录、房间创建/"
         "加入/观战、双人准备开局、15×15 五子棋完整规则、房间聊天、简单积分与排行榜，"
         "以及 60 秒宽限期的断线重连。测试方面，10 项冒烟检查全部通过；8 桌并发测试包含 "
         "16 名玩家与 8 名观战者、共 72 手落子，所有房间三方快照一致，平均落子时延约 "
         "4.8 ms，验证了多桌并发下状态同步的正确性与实时性。")
    kw = doc.add_paragraph()
    kw.paragraph_format.line_spacing = Pt(20)
    kw.paragraph_format.space_before = Pt(6)
    run = kw.add_run("关键词：")
    style_run(run, east=FONT_HEI, size=12, bold=True)
    run = kw.add_run("计算机网络；C/S 架构；TCP；应用层协议；多线程；五子棋；断线重连")
    style_run(run, size=12)

    # ---------- 1 问题描述 ----------
    heading("1 问题描述", 1, page_break=True)
    heading("1.1 设计题目", 2)
    para("题目 12：网络对战游戏系统。本设计以五子棋为对战载体，实现一套运行于单机回环地址的"
         "C/S 网络对战程序：一个服务器进程作为规则裁判与状态权威，多个客户端进程提供图形"
         "界面并相互对战，另可有观战者以只读方式进入同一房间。")
    heading("1.2 设计内容", 2)
    para("系统面向课程设计的完整要求，包含以下功能与质量目标：")
    para("（1）账号体系：注册、登录、登出，密码加盐哈希存储，登录后签发 token 会话。")
    para("（2）大厅与房间：实时房间列表，创建、加入、观战、离开房间，房主与席位展示。")
    para("（3）对局流程：两名玩家准备后自动开局，15×15 五子棋，黑方先行，完整实现五连获胜、"
         "平局、认输与掉线判负，并支持同一房间连续多局与先手轮换。")
    para("（4）实时同步：服务器是唯一状态权威，每次状态变化向房间全体成员广播完整快照，"
         "客户端整屏重绘；观战者与玩家的视图保持同步。")
    para("（5）互动功能：房间聊天（带限速与历史回放）、观战者列表、简单积分、排行榜与"
         "最近对局记录。")
    para("（6）可靠性：应用层心跳检测死连接；断线后 60 秒宽限期内可凭 token 恢复会话、"
         "房间席位与对局进度。")
    para("（7）可验证性：提供全功能冒烟测试与多桌并发一致性测试，输出可复现的测试数据。")
    heading("1.3 设计目的与要求", 2)
    para("通过本设计把课程中的分层体系结构、TCP 可靠传输、Socket 编程、并发控制与应用层"
         "协议设计等知识落到一个可运行的系统中，具体目标包括：理解 TCP 面向连接、可靠、"
         "字节流的特性及其对应用层帧定界的要求；掌握阻塞式 Socket 与多线程服务器的组织方式；"
         "学会自定义请求/响应/事件协议并处理并发、乱序与异常；掌握图形界面程序与网络线程的"
         "解耦方法。")
    para("工程要求：全部通信仅使用回环地址 127.0.0.1，不监听公网接口；服务器与多个客户端"
         "以独立进程运行；核心代码注释率不低于 30%；以 4 人小组分工完成编码、测试与文档，"
         "最终提交可运行源码、依赖与运行说明以及本说明书。")

    # ---------- 2 原理与设计环境 ----------
    heading("2 相关原理与设计环境", 1)
    heading("2.1 网络通信与应用层协议原理", 2)
    para("TCP 提供面向连接、可靠、全双工的字节流服务，但不保留应用消息边界。若应用层直接"
         "按“一次 send 对应一次 recv”处理，在报文合并或拆分时必然出错（即粘包与半包）。"
         "本系统采用换行符作为帧定界：每条报文是一行 UTF-8 编码的 JSON，接收端使用带缓冲的"
         "按行读取，一次读取恰好得到一条完整报文；同时对单条报文设置 1 MB 上限，超长即断开，"
         "防止恶意报文耗尽内存。JSON 具备自描述、可读、跨语言的特点，便于调试与抓包分析。")
    para("在消息语义上，系统采用“请求—响应 + 服务器事件”的混合模型。客户端请求携带自增 id，"
         "服务器响应原样回传该 id，从而在同一条 TCP 连接上实现多请求的并发关联；房间状态、"
         "聊天与对局结束等由服务器主动推送，事件不带 id，客户端按事件名分发。心跳请求每 15 秒"
         "发送一次，配合 TCP Keep-Alive，用于发现“对端已掉线但未收到 FIN”的半开连接。")
    para("断线重连依赖会话而非连接：登录时服务器签发随机 token，会话对象在连接断开后仍保留，"
         "客户端在 60 秒宽限期内用 token 重新连接并调用 resume，即可恢复原房间席位、棋子颜色"
         "与对局进度；超时未归则视为弃权，按掉线判负结算。")
    heading("2.2 多线程并发与共享状态原理", 2)
    para("服务器采用 thread-per-connection 模型：主线程只负责 accept，每个连接由独立线程"
         "读取与分发请求；房间表、会话表由服务器级 RLock 保护，每个房间另有自己的 RLock。"
         "锁的使用遵循两条规则：其一，同一临界区内不做阻塞网络 I/O，广播时先在房间锁内生成"
         "全部快照，释放锁后再逐个发送；其二，加锁顺序固定为“先服务器锁、后房间锁”，避免"
         "交叉持锁造成死锁。")
    para("服务器权威同步的核心是完整快照与版本号。一次落子会改变棋盘、轮次与手数，服务器在"
         "同一临界区内完成校验、落子、判胜、结算与快照生成，然后广播给房间全体成员。由于"
         "请求响应与事件推送可能由不同线程发出，同一条连接上的写入通过会话发送锁串行化；"
         "又因为客户端可能先收到较新的广播、后收到较旧的响应，每次广播都递增房间 revision，"
         "客户端只接受不小于当前版本的快照，从根本上避免棋盘“回退”。")
    para("持久化层使用单个 JSON 文件，Database 实例以 RLock 串行化全部读写，每次修改先写"
         "同目录临时文件并 fsync，再用 os.replace 原子替换正式文件，避免进程崩溃留下半截"
         " JSON；账号密码采用 PBKDF2-HMAC-SHA256（12 万次迭代、随机盐）存储，校验使用"
         "恒定时间比较，任何接口都不返回密码哈希与盐。")
    heading("2.3 设计环境", 2)
    add_table(
        ["项目", "配置"],
        [
            ["操作系统", "Windows 11（64 位）"],
            ["开发语言", "Python 3.14.4（使用 py 启动器）"],
            ["运行时依赖", "标准库：socket、threading、json、tkinter"],
            ["文档工具", "Pillow（示意图）、python-docx（本说明书）"],
            ["网络环境", "回环地址 127.0.0.1，默认端口 9527"],
            ["数据存储", "JSON 文件，默认 data/gomoku.json（原子写回）"],
            ["测试工具", "自研协议客户端与并发测试脚本"],
        ],
        widths=[3.4, 10.6], cap="表 2-1  设计环境",
        center_cols=(0,))

    # ---------- 3 概要设计 ----------
    heading("3 概要设计", 1)
    heading("3.1 总体架构", 2)
    para("系统由三类进程构成：服务器进程、客户端进程与可选的观战客户端进程，全部通过回环"
         "地址通信。服务器内部自上而下分为网络层（accept 循环与连接线程）、业务层（请求"
         "分发与各处理器）、状态层（房间、对局、会话）与持久层（JSON 文件）。客户端分为界面层"
         "与网络层：界面层负责渲染与交互，网络层负责请求/响应关联、事件接收与心跳。总体"
         "结构如图 3-1 所示。")
    add_figure("00_architecture.png", "图 3-1  系统总体架构", 14.4)
    para("服务器为每个房间维护独立的对局对象与房间锁，多桌可以并发推进而互不阻塞；大厅列表"
         "只暴露房间公开信息，棋盘与聊天仅在房间内广播。所有客户端只能访问 127.0.0.1，满足"
         "课程设计在本机以多进程模拟网络对战的要求。")
    heading("3.2 模块划分", 2)
    para("工程按职责划分为协议、服务器、客户端与测试四组模块，主要文件及规模见表 3-1。"
         "核心业务模块（协议、服务器各模块与客户端网络层）注释率约 31%，界面代码约 18%，"
         "注释重点说明线程模型、锁约定、协议语义与非显然的边界条件。")
    add_table(
        ["文件", "行数", "主要职责"],
        [
            ["common/protocol.py", "79", "协议常量、单行 JSON 编解码、报文长度限制"],
            ["server/app.py", "943", "accept 循环、请求分发、房间/会话/结算管理"],
            ["server/game.py", "171", "棋盘状态、落子校验、五连判定与认输"],
            ["server/room.py", "211", "房间席位、准备状态、观战者、聊天与快照"],
            ["server/session.py", "104", "会话对象、断线标记、重连绑定与发送串行化"],
            ["server/database.py", "349", "账号、简单积分、排行榜与对局记录"],
            ["client/net.py", "218", "客户端连接、接收线程、请求关联、心跳"],
            ["client/ui.py", "1498", "Tkinter 三视图界面、棋盘绘制与事件处理"],
            ["run_server.py / run_client.py", "10 / 10", "服务器与客户端启动入口"],
            ["tests/", "约 990", "协议客户端、冒烟测试、并发测试与演示脚本"],
        ],
        widths=[4.6, 2.0, 7.6], cap="表 3-1  模块划分",
        center_cols=(1,))
    heading("3.3 应用层协议设计", 2)
    para("协议报文分为请求、响应与事件三类，统一为单行 JSON（以 \\n 结尾）：")
    code_block('''请求  {"type":"game.move","id":7,"data":{"row":7,"col":8}}
响应  {"type":"resp","id":7,"ok":true,"data":{...},"error":null}
事件  {"type":"event","event":"room_state","data":{...}}''')
    para("请求的 type 表示操作类型，id 由客户端自增分配，data 为参数对象；响应以 type=resp "
         "和相同 id 关联请求，ok 表示成功与否，失败时 error 给出可展示的中文原因；事件由"
         "服务器主动推送，event 字段为事件名。主要请求类型见表 3-2。")
    add_table(
        ["请求类型", "方向", "说明"],
        [
            ["register / login", "客户端→服务器", "注册账号 / 登录并签发 token 会话"],
            ["resume", "客户端→服务器", "凭 token 恢复会话，返回所在房间快照"],
            ["logout", "客户端→服务器", "主动登出并销毁会话"],
            ["rooms", "客户端→服务器", "获取大厅房间列表"],
            ["room.create", "客户端→服务器", "创建房间，创建者占 0 号席位"],
            ["room.join", "客户端→服务器", "以 player 或 spectator 身份加入"],
            ["room.leave / room.ready", "客户端→服务器", "离开房间 / 切换准备状态"],
            ["room.chat", "客户端→服务器", "发送房间聊天（滑动窗口限速）"],
            ["game.move / game.resign", "客户端→服务器", "落子 / 认输"],
            ["ranking", "客户端→服务器", "排行榜与最近对局（limit 可选）"],
            ["ping", "客户端→服务器", "心跳，刷新会话活跃时间"],
            ["room_state / chat / game_start", "服务器→客户端", "完整房间快照 / 聊天 / 开局事件"],
            ["game_over / room_closed / kicked", "服务器→客户端", "结算结果 / 房间关闭 / 被踢下线"],
        ],
        widths=[4.6, 3.4, 6.2], cap="表 3-2  主要请求类型与事件",
        size=9.5)

    # ---------- 4 详细设计 ----------
    heading("4 详细设计", 1, page_break=True)
    heading("4.1 服务器连接管理与请求分发", 2)
    para("服务器启动后绑定回环地址并进入 accept 循环，为每个新连接设置读超时与 TCP "
         "Keep-Alive，随后启动一个守护线程执行读取循环。读取循环按行读取请求，单条报文"
         "格式错误只返回错误响应而不断开连接，超长报文则直接断开；连接结束（正常关闭、"
         "异常或巡检关闭）统一进入断开处理，标记会话离线并启动宽限期计时器。")
    para("所有请求由 self.handlers 字典映射到处理函数，统一签名为 (session, data, conn)。"
         "分发器负责校验登录态（register、login、resume、ping 允许匿名）、调用处理器、"
         "把业务异常转换为 ok=false 的响应，并对未预期异常记录堆栈而不向客户端泄露内部"
         "细节。注册、登录、重连、心跳之外的请求如果没有会话，一律返回“请先登录”。")
    code_block('''handler = self.handlers.get(msg_type)
if handler is None:
    raise ServerError("未知请求类型: %s" % msg_type)
session = self.conn_sessions.get(conn)
if msg_type not in self.AUTH_FREE and session is None:
    raise ServerError("请先登录")
result = handler(session, data, conn) or {}
self._reply(conn, req_id, True, result)''')
    heading("4.2 房间模型与状态同步", 2)
    para("房间对象包含固定两个玩家席位（seat 0/1）、观战者列表、聊天环形队列（上限 100 条）、"
         "当前对局、局号与已结算局号，以及快照版本号 revision。房间状态由对局推导：无对局为"
         "等待、对局未结束为对战中、对局结束为已结束，从而避免状态字段与对局状态不一致。"
         "玩家席位只决定顺序，棋子颜色每局按局号轮换，保证连续对局时双方先手机会均等。")
    para("每次状态变化都调用 push_room 广播完整快照。快照以观察者为中心：除棋盘、轮次、"
         "双方席位与准备状态外，还包含 you 字段说明当前用户是玩家还是观战者、执何颜色，"
         "客户端据此决定界面按钮与视角。聊天历史只在加入或重连时随快照下发，之后由 chat "
         "事件增量追加，避免每条状态报文重复携带全部历史。关键代码如下：")
    code_block('''def push_room(self, room):
    with room.lock:
        # 每次广播递增版本号，保证客户端能识别并丢弃迟到的旧快照
        room.revision += 1
        members = [s.username for s in room.players] + list(room.spectators)
        snapshots = {name: room.snapshot(name, include_chat=False)
                     for name in members}
    for name, snap in snapshots.items():
        sess = self.online.get(name)
        if sess is not None and sess.connected:
            sess.send({"type": "event", "event": "room_state", "data": snap})
    self.broadcast_lobby()''')
    para("快照在锁内一次性生成，网络发送移到锁外，慢客户端不会阻塞其他线程；离线成员被"
         "跳过，其快照由重连时的 resume 响应补发。房间状态变化同时刷新大厅列表，使“等待中”"
         "与“对战中”的展示及时更新。")
    heading("4.3 对局规则与胜负判定", 2)
    para("对局对象保存 15×15 棋盘、当前轮次、走子历史、最后落点、胜者与五连坐标。落子时"
         "依次校验：对局是否已结束、席位是否合法、是否轮到该席位、坐标是否在棋盘内、目标点"
         "是否为空；任一失败抛出 MoveError，由分发器转换为错误响应。合法落子后只从最后落子"
         "点沿横、竖、主对角线、副对角线四个方向正反延伸统计同色棋子，长度达到 5 即获胜，"
         "复杂度为 O(1)，无需全盘扫描；棋盘下满且无人五连则判平局。认输与掉线判负都只设置"
         "终局字段，积分结算统一由上层完成；forfeit 具有幂等性，可被宽限期定时器与巡检线程"
         "重复触发而不重复结算。")
    code_block('''def _find_five(self, row, col, stone):
    for dr, dc in DIRECTIONS:
        cells = [(row, col)]
        r, c = row + dr, col + dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE \\
                and self.board[r][c] == stone:
            cells.append((r, c))
            r, c = r + dr, c + dc
        r, c = row - dr, col - dc
        while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE \\
                and self.board[r][c] == stone:
            cells.insert(0, (r, c))
            r, c = r - dr, c - dc
        if len(cells) >= 5:
            return cells
    return []''')
    heading("4.4 客户端设计与界面渲染", 2)
    para("客户端网络层包含一个接收线程与一个心跳线程。接收线程按行解析报文：带 id 的响应"
         "放入对应请求的等待队列，唤醒发起请求的工作线程；事件放入事件队列，交由界面主线程"
         "处理。请求方法为同步 RPC 语义：分配自增 id、注册等待队列、发送报文并在超时内等待"
         "响应。连接建立后关闭 Nagle 算法（TCP_NODELAY），使落子等小报文立即发出。")
    para("Tkinter 控件只能在主线程操作，因此界面采用“后台工作线程 + 队列回调”的结构："
         "所有网络请求在工作线程执行，结果与异常投递到 ui_queue；主线程每 50 ms 排空事件"
         "队列与回调队列并刷新界面。收到房间快照后整屏重绘，不做增量推断，只丢弃 revision "
         "更旧的快照。棋盘画布边长 600 像素，按 15×15 交叉点绘制木色背景、网格与星位，"
         "鼠标悬停显示落点提示，点击最近交叉点后上报坐标。")
    code_block('''def apply(self, snapshot):
    current = self.snapshot
    if current is not None and current.get("room_id") == snapshot.get("room_id") \\
            and snapshot.get("revision", 0) < current.get("revision", 0):
        # 请求响应可能比后续事件推送更晚到达，丢弃过期快照避免棋盘回退
        return
    self.snapshot = snapshot
    # ... 依据快照整体重绘棋盘、玩家卡片、按钮与聊天区''')
    heading("4.5 断线重连与会话状态机", 2)
    para("连接断开时，服务器只把会话标记为离线并保留房间席位与对局，同时启动 60 秒宽限期"
         "定时器；客户端检测到 __disconnected__ 事件后，在 45 秒内按 1.5 秒间隔反复连接并"
         "调用 resume。重连成功时服务器取消定时器、绑定新连接，返回当前房间的完整快照，"
         "客户端据此直接回到原对局界面；宽限期满仍未重连，则掉线方判负、结算积分、释放席位"
         "并销毁会话。状态机如图 4-2 所示，完整对局时序如图 4-1 所示。")
    add_figure("00_sequence.png", "图 4-1  一次完整对局的通信时序", 14.4)
    add_figure("00_reconnect.png", "图 4-2  断线重连与会话状态机", 13.5)
    para("重连实现的关键是“先取消定时器、再关闭旧连接”：定时器若不取消，到点会把刚恢复的"
         "会话判负；旧连接关闭处理中会依据连接对象判断，不会误伤已改绑的新连接。")
    heading("4.6 数据存储与积分计算", 2)
    para("持久化采用单个 JSON 文件，根对象包含 version、自增序号与 users、matches 两个数组："
         "users 保存账号、密码哈希、盐、积分与胜负平场次，matches 保存每局的对局记录。"
         "密码使用 PBKDF2-HMAC-SHA256（12 万次迭代、16 字节随机盐）派生，登录校验采用"
         "恒定时间比较；对外接口只返回 id、用户名、积分与战绩，哈希与盐不出数据层。")
    add_table(
        ["字段路径", "类型", "说明"],
        [
            ["users[].username", "string", "用户名，加载与注册时检查唯一性"],
            ["users[].password_hash / salt", "string", "PBKDF2 派生值 / 随机盐（十六进制）"],
            ["users[].score / wins / losses / draws", "number", "积分（初始 1000）与胜负平场次"],
            ["matches[].room_name / black_user / white_user", "string", "对局房间与双方用户名"],
            ["matches[].winner / move_count / reason", "string / number", "胜者、手数与结束原因"],
            ["matches[].ended_at", "string", "结束时间"],
        ],
        widths=[6.4, 3.0, 4.8], cap="表 4-1  数据文件字段结构",
        size=9.5)
    para("积分采用简单累计制：初始 1000 分，胜方 +1、负方 +0、平局双方 +0，积分只反映"
         "累计胜场，不参考对手强度。积分以整数存储，每次结算与胜负平场次、对局记录一起"
         "原子写回 JSON 文件，避免积分与记录不一致。排行榜按积分降序、胜场降序与用户名"
         "排序，默认返回 20 条，可通过 limit 参数放大到 100 条。")

    # ---------- 5 测试与分析 ----------
    heading("5 测试与分析", 1, page_break=True)
    heading("5.1 功能测试", 2)
    para("冒烟测试脚本自动启动独立端口的服务器与测试数据库，使用协议客户端依次模拟两名"
         "玩家与一名观战者，覆盖认证、房间、对局、观战同步、断线重连与排行榜等主流程，"
         "共 10 项检查，结果见表 5-1。")
    add_table(
        ["序号", "测试项", "结果"],
        [
            ["1", "注册、登录并签发 token", "通过"],
            ["2", "创建房间、加入、观战与准备", "通过，黑方为房主甲选手"],
            ["3", "五连胜负判定与积分结算", "通过，胜者 +1、负者 +0"],
            ["4", "观战者实时同步", "通过，快照与玩家一致"],
            ["5", "第二局开局与先手轮换", "通过，黑方席位变为 1"],
            ["6", "断线后凭 token 恢复对局", "通过，恢复第 2 局 0 手状态"],
            ["7", "重连后继续落子", "通过"],
            ["8", "排行榜积分排序", "通过，1001 / 1000 / 1000 降序"],
            ["9", "非法落子校验", "通过，提示该位置已有棋子"],
            ["10", "认输与离开房间", "通过，席位与房间正确回收"],
        ],
        widths=[1.4, 7.2, 5.6], cap="表 5-1  冒烟测试结果",
        center_cols=(0,))
    para("10 项检查全部通过，说明服务器与客户端之间的协议实现、规则判定与并发边界在主流程"
         "上行为正确。")

    heading("5.2 并发一致性测试", 2)
    para("并发测试默认创建 8 张桌子，每桌 2 名玩家与 1 名观战者；所有玩家线程通过栅栏同步"
         "后按脚本落子，每桌在 9 手内形成五连。第 0 桌在第 5 手后主动断开一名玩家，并用"
         "token 重连，验证重连不丢手数、席位与颜色不变。测试结束后核对每桌玩家、观战者与"
         "重连客户端收到的最终快照，要求棋盘、手数、胜者、版本号完全一致，并检查积分结算"
         "与房间回收。逐桌结果见表 5-2。")
    report = json.load(open(os.path.join(BASE, "tests", "results",
                                         "concurrency_report.json"),
                            encoding="utf-8"))
    rows = []
    for item in report["results"]:
        rows.append([
            item["table"], item["moves"], "%.3f" % item["seconds"],
            "%.2f" % item["avg_latency_ms"], "%.2f" % item["max_latency_ms"],
            item["revision"], "是" if item["reconnect"] else "否",
        ])
    add_table(
        ["桌号", "手数", "用时/s", "平均时延/ms", "最大时延/ms", "版本号", "断线重连"],
        rows,
        widths=[1.3, 1.3, 2.0, 2.5, 2.5, 2.2, 2.6],
        cap="表 5-2  8 桌并发测试逐桌结果", size=9.5,
        center_cols=(0, 1, 2, 3, 4, 5, 6))
    para("全部 %(tables)d 桌、%(players)d 名玩家与 %(spectators)d 名观战者共完成 "
         "%(moves)d 手落子，总耗时 %(sec).3f s，平均落子时延 %(avg).1f ms，各桌平均时延的 "
         "95 分位为 %(p95).1f ms。" % {
             "tables": report["tables"], "players": report["players"],
             "spectators": report["spectators"], "moves": report["total_moves"],
             "sec": report["total_seconds"], "avg": report["avg_move_latency_ms"],
             "p95": report["p95_table_avg_latency_ms"]})
    para("一致性核对结果：每桌玩家与观战者的最终快照（棋盘、手数、胜者、revision）完全一致；"
         "第 0 桌重连前后手数未丢失，revision 为 16（其余桌为 14，恰好等于重连额外触发的"
         "两次广播）；8 局对局记录均已写入数据文件，房间在全员离开后全部回收；同分对局胜者"
         "+1、负者 +0。测试结论为“全部一致”。")
    heading("5.3 界面展示", 2)
    para("客户端界面分为登录、大厅与房间三层，房间视图同时支持玩家与观战者两种视角。"
         "运行截图如下。")
    add_figure("01_login.png", "图 5-1  登录界面（可修改服务器地址）", 12.6)
    add_figure("02_lobby.png", "图 5-2  大厅房间列表与房间操作", 12.6)
    add_figure("03_room_player.png", "图 5-3  玩家对局界面（黑方视角）", 12.6)
    add_figure("04_room_spectator.png", "图 5-4  观战者界面（同一棋盘实时同步）", 12.6)
    add_figure("05_ranking.png", "图 5-5  积分排行榜与胜负场次", 11.2)
    heading("5.4 结果分析", 2)
    para("在回环环境下，一次落子的端到端时延由客户端排队、JSON 编解码、服务器加锁校验、"
         "快照生成与广播、客户端重绘组成。实测 8 桌并发时平均时延约 4.8 ms，最大单次时延"
         "约 13 ms，说明锁粒度较细、临界区较短；每桌的版本号严格随广播递增，未发现快照"
         "乱序或回退。")
    para("系统的瓶颈主要在三处：一是线程模型，连接数达到数百后线程调度与栈内存开销会上升，"
         "可改为 selectors/异步 I/O；二是完整快照广播，房间成员越多、报文越大，当前 2 名"
         "玩家加若干观战者的规模没有问题，但更大规模可改为增量或按观察者裁剪；三是 JSON 文件"
         "全量写回，写操作虽只发生在注册与结算等低频路径，但数据量增大后单次写盘成本会上升；"
         "若改为高并发持久化，应使用 SQLite 或独立数据库服务。此外，当前协议未启用 TLS，仅在回环地址"
         "运行，若部署到局域网需要补充加密与鉴权。")

    # ---------- 6 设计总结 ----------
    heading("6 设计总结", 1)
    heading("6.1 小组分工", 2)
    para("本设计为 4 人小组，按“协议与服务器框架、服务器业务、客户端、测试与文档”四条线"
         "分工，组内接口以协议报文与模块函数为边界，联调通过冒烟测试驱动。分工见表 6-1。")
    add_table(
        ["成员", "角色", "主要工作", "占比"],
        [
            ["组员一（组长）", "协议与服务器框架",
             "应用层协议、accept 与连接线程、请求分发、会话与断线重连、并发模型设计",
             "25%"],
            ["组员二", "服务器业务",
             "房间与席位管理、五子棋规则与胜负判定、积分结算、JSON 账号与排行榜",
             "25%"],
            ["组员三", "客户端开发",
             "Tkinter 三视图界面、棋盘绘制与鼠标交互、网络线程与界面队列解耦、自动重连",
             "25%"],
            ["组员四", "测试与文档",
             "冒烟与并发测试、截图与演示脚本、运行说明、说明书与示意图撰写",
             "25%"],
        ],
        widths=[2.9, 3.3, 6.9, 1.3], cap="表 6-1  小组分工",
        size=9.5, center_cols=(3,))
    heading("6.2 主要问题与解决方法", 2)
    para("（1）TCP 粘包与半包。最初按定长读取，不同长度的 JSON 报文会互相粘连。改为"
         "makefile(\"rb\") 配合 readline 按换行切分，并限制单行最大长度为 1 MB，问题彻底消除。")
    para("（2）响应与事件并发写导致字节流交错。同一连接的响应由处理线程发送、事件由广播"
         "线程发送，曾出现两条 JSON 拼在一起导致客户端解析失败。为每个会话增加发送锁，"
         "所有写操作统一经过会话发送方法串行化。")
    para("（3）广播快照乱序造成棋盘回退。落子响应可能比随后的 room_state 事件更晚到达，"
         "客户端用旧快照覆盖新棋盘。引入房间 revision，每次广播递增，客户端只接受版本不"
         "低于当前值的快照。")
    para("（4）掉线判负与重连的竞态。宽限期定时器可能在重连成功后仍触发，或与巡检线程"
         "同时判负。解决方法是：重连绑定新连接时先取消定时器；判负方法具备幂等性；同一局"
         "通过 settled_serial 保证只结算一次。")
    para("（5）关闭连接时的死锁。接收线程阻塞在 readline，主线程直接关闭 BufferedReader "
         "会等待读锁而卡死。改为先 shutdown 再 close 套接字唤醒读线程，并让接收线程自行"
         "退出。")
    para("（6）Tkinter 线程安全。网络线程直接更新控件会偶发崩溃。改为网络线程只投递队列，"
         "主线程每 50 ms 通过 after 回调统一刷新界面，界面与网络彻底解耦。")
    heading("6.3 总结与体会", 2)
    para("本设计把课堂上的分层模型、TCP 可靠传输与 Socket 编程落到了一个完整可运行的系统"
         "中，最有价值的收获是对“状态权威”与“并发一致性”的理解：只有当服务器在锁内完成"
         "校验与快照生成、客户端无条件服从快照并防御乱序时，多端界面才能稳定一致。同时，"
         "协议设计要预留错误语义与长度边界，断线重连要以会话为单位而非连接，界面线程绝不"
         "直接做网络 I/O，这些都是调试过程中反复验证得到的经验。")
    para("不足与改进方向：当前服务器使用线程模型，连接规模受限；完整快照在房间较大时存在"
         "冗余；协议未加密且仅支持 IPv4 回环。后续可改用异步 I/O、按观察者裁剪快照，并"
         "为局域网部署补充 TLS 与连接鉴权。")

    # ---------- 参考文献 ----------
    heading("参考文献", 1, page_break=True)
    refs = [
        "[1] 谢希仁. 计算机网络（第 8 版）. 北京: 电子工业出版社, 2021.",
        "[2] 吴功宜, 吴英. 计算机网络（第 4 版）. 北京: 清华大学出版社, 2018.",
        "[3] Python Software Foundation. Python 3 Documentation: socket, threading, "
        "json, tkinter. https://docs.python.org/3/",
        "[4] 《计算机网络》课程设计任务书与选题汇总. 安徽理工大学, 2026.",
    ]
    for ref in refs:
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.line_spacing = Pt(20)
        pf.first_line_indent = Pt(0)
        pf.left_indent = Pt(0)
        run = p.add_run(ref)
        style_run(run, size=10.5)

    # ---------- 附录 ----------
    heading("附录 A 运行与部署说明", 1, page_break=True)
    para("服务器默认监听 127.0.0.1:9527，数据文件位于 data/gomoku.json。启动命令如下：")
    code_block('''# 启动服务器（先运行）
py run_server.py

# 再启动多个客户端（可多开，模拟多名玩家与观战者）
py run_client.py

# 可选参数
py run_server.py --host 127.0.0.1 --port 9527 --db data/gomoku.json''')
    para("运行环境为 Python 3.10 及以上，程序仅依赖标准库；生成说明书插图与 Word 文档时"
         "额外需要 Pillow 与 python-docx。删除 data/gomoku.json 即可重置所有账号与积分。"
         "测试命令：")
    code_block('''# 全功能冒烟测试（自动启动服务器，10 项检查）
py tests/smoke_test.py

# 多桌并发测试：默认 8 桌，每桌 2 玩家 + 1 观战者
py tests/concurrency_test.py --tables 8 --port 9620''')

    heading("附录 B 协议报文与关键代码", 1)
    para("一次登录与创建房间的报文交互示例（省略中间事件）：")
    code_block('''C→S  {"type":"login","id":1,"data":{"username":"甲","password":"******"}}
S→C  {"type":"resp","id":1,"ok":true,
      "data":{"token":"9f2c...","user":{"username":"甲","score":1000,...}}}
C→S  {"type":"room.create","id":2,"data":{"name":"交流房"}}
S→C  {"type":"resp","id":2,"ok":true,"data":{"room_id":1}}
S→C  {"type":"event","event":"room_state",
      "data":{"room_id":1,"revision":1,"game":null,...}}''')
    para("会话重连的核心实现（server/session.py）：")
    code_block('''def rebind(self, conn):
    """断线重连成功：绑定新连接并恢复在线状态。"""
    old = self.conn
    self.conn = conn
    self.connected = True
    self.disconnected_at = None
    self.last_seen = time.time()
    # 先取消宽限期定时器，否则到点会把刚恢复的会话判负
    if self.grace_timer is not None:
        self.grace_timer.cancel()
        self.grace_timer = None
    if old is not None and old is not conn:
        try:
            old.close()
        except OSError:
            pass''')
    para("帧定界与 JSON 编解码（common/protocol.py）：")
    code_block('''def encode_message(obj):
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    data = raw.encode("utf-8")
    if len(data) > MAX_LINE_BYTES:
        raise ProtocolError("报文超过最大长度限制")
    return data + b"\\n"

def decode_message(line):
    text = line.decode("utf-8").strip()
    obj = json.loads(text)        # 失败时抛出 ProtocolError
    if not isinstance(obj, dict):
        raise ProtocolError("报文必须是 JSON 对象")
    return obj''')

    heading("附录 C 测试输出", 1)
    para("冒烟测试标准输出（已通过）：")
    code_block('''[1] 注册登录 OK
[2] 房间/观战/准备 OK, 黑方=甲选手
[3] 胜负判定/积分结算 OK: 甲选手 胜, 积分 {'甲选手': {'score': 1001, 'delta': 1}, '乙选手': {'score': 1000, 'delta': 0}}
[4] 观战同步 OK
[5] 第二局开局 OK, 黑方席位=1（先手已轮换）
[6] 断线重连恢复对局 OK（房间 1, 第 2 局, 0 手）
[7] 重连后继续对局 OK
[8] 排行榜 OK: 甲选手 1001, 乙选手 1000, 观众丙 1000
[9] 非法落子校验 OK: 该位置已有棋子
[10] 认输/离开房间 OK

全部冒烟测试通过''')
    para("并发测试汇总（tests/results/concurrency_report.json）：")
    summary = {k: report[k] for k in (
        "tables", "players", "spectators", "total_moves",
        "max_concurrent_rooms", "total_seconds", "avg_move_latency_ms",
        "p95_table_avg_latency_ms", "consistency", "match_records")}
    code_block(json.dumps(summary, ensure_ascii=False, indent=2))


# ==================== 主流程 ====================

def main():
    global doc
    doc = Document(TEMPLATE)
    fill_front_matter()
    toc_para = build_toc()
    trim_body(toc_para)
    build_body()

    doc.core_properties.title = "《计算机网络》课程设计说明书"
    doc.core_properties.subject = TITLE
    doc.core_properties.author = "软件工程24-X班 课程设计小组"
    doc.save(OUTPUT)
    print("已生成：%s" % OUTPUT)
    return OUTPUT


def update_fields(path):
    """用 Word 更新目录域、页码域并重新分页，使 TOC 带真实页码。"""
    try:
        import win32com.client as win32
    except ImportError:
        print("警告：未安装 pywin32，目录需在 Word 中按 F9 手动更新")
        return
    word = win32.gencache.EnsureDispatch("Word.Application")
    word.Visible = False
    try:
        document = word.Documents.Open(os.path.abspath(path))
        document.Fields.Update()
        for toc in document.TablesOfContents:
            toc.Update()
        document.Repaginate()
        document.Save()
        document.Close(False)
        print("已更新目录与页码域")
    finally:
        word.Quit()


if __name__ == "__main__":
    out = main()
    update_fields(out)