@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

rem 查找可用的 Python 解释器：优先 py 启动器，其次 python
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

rem 可选的客户端数量参数，默认启动 1 个客户端；例如：一键启动.bat 2
set "CLIENTS=%~1"
if "%CLIENTS%"=="" set "CLIENTS=1"

rem 若 9527 端口已有服务器在监听，则直接复用，避免重复启动导致端口冲突
netstat -ano | findstr ":9527 " | findstr /I "LISTENING" >nul 2>nul
if errorlevel 1 (
    echo 正在启动服务器 ...
    start "五子棋服务器" cmd /k "%PY% run_server.py"
    "%SystemRoot%\System32\timeout.exe" /t 2 /nobreak >nul
) else (
    echo 检测到 9527 端口已有服务器在运行，跳过启动服务器。
)

for /l %%i in (1,1,%CLIENTS%) do (
    echo 正在启动客户端 %%i ...
    start "五子棋客户端" cmd /k "%PY% run_client.py"
)

echo.
echo 已启动服务器与 %CLIENTS% 个客户端，请在弹出的窗口中登录。
echo 需要更多玩家或观战者时，可再次双击「启动客户端.bat」。
"%SystemRoot%\System32\timeout.exe" /t 3 /nobreak >nul
exit /b 0