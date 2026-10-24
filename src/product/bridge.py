"""离线运行桥接（E3-A 模块 E）：提交 → 运行 → 进度 → 结果，全部经过 E1 已有的运行器。

* **提交**（``submit``）：核对确认画像与预检查的内容身份（``submission_problems``），按幂等键登记。同一个键、同一份预检查 → 返回
  原运行 ID（``duplicate_same_run``，不再生成一批分身）；同一个键配了另一份预检查 → 拒绝；确认或预检查失效 → 拒绝。登记表可以换：
  本地离线桥接用 JSON 文件（``JsonSubmissionRegistry``）；托管服务（E3C）用服务端 SQLite——同一套核对，只有一个权威登记。
* **运行**（``run_offline``）：用运行配置声明的离线规划器（产品 mock，零网络、零费用），交给 ``orchestration.life_runner.start_run``：
  年度调用、执行前检查与有限修订、有限记忆、归档与冻结、E1-6A 评价与 E1-6B 汇总都沿用已有入口，不另写一条生活链。
* **进度**（``progress``）：从运行归档的进度记录投影成产品的 ``RunProgress``（断线后用运行 ID 查询，不重发规划）。费用按这次运行的
  规划器写（E3C）：离线 mock 才是零费用；真实模型按报价估算，从这次运行自己的账本读已核算、用量不确定（保留预留）与在途预留三类
  金额，规划候选数与服务尝试数分开计。只读：不开写事务、不建账本。
"""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from agentlayer.life_mock import mock_planner_for
from common.life_planning_contracts import CLONE_RUN_STATUS_TEXT
from common.life_planning_contracts import RunProgress as E1RunProgress
from common.product_contracts import (
    CloneProgress,
    ConfirmedProfile,
    CostStatus,
    ProductIssue,
    ProductPreflight,
    RunProgress,
    RunReceipt,
    RunSubmission,
    SampleLabel,
)
from orchestration.life_archive import RunArchive
from orchestration.life_budget import ledger_state, read_ledger
from orchestration.life_runner import RunOutcome, RunWorld, start_run
from product.answers import confirmation_problems, issue
from product.pack import DataPack
from product.preflight import preflight_problems
from product.public_errors import progress_pause
from product.results import STAGE_TEXT

if TYPE_CHECKING:
    from product.execution import ExecutionSpec

REGISTRY = "submissions.json"
_MICRO = Decimal("0.000001")


def run_id_for(submission: RunSubmission) -> str:
    """运行 ID 由幂等键与预检查身份确定（同一提交得到同一 ID）。"""
    digest = hashlib.sha256(
        f"{submission.idempotency_key}|{submission.preflight.preflight_fingerprint}".encode()
    ).hexdigest()
    return f"run-e3a-{digest[:16]}"


class SubmissionRegistry(Protocol):
    """提交登记表：幂等键 → {"run_id", "preflight_fingerprint"}。"""

    def find(self, key: str) -> dict[str, str] | None: ...

    def add(self, key: str, run_id: str, preflight_fingerprint: str) -> None: ...


class JsonSubmissionRegistry:
    """本地离线桥接的登记表：一个 JSON 文件（``submissions.json``）。不是托管服务的权威记录。"""

    def __init__(self, registry_dir: Path) -> None:
        self.path = Path(registry_dir) / REGISTRY

    def _all(self) -> dict[str, dict[str, str]]:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def find(self, key: str) -> dict[str, str] | None:
        return self._all().get(key)

    def add(self, key: str, run_id: str, preflight_fingerprint: str) -> None:
        registry = self._all()
        registry[key] = {"run_id": run_id, "preflight_fingerprint": preflight_fingerprint}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
        )


def submission_problems(
    pack: DataPack, confirmed: ConfirmedProfile, pre: ProductPreflight, submission: RunSubmission
) -> list[ProductIssue]:
    """一次提交能不能登记：引用的确认画像与预检查是不是当前这一份、确认画像与预检查本身还有没有效（离线桥接与托管服务共用）。"""
    reasons = []
    if (submission.confirmed.confirmed_id, submission.confirmed.content_fingerprint) != (
        confirmed.confirmed_id,
        confirmed.content_fingerprint,
    ):
        reasons.append(
            issue(
                "submission_confirmed_mismatch",
                "confirmed",
                "提交引用的确认画像不是当前这一份",
                "不能运行",
                "重新确认后再提交",
            )
        )
    if (submission.preflight.preflight_id, submission.preflight.preflight_fingerprint) != (
        pre.preflight_id,
        pre.preflight_fingerprint,
    ):
        reasons.append(
            issue(
                "submission_preflight_mismatch",
                "preflight",
                "提交引用的预检查不是当前这一份（输入、配置或城市变了要重新检查）",
                "不能运行",
                "重新预检查后再提交",
            )
        )
    reasons += confirmation_problems(pack, confirmed)
    reasons += preflight_problems(pack, confirmed, pre)
    return reasons


def submit(
    pack: DataPack,
    confirmed: ConfirmedProfile,
    pre: ProductPreflight,
    submission: RunSubmission,
    registry_dir: Path | SubmissionRegistry,
    *,
    sample: SampleLabel | None = None,
    service: str = "offline_bridge",
) -> RunReceipt:
    """登记一次提交。不运行生活；运行由调用方在拿到 ``accepted`` 之后做。``registry_dir`` 是 JSON 登记表的目录，或别的登记表。"""
    registry = (
        JsonSubmissionRegistry(registry_dir) if isinstance(registry_dir, Path) else registry_dir
    )
    reasons = submission_problems(pack, confirmed, pre, submission)
    key = submission.idempotency_key
    previous = registry.find(key)
    if previous is not None and previous["preflight_fingerprint"] != pre.preflight_fingerprint:
        reasons.append(
            issue(
                "idempotency_key_reused",
                "idempotency_key",
                "这个幂等键已经用于另一份预检查：不能用同一个键提交不同的运行",
                "不能运行",
                "为新的预检查生成新的幂等键",
            )
        )
    if reasons:
        return RunReceipt(
            meta=pack.meta(sample),
            status="rejected",
            idempotency_key=key,
            reasons=tuple(reasons),
            service=service,  # type: ignore[arg-type]
        )
    if previous is not None:
        return RunReceipt(
            meta=pack.meta(sample),
            status="duplicate_same_run",
            run_id=previous["run_id"],
            idempotency_key=key,
            service=service,  # type: ignore[arg-type]
        )
    rid = run_id_for(submission)
    registry.add(key, rid, pre.preflight_fingerprint)
    return RunReceipt(
        meta=pack.meta(sample),
        status="accepted",
        run_id=rid,
        idempotency_key=key,
        service=service,  # type: ignore[arg-type]
    )


def run_offline(
    world: RunWorld,
    run_dir: Path,
    run_id: str,
    *,
    stop_after_calls: int | None = None,
) -> RunOutcome:
    """用运行配置声明的产品 mock 跑完整批（零网络）。真实模型规划器在这里被拒绝：本轮不新增任何真实调用。"""
    spec = world.config.planner
    if spec.identity.kind != "mock":
        raise ValueError("E3-A 的离线桥接只接受 mock 规划器：本轮不新增真实模型调用")
    planner = mock_planner_for(spec)
    return start_run(
        world, planner, Path(run_dir), run_id=run_id, stop_after_calls=stop_after_calls
    )


def _money(x: Decimal) -> str:
    return str(x.quantize(_MICRO, rounding=ROUND_CEILING))


def cost_status(
    execution: ExecutionSpec | None,
    ledger_dir: Path | None,
    *,
    calls_used: int,
    call_limit: int,
) -> CostStatus:
    """一次运行的费用状态。mock（或没有正式运行设置）是零费用；真实模型从这次运行自己的账本只读地汇总（账本还没建就全是 0）。"""
    if execution is None or execution.planner_kind == "mock":
        return CostStatus(
            kind="offline_mock_zero_cost",
            calls_used=calls_used,
            call_limit=call_limit,
            note="离线 mock：没有网络调用，费用为 0（真实模型运行会写“按报价估算”，以供应商账单为准）",
        )
    found = None if ledger_dir is None else read_ledger(Path(ledger_dir))
    settled = uncertain = in_flight = Decimal(0)
    attempts = uncertain_attempts = 0
    if found is not None:
        reserves, closes = ledger_state(found[1])
        cur = (execution.currency or "CNY").lower()
        for key, r in reserves.items():
            attempts += 1
            c = closes.get(key)
            if c is None:
                in_flight += Decimal(r[f"reserved_{cur}"])
            elif c["event"] == "settle":
                settled += Decimal(c[f"cost_{cur}"])
            else:
                uncertain += Decimal(c[f"cost_{cur}"])
                uncertain_attempts += 1
    return CostStatus(
        kind="estimated_from_quote",
        calls_used=calls_used,
        call_limit=call_limit,
        estimated_cost=_money(settled + uncertain + in_flight),
        note=(
            f"应用侧按报价 {execution.price_version} 估算，不是供应商账单（以账单为准）：已核算 + 用量不确定（保留预留，不记 0）"
            f" + 在途预留；本次运行上限 {execution.max_cost} {execution.currency}"
        ),
        currency=execution.currency,
        price_version=execution.price_version,
        max_cost=execution.max_cost,
        settled_cost=_money(settled),
        uncertain_cost=_money(uncertain),
        in_flight_cost=_money(in_flight),
        service_attempts=attempts,
        service_attempt_limit=execution.max_requests,
        uncertain_attempts=uncertain_attempts,
    )


def progress(
    pack: DataPack,
    run_dir: Path,
    *,
    sample: SampleLabel | None = None,
    execution: ExecutionSpec | None = None,
    ledger_dir: Path | None = None,
    updated_at: str | None = None,
    resumable: bool | None = None,
    running_now: bool = False,
) -> RunProgress:
    """从运行归档的进度记录投影出产品进度（断线后按运行 ID 查询；不重发规划）。路径可以是绝对路径（托管服务的监测线程不切换
    工作目录）；``execution`` 与 ``ledger_dir`` 决定费用怎么写（见 ``cost_status``）。``resumable`` 不写时按 E1 的进度状态；
    托管服务传它自己的恢复许可（按 E1 的请求尝试、预算与发送状态判断），公开进度、暂停说明与“恢复”按钮用同一个结论。
    ``running_now``：托管服务的监测线程在运行进行中写快照——E1 的状态还是 ``running`` 时，这次运行正在跑，没有要恢复的东西，
    也不是“停下了”（E1 已经写下结束或暂停状态时照常按那个状态写，不受它影响）。离线桥接不写它：那里查询时还是 ``running``
    说明上一个进程中途退出了。"""
    archive = RunArchive(Path(run_dir), create=False)
    p = E1RunProgress.model_validate(archive.read_json("progress.json"))
    report = (Path(run_dir) / "reports" / "arm.main.json").exists()
    stopped = p.status in ("running", "interrupted", "halted")
    live = running_now and p.status == "running"
    if live:
        resumable = False
    elif resumable is None:
        resumable = stopped
    return RunProgress(
        meta=pack.meta(sample),
        run_id=p.run_id,
        stage=p.status,  # type: ignore[arg-type]
        stage_zh=STAGE_TEXT.get(p.status, p.status),
        clones=tuple(
            CloneProgress(
                clone_ref=c.clone_id,
                city_ref=str(c.city),
                status=c.status,
                status_zh=CLONE_RUN_STATUS_TEXT[c.status],
                years_committed=c.years_committed,
            )
            for c in p.clones
        ),
        # 按进度的结构化状态生成说明，不透传 pause_reason 原文（那里可能有异常内容）；报告与恢复两句按实际情况写
        pause=progress_pause(p, report_available=report, resumable=resumable),
        cost=cost_status(execution, ledger_dir, calls_used=p.calls_used, call_limit=p.call_limit),
        resumable=resumable,
        resume_note=(
            "这次运行正在进行，不需要恢复；结束或暂停之后这里会更新"
            if live
            else "可以从年度边界或两份候选之间恢复（E1 的恢复入口逐条核对归档，尝试次数与调用次数不清零）"
            if resumable
            else "这次运行停在不能由你恢复的位置：等维护者处理（尝试次数与预算不会被清零来绕过）"
            if stopped
            else "这次运行已经结束，没有可以恢复的部分"
        ),
        report_available=report,
        updated_at=updated_at,
    )
