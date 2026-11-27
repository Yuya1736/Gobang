# -*- coding: utf-8 -*-
"""服务器启动入口：py run_server.py [--port 9527]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server.app import main

if __name__ == "__main__":
    main()