# -*- coding: utf-8 -*-
"""按窗口标题截取运行界面，用于生成说明书截图。

    py tests/capture_window.py --title "五子棋 · 网络对战 - 棋手甲" --out docs/images/room_player.png
"""
import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes

from PIL import Image, ImageGrab

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 物理像素坐标
except Exception:
    pass


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def find_windows(substring):
    found = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
            if substring in title:
                found.append((hwnd, title))
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return found


def wait_window(title, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        windows = find_windows(title)
        if windows:
            exact = [w for w in windows if w[1] == title]
            return (exact or sorted(windows, key=lambda w: len(w[1])))[0][0]
        time.sleep(0.2)
    return None


def capture_offscreen(hwnd, width, height):
    """PrintWindow 直接渲染窗口内容，不依赖窗口是否在最前。"""
    hdc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    try:
        result = user32.PrintWindow(hwnd, mem_dc, 2)  # PW_RENDERFULLCONTENT
        if not result:
            return None
        info = BITMAPINFO()
        header = info.bmiHeader
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        lines = gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
        if lines != height:
            return None
        image = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
        extrema = image.getextrema()
        if all(hi == 0 for _lo, hi in extrema):
            return None
        return image
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hdc)


def capture(title, out_path, timeout=10):
    hwnd = wait_window(title, timeout)
    if hwnd is None:
        raise SystemExit("未找到窗口: %s" % title)
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_TOPMOST
    time.sleep(0.6)

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    image = None
    if width > 100 and height > 100:
        image = capture_offscreen(hwnd, width, height)
    if image is None:
        # 后备方案：窗口已置顶，直接抓屏
        image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom),
                               all_screens=True)
    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_NOTOPMOST

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    image.save(out_path)
    print("[截图] %s -> %s (%dx%d)" % (title, out_path, image.width, image.height))
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    capture(args.title, args.out, args.timeout)


if __name__ == "__main__":
    main()