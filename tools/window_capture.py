"""开发辅助：抓取本工具自身窗口的截图（Windows GDI + 标准库 PNG 编码）。

**不是运行时依赖**，仅用于「改完界面后留证」这一验证环节，可随时删除。

为什么不用外部抓屏脚本（如 PowerShell + Get-Process 取 MainWindowHandle）：
Tk 的 ``ttk.Combobox`` 会额外创建一个标题为 ``popdown`` 的顶层浮层，外部脚本按
「进程主窗口」挑选时可能选中它，从而抓到错误窗口（实测已发生过）。
本模块在**进程内**用 Tk 自身的 ``winfo_id()`` 向上找到带标题的顶层窗口，
再用 ``BitBlt`` + ``GetDIBits`` 取像素，用 ``zlib`` 手写 PNG，无第三方依赖。

用法::

    import window_capture
    hwnd = window_capture.top_level_hwnd(app.winfo_id())
    window_capture.capture(hwnd, "shot.png")
"""
from __future__ import annotations

import ctypes
import struct
import zlib
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def top_level_hwnd(hwnd: int) -> int:
    """由 Tk 控件句柄向上找到带标题的真正顶层窗口。"""
    cursor = int(hwnd)
    for _ in range(8):
        if user32.GetWindowTextLengthW(cursor) > 0:
            return cursor
        parent = user32.GetParent(cursor)
        if not parent:
            break
        cursor = parent
    return int(hwnd)


def capture(hwnd: int, path: str) -> tuple[int, int]:
    """把窗口像素写成 PNG，返回 (宽, 高)。

    调用前须已声明 DPI 感知（见 ``src.app.enable_dpi_awareness``），
    否则 ``GetWindowRect`` 与 ``BitBlt`` 的坐标口径不一致会截歪。
    """
    rect = RECT()
    if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        raise RuntimeError("GetWindowRect 失败")
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError(f"窗口尺寸无效：{width}x{height}")

    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, rect.left, rect.top, SRCCOPY)

    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height  # 负值 = 自上而下
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0  # BI_RGB

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(0, screen_dc)

    data = buffer.raw
    stride = width * 4
    rows = []
    for y in range(height):
        src = data[y * stride : (y + 1) * stride]
        row = bytearray(width * 3)
        row[0::3] = src[2::4]  # BGRA -> RGB
        row[1::3] = src[1::4]
        row[2::3] = src[0::4]
        rows.append(b"\x00" + bytes(row))

    _write_png(path, width, height, b"".join(rows))
    return width, height


def _write_png(path: str, width: int, height: int, raw: bytes) -> None:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8bit truecolor
    with open(path, "wb") as handle:
        handle.write(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b"")
        )
