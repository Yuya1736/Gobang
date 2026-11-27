# -*- coding: utf-8 -*-
"""客户端启动入口：py run_client.py（可多次运行模拟多名玩家）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from client.ui import main

if __name__ == "__main__":
    main()