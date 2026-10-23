"""PDF 正文的最小读取（只用标准库）：解压 Flate 流，取文字操作里的字面字符串，还原转义。

只给摘句核对用：PDF 排版常把一个词拆成几段（“Oper a ting”），所以核对时去掉全部空白再比较（``squash``）。不处理十六进制
字形编码、加密或扫描件；读不出来的文件核对就不通过，不拿别的数顶替。
"""

from __future__ import annotations

import re
import zlib

_STREAM = re.compile(rb"stream\r?\n")
_LITERAL = re.compile(rb"\(((?:[^()\\]|\\.)*)\)", re.DOTALL)
_ESCAPES = {b"n": "\n", b"r": "\r", b"t": "\t", b"b": "\b", b"f": "\f"}


def is_pdf(content: bytes) -> bool:
    return content.startswith(b"%PDF-")


def _unescape(raw: bytes) -> str:
    out: list[str] = []
    i = 0
    while i < len(raw):
        c = raw[i : i + 1]
        if c != b"\\":
            out.append(c.decode("cp1252", errors="replace"))  # 标准字体按 WinAnsi（≈ cp1252）编码
            i += 1
            continue
        nxt = raw[i + 1 : i + 2]
        if nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            i += 2
        elif nxt.isdigit():
            digits = re.match(rb"[0-7]{1,3}", raw[i + 1 : i + 4])
            assert digits is not None
            out.append(bytes([int(digits.group(), 8) & 0xFF]).decode("cp1252", errors="replace"))
            i += 1 + len(digits.group())
        else:
            out.append(nxt.decode("latin-1"))  # \( \) \\ 与未知转义：取字符本身
            i += 2
    return "".join(out)


def pdf_text(content: bytes) -> str:
    """全部内容流里的字面字符串，按出现顺序用空格连接。"""
    parts: list[str] = []
    for m in _STREAM.finditer(content):
        end = content.find(b"endstream", m.end())
        if end < 0:
            continue
        try:
            data = zlib.decompressobj().decompress(content[m.end() : end])
        except zlib.error:
            continue
        parts += [_unescape(s) for s in _LITERAL.findall(data)]
    return " ".join(parts)


def squash(text: str) -> str:
    """去掉全部空白（PDF 排版拆开的词与数字重新接上）。"""
    return "".join(text.split())
