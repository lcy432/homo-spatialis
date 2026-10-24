"""服务用的时钟：UTC、秒级、可注入（测试用 ``FakeClock`` 跨过 30 天，不真的等）。"""

from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable

Clock = Callable[[], dt.datetime]


def system_clock() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def iso(t: dt.datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ``（公共消息的时间写法）。"""
    return t.astimezone(dt.UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)


def days_after(text: str, days: int) -> str:
    """``text`` 之后 ``days`` × 24 小时。"""
    return iso(parse(text) + dt.timedelta(days=days))


class FakeClock:
    """测试时钟：初始时间固定，``advance`` 前进；多线程读取安全。"""

    def __init__(self, start: dt.datetime | None = None) -> None:
        self._t = start or dt.datetime(2026, 10, 24, 9, 0, 0, tzinfo=dt.UTC)
        self._lock = threading.Lock()

    def __call__(self) -> dt.datetime:
        with self._lock:
            return self._t

    def advance(self, **kw: float) -> None:
        with self._lock:
            self._t = self._t + dt.timedelta(**kw)
