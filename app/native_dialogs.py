from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def select_json_file() -> str:
    """Show the built-in Windows file chooser without Tk or upload middleware."""
    if os.name != "nt":
        raise RuntimeError("文件选择按钮目前仅支持 Windows 安装版")
    file_buffer = ctypes.create_unicode_buffer(32768)
    dialog = OPENFILENAMEW()
    dialog.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    dialog.lpstrFilter = "JSON 文件 (*.json)\0*.json\0所有文件 (*.*)\0*.*\0\0"
    dialog.nFilterIndex = 1
    dialog.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
    dialog.nMaxFile = len(file_buffer)
    dialog.lpstrTitle = "选择 Google 服务账号 JSON"
    dialog.lpstrDefExt = "json"
    dialog.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008

    chooser = ctypes.windll.comdlg32.GetOpenFileNameW
    chooser.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    chooser.restype = wintypes.BOOL
    if chooser(ctypes.byref(dialog)):
        return file_buffer.value
    error_code = ctypes.windll.comdlg32.CommDlgExtendedError()
    if error_code:
        raise OSError(error_code, "Windows 文件选择窗口发生错误")
    return ""
