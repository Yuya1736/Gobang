# 网络对战游戏系统（五子棋）

2026 计算机网络课程设计 · 题目 12

基于 Python 标准库实现的 C/S 架构五子棋网络对战程序：一个服务器进程、多个客户端进程，
全部通过回环地址 `127.0.0.1` 通信。服务器是唯一的规则裁判与状态权威，客户端只负责
渲染服务器下发的房间快照并上报用户操作。

## 功能

- 注册 / 登录，PBKDF2-HMAC-SHA256 加盐存储密码，token 会话
- 大厅房间列表实时刷新，创建 / 加入 / 观战 / 离开房间
- 双人准备自动开局，15×15 五子棋完整规则（五连、平局、认输、掉线判负）
- 服务器权威实时同步：每次状态变化下发完整快照，客户端整屏重绘
- 断线重连：60 秒宽限期内凭 token 恢复席位与对局
- 房间聊天（滑动窗口限速）、观战者列表
- 简单积分（初始 1000，胜 +1，平/负 +0）、排行榜、最近对局记录
- 多桌并发一致性测试、冒烟测试、GUI 截图脚本

## 目录结构

```
网络对战游戏系统/
├── run_server.py          服务器启动入口
├── run_client.py          客户端启动入口
├── common/
│   └── protocol.py        应用层协议：单行 JSON 编解码、常量
├── server/
│   ├── app.py             服务器主程序：accept、请求分发、房间/会话/结算
│   ├── game.py            五子棋规则与胜负判定
│   ├── room.py            房间数据结构与快照
│   ├── session.py         会话对象（断线重连）
│   └── database.py        JSON 文件：账号、积分、对局记录
├── client/
│   ├── net.py             客户端网络层（请求/响应、事件、心跳）
│   └── ui.py              Tkinter 图形界面
├── tests/
│   ├── agent.py           测试用协议客户端
│   ├── smoke_test.py      全功能冒烟测试
│   ├── concurrency_test.py 多桌并发一致性测试
│   ├── demo_client.py     演示自动化客户端
│   ├── demo_launch.py     一键启动演示与截图
│   └── capture_window.py  窗口截图工具
├── docs/images/           说明书用截图
└── data/gomoku.json       运行后生成的 JSON 数据文件
```

## 环境要求

- Python 3.10 及以上（开发验证环境：Python 3.14.4，Windows 11）
- 运行时仅使用标准库：`socket`、`threading`、`json`、`tkinter`
- 生成说明书截图/文档时额外需要：`Pillow`、`python-docx`（不属于程序运行依赖）
- Windows 下建议使用 `py` 启动器；若 `python` 是应用商店占位程序，请改用 `py`

## 运行

先启动服务器（默认监听 `127.0.0.1:9527`）：

```bash
py run_server.py
```

再启动多个客户端（每个命令一个窗口，可开 2~3 个模拟玩家与观战者）：

```bash
py run_client.py
```

可选参数：

```bash
py run_server.py --host 127.0.0.1 --port 9527 --db data/gomoku.json
```

客户端服务器地址默认 `127.0.0.1:9527`，也可在登录页修改。

## 测试

```bash
# 全功能冒烟测试（自动起服务器，10 项检查）
py tests/smoke_test.py

# 多桌并发测试：默认 8 桌，每桌 2 玩家 + 1 观战者，含断线重连
py tests/concurrency_test.py --tables 8 --port 9620
```

并发测试报告写入 `tests/results/concurrency_report.json`，并验证：

- 每桌玩家与观战者的最终快照（棋盘、手数、胜者、版本号）完全一致
- 断线重连后手数不丢失、席位与棋子颜色不变
- 积分结算为胜者 +1、负者 +0（同分对局）
- 对局结束后房间全部回收、排行榜与对局记录正确

## 应用层协议摘要

单行 JSON，以 `\n` 分隔：

```
请求  {"type":"game.move","id":7,"data":{"row":7,"col":8}}
响应  {"type":"resp","id":7,"ok":true,"data":{...},"error":null}
事件  {"type":"event","event":"room_state","data":{...}}
```

请求 `id` 自增用于响应配对；事件由服务器主动推送，无 `id`。

主要请求类型：`register`、`login`、`resume`、`logout`、`rooms`、`room.create`、
`room.join`、`room.leave`、`room.ready`、`room.chat`、`game.move`、`game.resign`、
`ranking`、`ping`。

## 说明

- 所有通信仅使用回环地址，未监听公网接口。
- 数据文件默认位于项目根目录 `data/gomoku.json`，删除该文件即可重置所有账号与积分。
- 每次注册、登录、结算都会原子写回整个 JSON 文件；同一数据文件只能由一个服务器进程使用，
  多进程同时运行会互相覆盖。
- 运行日志输出到标准输出；并发测试会自动清理测试数据文件。