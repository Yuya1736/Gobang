@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [错误] 未找到可用的 Python 3，请先安装 Python 并加入 PATH。
    pause
    exit /b 1
)

echo 五子棋客户端启动中 ...
%PY% run_client.py %*

echo.
echo 客户端已退出，按任意键关闭窗口。
pause >nul
exit /b 0