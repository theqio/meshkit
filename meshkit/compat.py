# -*- coding: utf-8 -*-
"""兼容层：Python 2.7 (IronPython) / Python 3 双兼容的小工具集合。

Mechanical 2022 R1 用的是 IronPython 2.7.4，因此：
  * 不用 f-string、不用 typing、不用 dataclasses；
  * ``unicode`` / ``basestring`` 只在 Py2 存在，这里统一封装；
  * 所有除法模块内显式 ``from __future__ import division``。
"""
from __future__ import division

import sys
import os
import time
import math
import datetime
import traceback

try:  # Python 2 / IronPython
    text_type = unicode            # noqa: F821
    string_types = (str, unicode)  # noqa: F821
    integer_types = (int, long)    # noqa: F821
    PY2 = True
except NameError:  # Python 3
    text_type = str
    string_types = (str,)
    integer_types = (int,)
    PY2 = False


# --------------------------------------------------------------------------
# 文本与数值
# --------------------------------------------------------------------------
def to_text(value):
    """任何对象 -> unicode 文本，永不抛异常。"""
    if value is None:
        return u""
    if isinstance(value, text_type):
        return value
    try:
        return text_type(value)
    except Exception:
        try:
            return text_type(repr(value))
        except Exception:
            return u"<unprintable>"


def fmt_num(value, digits=4, none_text=u"-"):
    """数值格式化：整数不带小数点，浮点按有效位裁剪。"""
    if value is None:
        return none_text
    try:
        f = float(value)
    except Exception:
        return to_text(value)
    if math.isnan(f) or math.isinf(f):
        return to_text(f)
    if abs(f - round(f)) < 1e-12 and abs(f) < 1e15:
        return u"%d" % int(round(f))
    if abs(f) >= 1e5 or (abs(f) < 1e-3 and f != 0):
        return u"%.3e" % f
    text = u"%.*f" % (digits, f)
    text = text.rstrip(u"0").rstrip(u".")
    return text if text else u"0"


def to_float(value, default=None):
    """尽量把 Quantity / 字符串 / 数值 转成 float。"""
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        pass
    for attr in ("Value", "Magnitude"):
        try:
            return float(getattr(value, attr))
        except Exception:
            pass
    try:
        return float(to_text(value).strip())
    except Exception:
        return default


# --------------------------------------------------------------------------
# 时间
# --------------------------------------------------------------------------
def now():
    return datetime.datetime.now()


def stamp():
    """文件名友好时间戳：20261116_124501"""
    return now().strftime("%Y%m%d_%H%M%S")


def hms():
    return now().strftime("%H:%M:%S")


def elapsed_text(seconds):
    seconds = float(seconds or 0)
    if seconds < 90:
        return u"%.1f 秒" % seconds
    if seconds < 5400:
        return u"%.1f 分钟" % (seconds / 60.0)
    return u"%.2f 小时" % (seconds / 3600.0)


# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------
class Logger(object):
    """同时写 Mechanical 控制台 与 文本文件（UTF-8）。

    Mechanical 的 print 会进 Messages / 控制台面板；写文件保证事后可查。
    IronPython 下 print 偶发编码异常，故统一走 ``_console_write``。
    """

    LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")

    def __init__(self, path=None, verbose=True):
        self.path = path
        self.verbose = verbose
        self.lines = []
        self._sw = None
        if path:
            self._open(path)

    def _open(self, path):
        try:
            folder = os.path.dirname(os.path.abspath(path))
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
        except Exception:
            pass
        try:
            import System  # IronPython / .NET
            from System.Text import Encoding
            self._sw = System.IO.StreamWriter(path, False, Encoding.UTF8)
        except Exception:
            self._sw = None
        if self._sw is None:
            try:
                import io
                self._sw = io.open(path, "w", encoding="utf-8")
            except Exception:
                self._sw = None

    @staticmethod
    def _console_write(text):
        out = getattr(sys, "stdout", None)
        try:
            if out is not None:
                out.write(text + u"\n")
                flush = getattr(out, "flush", None)
                if flush:
                    flush()
                return
        except Exception:
            pass
        try:
            print(text)
        except Exception:
            pass

    def log(self, level, msg):
        text = u"[%s] %-5s %s" % (hms(), level, to_text(msg))
        self.lines.append(text)
        if self.verbose or level in ("WARN", "ERROR"):
            self._console_write(text)
        if self._sw is not None:
            try:
                self._sw.WriteLine(text)
                self._sw.Flush()
            except Exception:
                pass

    def debug(self, msg):
        if self.verbose:
            self.log("DEBUG", msg)

    def info(self, msg):
        self.log("INFO", msg)

    def warn(self, msg):
        self.log("WARN", msg)

    def error(self, msg):
        self.log("ERROR", msg)

    def exception(self, msg, exc_info=True):
        self.log("ERROR", msg)
        if exc_info:
            self.log("ERROR", to_text(traceback.format_exc()))

    def close(self):
        if self._sw is not None:
            try:
                self._sw.Close()
            except Exception:
                pass
            self._sw = None

    def dump(self):
        return u"\n".join(self.lines)


# --------------------------------------------------------------------------
# 其它
# --------------------------------------------------------------------------
_UNSET = object()


def safe_get(obj, name, default=None):
    """getattr 的安全版本；返回 default 表示属性不存在或不可读。"""
    try:
        return getattr(obj, name)
    except Exception:
        return default


def has_attr(obj, name):
    try:
        getattr(obj, name)
        return True
    except Exception:
        return False


def last_error_line():
    """当前异常的最后一行，用于紧凑日志。"""
    return to_text(traceback.format_exc()).strip().splitlines()[-1:]


def chunked(seq, size):
    size = max(1, int(size))
    out = []
    for i in range(0, len(seq), size):
        out.append(seq[i:i + size])
    return out


def mkdirs(path):
    try:
        if path and not os.path.isdir(path):
            os.makedirs(path)
        return True
    except Exception:
        return False


def write_text(path, text):
    """UTF-8 写文本（IronPython 下优先用 .NET，保证中文与 BOM 行为一致）。"""
    mkdirs(os.path.dirname(os.path.abspath(path)))
    try:
        import System
        from System.Text import Encoding
        sw = System.IO.StreamWriter(path, False, Encoding.UTF8)
        sw.Write(to_text(text))
        sw.Close()
        return True
    except Exception:
        pass
    try:
        import io
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(to_text(text))
        return True
    except Exception:
        try:
            with open(path, "wb") as fh:
                fh.write(to_text(text).encode("utf-8"))
            return True
        except Exception:
            return False


def read_text(path):
    try:
        import System
        from System.Text import Encoding
        return System.IO.File.ReadAllText(path, Encoding.UTF8)
    except Exception:
        pass
    try:
        import io
        with io.open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return None
