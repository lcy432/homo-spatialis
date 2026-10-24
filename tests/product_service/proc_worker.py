"""测试用：在子进程里跑一次 worker（做完待办任务就退出），用来模拟崩溃与重启。

    python tests/product_service/proc_worker.py <服务配置 JSON> [--hold-lock <秒>]

* 故障注入沿用流程接入层的环境变量 ``HS_WF_E3_FAULT``（例如 ``life:crash_at_call:4``、``life:after_complete``、
  ``submit:after_register``），进程用 ``os._exit`` 直接退出，和真的崩溃一样不做任何收尾；
* 配置是真实模型规划器时，传输层是离线替身（**测试传输**）：用 mock 规则从安全投影生成合法计划，每次发送记一行到数据目录的
  ``test_transport_sends.jsonl``；``HS_TEST_EXIT_ON_SEND=<n>`` 让第 n 次发送在“已经发出、还没有结果”的位置退出；
* ``--hold-lock``：拿到 worker 锁之后等几秒再退出（测试第二个 worker 不能启动）。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (str(REPO), str(REPO / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from common import config as common_config  # noqa: E402
from product.service.actions import ServiceContext  # noqa: E402
from product.service.config import ServiceConfig  # noqa: E402
from tests.life.live_support import mock_text, reply  # noqa: E402
from workflows.service_worker import Worker, WorkerLockedError  # noqa: E402

EXIT_ON_SEND = 54


class FileTransport:
    """测试传输：不联网。发送序号跨进程连续（按记录文件的行数）。"""

    def __init__(self, log: Path, exit_on: int | None) -> None:
        self.log, self.exit_on = log, exit_on

    def send(self, body, *, timeout):
        n = (
            len(self.log.read_text(encoding="utf-8").splitlines()) if self.log.is_file() else 0
        ) + 1
        payload = json.loads(
            body["messages"][1]["content"].split("```json\n", 1)[1].rsplit("\n```", 1)[0]
        )
        with self.log.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(
                json.dumps({"n": n, "pid": os.getpid(), "request_id": payload["request_id"]}) + "\n"
            )
        if self.exit_on == n:
            os._exit(EXIT_ON_SEND)  # 已经发出、结果还没有落盘
        return reply(mock_text(body), body, model=body["model"])


def _block_network() -> None:
    import ipaddress
    import socket

    original = socket.socket.connect

    def connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        try:
            ok = ipaddress.ip_address(host).is_loopback
        except ValueError:
            ok = host == "localhost"
        if not ok:
            raise AssertionError(f"测试子进程禁止连接外部地址：{host}")
        return original(self, address, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]


def main() -> int:
    _block_network()
    cfg = ServiceConfig.model_validate(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    hold = float(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--hold-lock" else 0.0
    common_config._ensure_dotenv = lambda: None  # 不读项目的 .env
    ctx = ServiceContext.open(cfg)
    transport = None
    if cfg.planner == "llm":
        exit_on = os.environ.get("HS_TEST_EXIT_ON_SEND")
        transport = FileTransport(
            ctx.data_root / "test_transport_sends.jsonl", int(exit_on) if exit_on else None
        )
    worker = Worker(
        ctx,
        transport_factory=(lambda _s: transport) if transport is not None else None,
        sleep=lambda _s: None,
    )
    try:
        worker.acquire()
    except WorkerLockedError:
        print("locked")
        return 3
    try:
        if hold:
            print("holding", flush=True)
            time.sleep(hold)
            return 0
        worker.recover()
        worker.run_pending()
        return 0
    finally:
        worker.release()


if __name__ == "__main__":
    sys.exit(main())
