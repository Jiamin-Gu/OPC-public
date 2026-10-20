import sys

__version__ = "0.22.0"


def use_utf8_output() -> None:
    """输出被重定向到文件或管道时，Windows 默认用 GBK 编码，遇到 emoji 会报错，统一改成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")
