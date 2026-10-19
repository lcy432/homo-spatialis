"""LangGraph 外层流程的本地演示入口（原型）。零网络、零费用，不调用任何模型。

本入口与旧入口互不影响：``scripts/run_life_e1_7b.py`` 与 ``scripts/product_e3a.py`` 照旧可用，默认配置没有改。

两种服务：

* ``--services e3``：真实 E3 / E1 接入（E3 的预检查、登记、进度与结果投影；E1 的启动、恢复与归档）。需要 ``--e3-workspace``：
  一个 E3 工作目录（``e3-seed`` 会准备它：资料包与基础样例按仓库相对路径复制进来）。规划器是资料包声明的离线 mock。
* ``--services synthetic``（默认）：合成替身（``workflows.synthetic``），只检验流程本身；需要 ``--synthetic-root``。

    uv run python scripts/product_workflow.py --e3-workspace <目录> e3-seed --example normal
    uv run python scripts/product_workflow.py --services e3 --e3-workspace <目录> --db <库> start --workflow-id W1 --profile <确认编号> --inputs normal
    uv run python scripts/product_workflow.py --services e3 --e3-workspace <目录> --db <库> status --workflow-id W1
    uv run python scripts/product_workflow.py --services e3 --e3-workspace <目录> --db <库> confirm --workflow-id W1 --preflight <confirm_with>
    uv run python scripts/product_workflow.py --services e3 --e3-workspace <目录> --db <库> resume --workflow-id W1
    uv run python scripts/product_workflow.py --services e3 --e3-workspace <目录> --db <库> resolve --workflow-id W1 --action retry

``e3-seed`` 的例子（与 E3 测试场景同一组合成输入）：normal（u501，两城三年）、mixed（u603，C / E / M 都有）、all_exit（u604，全部
出局）、paused（u606，mock 抛意外异常，E1 暂停批次）。``--profile`` / ``--inputs`` 写确认编号与输入名时按工作目录里当前的文件取
内容身份；也可以写 ``<编号>@<内容身份>``。

合成替身：``seed --synthetic-root <目录> --example normal|halt-service|uncertain|budget|blocked``，其余命令同上（把 ``--services e3
--e3-workspace`` 换成 ``--synthetic-root``）。输出是公开视图 JSON；被拒绝的操作退出码 2。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401  (src/ 进 sys.path)
from workflows.e3_adapters import EXAMPLES as E3_EXAMPLES
from workflows.e3_adapters import E3Workspace, e3_services, seed_example
from workflows.runtime import WorkflowRefusedError, WorkflowRuntime
from workflows.state import ContentRef
from workflows.synthetic import SyntheticWorld

PROFILE = {"person_id": "u301", "note": "合成画像占位；真实画像是 E3 的 ConfirmedProfile"}
BASE = {
    "years": 3,
    "cities": ["K51", "M52"],
    "outcomes": {"K51": "C", "M52": "M"},
    "max_paid_calls": 12,
}
EXAMPLES = {
    "normal": BASE,
    "halt-service": {**BASE, "halt": {"after_year": 1, "kind": "service_unavailable"}},
    "uncertain": {**BASE, "halt": {"after_year": 1, "kind": "uncertain_send"}},
    "budget": {**BASE, "max_paid_calls": 2},
    "blocked": {**BASE, "blocked": True},
}


def _print(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def _ref(
    world: SyntheticWorld | None, ws: E3Workspace | None, value: str | None, kind: str
) -> ContentRef | None:
    if value is None:
        return None
    if "@" in value:  # <编号>@<内容身份>
        ref_id, _, fp = value.partition("@")
        return ContentRef(kind=kind, ref_id=ref_id, fingerprint=fp)
    if world is not None:
        return world.ref(value, kind=kind)
    assert ws is not None
    return ws.confirmed_ref(value) if kind == "confirmed_profile" else ws.request_ref(value)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", type=Path, help="流程的 SQLite 数据库（checkpoint）")
    ap.add_argument("--services", choices=("synthetic", "e3"), default="synthetic")
    ap.add_argument("--synthetic-root", type=Path)
    ap.add_argument("--e3-workspace", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--example", choices=sorted(EXAMPLES), default="normal")
    s.add_argument("--name", default=None, help="输入文件名（默认与例子同名）")
    e = sub.add_parser("e3-seed")
    e.add_argument("--example", choices=sorted(E3_EXAMPLES), default="normal")
    e.add_argument("--name", default=None, help="本次输入的名字（也是编译目录名；默认与例子同名）")
    for name in ("start", "confirm", "decline", "revise", "resume", "resolve", "status"):
        p = sub.add_parser(name)
        p.add_argument("--workflow-id", required=True)
        if name in ("start", "revise"):
            p.add_argument("--profile", required=(name == "start"))
            p.add_argument("--inputs", required=(name == "start"))
        if name == "start":
            p.add_argument("--idempotency-key", default=None)
        if name == "confirm":
            p.add_argument("--preflight", required=True, help="公开视图里的 confirm_with")
        if name == "resolve":
            p.add_argument("--action", choices=("retry", "abandon"), required=True)
    args = ap.parse_args(argv)

    if args.cmd == "e3-seed":
        if args.e3_workspace is None:
            ap.error("e3-seed 需要 --e3-workspace")
        ws = E3Workspace.prepare(args.e3_workspace)
        profile, inputs = seed_example(ws, args.example, name=args.name)
        _print({"profile": profile.as_state(), "inputs": inputs.as_state()})
        return 0
    world = ws = None
    if args.services == "synthetic":
        if args.synthetic_root is None:
            ap.error("--services synthetic 需要 --synthetic-root")
        world = SyntheticWorld(args.synthetic_root)
    else:
        if args.e3_workspace is None:
            ap.error("--services e3 需要 --e3-workspace")
        ws = E3Workspace(args.e3_workspace)
    if args.cmd == "seed":
        if world is None:
            ap.error("seed 只用于合成替身；E3 用 e3-seed")
        profile = world.write_input("profile", PROFILE, kind="confirmed_profile")
        inputs = world.write_input(
            args.name or args.example, EXAMPLES[args.example], kind="run_inputs"
        )
        _print({"profile": profile.as_state(), "inputs": inputs.as_state()})
        return 0
    if args.db is None:
        ap.error("需要 --db")
    services = world.services() if world is not None else e3_services(ws)  # type: ignore[arg-type]
    try:
        with WorkflowRuntime(args.db, services) as rt:
            wid = args.workflow_id
            if args.cmd == "start":
                view = rt.start(
                    workflow_id=wid,
                    profile=_ref(world, ws, args.profile, "confirmed_profile"),
                    inputs=_ref(world, ws, args.inputs, "run_inputs"),
                    idempotency_key=args.idempotency_key,
                )
            elif args.cmd == "confirm":
                view = rt.confirm(wid, args.preflight)
            elif args.cmd == "decline":
                view = rt.decline(wid)
            elif args.cmd == "revise":
                view = rt.revise(
                    wid,
                    profile=_ref(world, ws, args.profile, "confirmed_profile"),
                    inputs=_ref(world, ws, args.inputs, "run_inputs"),
                )
            elif args.cmd == "resume":
                view = rt.resume(wid)
            elif args.cmd == "resolve":
                view = rt.resolve(wid, args.action)
            else:
                view = rt.status(wid)
    except WorkflowRefusedError as exc:
        _print(exc.public())
        return 2
    except (ValueError, FileNotFoundError):  # 引用格式不对或文件不在
        _print(WorkflowRefusedError("invalid_request").public())
        return 2
    _print(view)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
