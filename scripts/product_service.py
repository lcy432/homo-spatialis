"""E3C 托管服务的入口（DECISIONS/0043）：HTTP 接口、单个 worker 与本机管理员操作。不调用任何真实模型，除非服务配置打开了付费开关
（``live_calls_enabled``，默认关闭；本仓库的配置都没有打开）。

    uv run python scripts/product_service.py api [--config <服务配置>]
        启动 HTTP 接口（uvicorn，绑定配置里的地址；生产在反向代理后面，只信任代理转发的来源地址）
    uv run python scripts/product_service.py worker [--config <服务配置>] [--once]
        启动 worker（持进程锁；第二个 worker 拒绝启动）。--once：做完待办任务就退出
    uv run python scripts/product_service.py invite create --label <备注> [--config ...]
        新建一个专属空间与它的链接。完整链接只在这里显示一次（不进日志）；服务端只存摘要
    uv run python scripts/product_service.py invite reissue --principal <pr-…>
        重发链接：沿用原身份与次数，旧链接与旧会话一起失效（完整新链接只显示一次）
    uv run python scripts/product_service.py invite revoke --principal <pr-…>
        撤销：链接与会话失效（内容与次数记录不动）
    uv run python scripts/product_service.py jobs
        查看待处理任务与需要维护者处理的运行（只列编号、阶段与公开代码）
    uv run python scripts/product_service.py quota-adjust --principal <pr-…> (--credit | --debit) --note <原因>
        修正误扣的次数：追加一条记录，不改历史
    uv run python scripts/product_service.py openapi [--out contracts/product/v3/openapi.json]
        导出给 E5 的 OpenAPI 文档（生产不在线公开 /docs）

默认配置是 ``configs/product/service.yaml``（生产，真实规划器、付费开关关闭）；本地联调用
``configs/product/service_local_mock.yaml``（只绑定 127.0.0.1、离线 mock、零费用）。
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import tempfile
import threading
from pathlib import Path

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from product.service.actions import ServiceContext
from product.service.config import REPO, SERVICE_CONFIG, ServiceConfig, load_service_config

OPENAPI_OUT = REPO / "contracts/product/v3/openapi.json"


def _ctx(path: str | None) -> ServiceContext:
    return ServiceContext.open(load_service_config(Path(path) if path else SERVICE_CONFIG))


def cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    from product.service.api import create_app

    ctx = _ctx(args.config)
    cfg = ctx.config
    uvicorn.run(
        create_app(ctx),
        host=cfg.bind_host,
        port=cfg.bind_port,
        proxy_headers=True,
        forwarded_allow_ips=cfg.forwarded_allow_ips,
        server_header=False,
        date_header=True,
    )
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from workflows.service_worker import Worker, WorkerLockedError

    ctx = _ctx(args.config)
    worker = Worker(ctx)
    try:
        worker.acquire()
    except WorkerLockedError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    try:
        worker.recover()
        if args.once:
            n = worker.run_pending()
            print(f"处理了 {n} 个任务")
            return 0
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        print("worker 已启动（Ctrl+C 停止）")
        worker.run_forever(stop)
        return 0
    finally:
        worker.release()


def _link(ctx: ServiceContext, secret: str) -> str:
    return f"{ctx.config.site_origin}/invite#token={secret}"


def cmd_invite(args: argparse.Namespace) -> int:
    ctx = _ctx(args.config)
    store = ctx.store
    if args.action == "create":
        principal, secret = store.create_principal(args.label or "")
        print(f"服务身份：{principal}")
        print("专属链接（只显示这一次，请直接交给对方；不要贴进日志或工单）：")
        print(_link(ctx, secret))
        return 0
    if not args.principal:
        print("需要 --principal", file=sys.stderr)
        return 2
    if args.action == "reissue":
        try:
            secret = store.reissue_invite(args.principal)
        except KeyError:
            print("没有这个服务身份，或它已被撤销", file=sys.stderr)
            return 2
        print("新的专属链接（旧链接与旧会话已失效；次数与内容不变；只显示这一次）：")
        print(_link(ctx, secret))
        return 0
    store.revoke_principal(args.principal)
    print(f"{args.principal} 的链接与会话已撤销（内容与次数记录不动）")
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    ctx = _ctx(args.config)
    store = ctx.store
    rows = store.pending_jobs()
    print(f"待处理任务 {len(rows)} 个")
    for r in rows:
        print(
            f"  #{r['job_id']} {r['kind']:<9} {r['state']:<7} {r['intent_id']} 创建于 {r['created_at']}"
        )
    with store.read() as con:
        attention = list(
            con.execute(
                "SELECT intent_id, run_id, waiting_for, support_ref, updated_at FROM intents "
                "WHERE stage = 'needs_attention' ORDER BY updated_at"
            )
        )
    print(f"停下等处理的运行 {len(attention)} 个")
    for r in attention:
        print(
            f"  {r['intent_id']} 运行 {r['run_id']} 等 {r['waiting_for']} 支持编号 {r['support_ref']} 更新于 {r['updated_at']}"
        )
    return 0


def cmd_quota(args: argparse.Namespace) -> int:
    ctx = _ctx(args.config)
    if not args.note:
        print("需要 --note 写明修正原因", file=sys.stderr)
        return 2
    ctx.store.admin_adjust(args.principal, credit=args.credit, note=args.note)
    q = ctx.store.quota(args.principal)
    print(f"已追加修正记录。现在：{json.dumps(q.as_dict(), ensure_ascii=False)}")
    return 0


def cmd_openapi(args: argparse.Namespace) -> int:
    from product.service.api import openapi_document

    raw = load_service_config(REPO / "configs/product/service_local_mock.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        # 只为生成文档：数据目录放在临时目录里，不碰真实的服务数据
        cfg = ServiceConfig.model_validate({**raw.model_dump(mode="json"), "data_dir": tmp})
        doc = openapi_document(ServiceContext.open(cfg))
    out = Path(args.out) if args.out else OPENAPI_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"已写 {out.relative_to(REPO) if out.is_relative_to(REPO) else out.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("api", "worker", "jobs"):
        p = sub.add_parser(name)
        p.add_argument("--config")
        if name == "worker":
            p.add_argument("--once", action="store_true")
    inv = sub.add_parser("invite")
    inv.add_argument("action", choices=("create", "reissue", "revoke"))
    inv.add_argument("--label")
    inv.add_argument("--principal")
    inv.add_argument("--config")
    qa = sub.add_parser("quota-adjust")
    qa.add_argument("--principal", required=True)
    group = qa.add_mutually_exclusive_group(required=True)
    group.add_argument("--credit", action="store_true", help="退回一次误扣")
    group.add_argument("--debit", action="store_true", help="补记一次漏扣")
    qa.add_argument("--note")
    qa.add_argument("--config")
    oa = sub.add_parser("openapi")
    oa.add_argument("--out")
    args = ap.parse_args(argv)
    handlers = {
        "api": cmd_api,
        "worker": cmd_worker,
        "invite": cmd_invite,
        "jobs": cmd_jobs,
        "quota-adjust": cmd_quota,
        "openapi": cmd_openapi,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
