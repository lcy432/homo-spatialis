"""真实 E3 / E1 接入层（2026-10-18 接入 E3-A 最终版：``src/product/`` 与 ``common.product_contracts`` 在 20:52 之后没有再变）。

四个接口都只调用已有的正式入口，不复制画像、预检查、进度或结果的字段定义，也不重写任何生活、评分或评价数学：

* ``PreflightPort.check``：``product.preflight.run_preflight``（编译 → E1 运行前检查 → 产品预检查），结果身份就是
  ``ProductPreflight.preflight_fingerprint``（覆盖画像、资料包、编译出的 E1 输入、协议、评分内容、评价规格、提示模板、规划器与
  E1 运行身份；资料包里的规划器与调用上限一变，身份就变）。同一输入、同一编译目录重复调用得到同一身份（流程在登记前与恢复时
  依赖这一点）。
* ``SubmissionPort.submit``：``product.bridge.submit``（只登记，不运行；同键同检查返回原运行，同键不同检查拒绝）。**不用**
  ``bridge.run_offline`` 登记：它直接 ``start_run``。
* ``LifeRunPort``：``inspect`` 只读（运行世界按编译出的配置重建，并核对 E1 运行身份与预检查记下的一致）；``start`` 用 E3 的
  ``bridge.run_offline``（产品 mock，零网络）；``resume`` 用 E1 的 ``orchestration.life_runner.resume_run``。E1 自己负责修订、
  服务重试、归档核对与调用次数；本层不另加重试。
* ``ProjectionPort``：``product.bridge.progress`` 与 ``product.results.project_result``。结果所需的 E1 ``RunOutcome`` 由
  ``orchestration.life_outcome.outcome_from_archive`` 从归档零调用重建（先过 E1 官方重放核对），所以“E1 已经跑完、外层还没记下”
  之后恢复，也只是读取，不会再模拟一次。

全部操作在一个 E3 工作目录里进行（``product.fixtures.prepare_workspace``：资料包与基础样例按仓库相对路径复制进来，编译产物
与运行配置都用相对路径）。工作目录里本层自己的文件都在 ``workflow/`` 下：

    workflow/confirmed/<确认编号>.json   已确认画像（E3 的 ConfirmedProfile；流程从这里开始，不重做问卷）
    workflow/requests/<名字>.json        本次输入：资料包路径与身份、编译目录名、受控测试设置（不是 E3 的共享消息）
    workflow/preflights/<身份>.json      预检查记录（E3 的 ProductPreflight 原样 + 选项目录覆盖 + 引用）
    workflow/compiled_owners/<名字>.json 编译目录的归属（同名不同条件拒绝，防止覆盖已登记运行的编译输入）
    workflow/runs/<运行编号>.json        运行索引（运行编号 → 预检查身份与幂等键）
    workflow/receipts/<键的摘要>.json    E3 的 RunReceipt 原样（审计用）
    workflow/results/<运行编号>.json     已投影的 ProductResult（按它自己的结果身份核对后才复用）

E3 的提交记录在 ``registry/``，E1 的运行归档在 ``archive/<运行编号>/``，编译产物在 ``compiled/<名字>/``——都是 E3 / E1 自己的格式。

默认只接受资料包声明的离线 mock 规划器（与 ``bridge.run_offline`` 相同），真实模型报 ``real_planner_not_supported``。托管服务
（E3C，``workflows.service_worker``）在构造上下文时注入：

* ``planner_factory``：按运行配置造规划器（离线 mock，或 ``LlmPlanner`` + ``make_transport`` + 本次运行自己的 ``RoundBudget``）；
  付费开关没开或缺凭证就报公开代码，不换成 mock；
* ``control``：用户删除时的停止与逐次发送许可（``agentlayer.life_planning.RunControl``）；
* ``on_start``：首次启动 E1 之前的最后一步（把预留的次数转为已使用）；拒绝就不启动；
* ``registry``：提交登记表（服务端 SQLite 是权威记录，没有第二份 JSON 登记），回执写 ``hosted_service``；
* ``workers``：一次运行内同时过生活的分身数（≤ 16）。

``RunRequest.execution`` 是正式运行设置（``product.execution.ExecutionSpec``）：预检查按它编译，资料包按它核对与设定规模。

测试钩子（只在构造参数 ``faults`` 或环境变量 ``HS_WF_E3_FAULT`` 里显式写了才生效）：``submit:after_register``（E3 登记写完后进程立刻
退出，退出码 51）、``life:crash_at_call:<n>``（本进程第 n 次规划调用开始时退出，52）、``life:halt_at_call:<n>:<种类>``（第 n 次规划
调用时规划器报 E1 的 ``PlannerHalt``，每个接入层实例只报一次）、``life:after_complete``（E1 跑完之后、返回之前退出，53）、
``count_calls``（把每次实际规划调用记进 ``workflow/planner_invocations.jsonl``）。包装后的规划器对外的身份与行为设定与原 mock
完全相同，所以照样通过 E1 的规划器一致性检查。
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from agentlayer.life_mock import mock_planner_for
from agentlayer.life_planning import PlannerHalt, RunControl
from common.life_contracts import canonical_fingerprint
from common.life_planning_contracts import MockScript
from common.life_planning_contracts import RunProgress as E1RunProgress
from common.product_contracts import (
    ConfirmedProfile,
    ConfirmedRef,
    OptionInventoryCoverage,
    PreflightRef,
    ProductPreflight,
    ProductResult,
    RunSubmission,
)
from common.run_store import RunStore
from orchestration.life_outcome import FINISHED_WITH_REPORT, outcome_from_archive
from orchestration.life_runner import (
    RunRefusedError,
    RunWorld,
    first_year_input_checks,
    resume_run,
    start_run,
)
from orchestration.life_runner import preflight as e1_preflight
from product import bridge
from product.bridge import SubmissionRegistry
from product.display import recording
from product.display.document import binding_document, carry_bindings, doc_version
from product.execution import ExecutionSpec, service_pack
from product.fixtures import prepare_workspace
from product.pack import DataPack, load_pack
from product.preflight import run_preflight
from product.results import project_result
from workflows.ports import (
    AttentionRequiredError,
    PreflightOutcome,
    Receipt,
    ResultView,
    RunInspection,
    WorkflowServices,
)
from workflows.state import ContentRef, fingerprint, public_text

REPO = Path(__file__).resolve().parents[2]
RUN_REQUEST_VERSION = "wf-e3-run-request-v1"
DEFAULT_PACK_PATH = "configs/product/e3a_synthetic_pack.yaml"
#: E3B 六城真实资料包（DECISIONS/0042）。工作目录里同样按仓库相对路径复制。
E3B_PACK_PATH = "configs/product/e3b_us_v1_pack.yaml"
EXIT_CODES = {"submit:after_register": 51, "life:crash_at_call": 52, "life:after_complete": 53}

#: E1 停下（halted）的原因 → 流程的公开代码。服务失败是 E1 支持恢复的边界（等人发起后经 E1 的恢复入口接上）；其余都要维护者
#: 按 E1 的规则核对，流程的“重试”只会再查询一次，不会恢复，也不会再请求规划。
HALT_CODES: dict[str, str | None] = {
    "service_unavailable": None,
    "uncertain_send": "uncertain_send",
    "budget": "budget_blocked",
    "model_mismatch": "model_mismatch",
    "user_deletion": "run_deleted",
}


class RunRequest(BaseModel):
    """本次输入：资料包（含基础样例与运行设置）+ 编译目录名 + 受控测试设置。流程侧的文件，不是 E3 的共享消息。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["wf-e3-run-request-v1"]
    label: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,40}$")
    pack_path: str = DEFAULT_PACK_PATH
    pack_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repeat_indexes: tuple[int, ...] | None = None
    scripts: tuple[MockScript, ...] = ()
    illness_events: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=300)
    #: 正式运行设置（E3C 托管服务）：规模、规划器、调用设置、报价与上限；它的内容进入本次输入的身份
    execution: ExecutionSpec | None = None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, obj: Any, *, sort_keys: bool = False) -> None:
    """原样保留键的顺序：E3 编译时按传入顺序写 YAML（``sort_keys=False``），已确认画像里点数字典的键顺序会进入编译产物、
    预检查身份与 E1 运行身份——即使画像的内容身份不变（2026-10-18 接入时发现，已写进给 E3 的同步清单）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=sort_keys)
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validated(model: Any, data: Any, code: str, audit_ref: str | None = None) -> Any:
    """读回本层存下的 E3 / E1 对象：结构校验不过（被改过、写坏了）就停在 ``code``，不让它变成内部错误。"""
    try:
        return model.model_validate(data)
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise AttentionRequiredError(code, audit_ref=audit_ref) from exc


class E3Workspace:
    """一个 E3 工作目录。所有路径相对它；E3 / E1 的调用都在 ``cwd()`` 里进行（E3 的编译产物用相对路径）。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        if not (self.root / DEFAULT_PACK_PATH).is_file():
            raise FileNotFoundError(
                f"不是 E3 工作目录（缺 {DEFAULT_PACK_PATH}）：先用 E3Workspace.prepare"
            )

    @classmethod
    def prepare(cls, root: Path | str) -> E3Workspace:
        prepare_workspace(Path(root), REPO)
        return cls(root)

    @contextlib.contextmanager
    def cwd(self) -> Iterator[None]:
        with contextlib.chdir(self.root):
            yield

    # ---- 准备流程的起点（已确认画像与本次输入） ----
    def save_confirmed(self, confirmed: ConfirmedProfile) -> ContentRef:
        if confirmed.content_fingerprint != confirmed.expected_fingerprint():
            raise ValueError("已确认画像的内容与它记下的身份不一致")
        _write_json(
            self.root / "workflow/confirmed" / f"{confirmed.confirmed_id}.json",
            confirmed.model_dump(mode="json"),
        )
        return self.confirmed_ref(confirmed.confirmed_id)

    def confirmed_ref(self, confirmed_id: str) -> ContentRef:
        raw = _read_json(self.root / "workflow/confirmed" / f"{confirmed_id}.json")
        return ContentRef(
            kind="confirmed_profile", ref_id=confirmed_id, fingerprint=raw["content_fingerprint"]
        )

    def save_request(self, name: str, request: RunRequest) -> ContentRef:
        path = self.root / "workflow/requests" / f"{name}.json"
        _write_json(path, request.model_dump(mode="json"))
        return self.request_ref(name)

    def request_ref(self, name: str) -> ContentRef:
        data = (self.root / "workflow/requests" / f"{name}.json").read_bytes()
        return ContentRef(kind="run_inputs", ref_id=name, fingerprint=_sha(data))

    def pack_fingerprint(self, pack_path: str = DEFAULT_PACK_PATH) -> str:
        with self.cwd():
            return load_pack(Path(pack_path)).fingerprint


class _Faults:
    def __init__(self, faults: set[str]) -> None:
        self.items = set(faults)
        self.halts_fired: set[str] = set()

    def exit_if(self, name: str) -> None:
        if name in self.items:
            os._exit(EXIT_CODES[name])

    @property
    def wraps_planner(self) -> bool:
        return any(
            f.startswith(("life:crash_at_call:", "life:halt_at_call:")) or f == "count_calls"
            for f in self.items
        )


class _InstrumentedPlanner:
    """测试用：包住产品 mock。对外的身份与行为设定不变；按测试钩子在第 n 次调用时退出进程或报 ``PlannerHalt``。"""

    def __init__(self, inner: Any, faults: _Faults, log: Path) -> None:
        self._inner = inner
        self._faults = faults
        self._log = log
        self._n = 0

    @property
    def identity(self):
        return self._inner.identity

    def effective_spec(self):
        return self._inner.effective_spec()

    def plan(self, call):
        self._n += 1
        if "count_calls" in self._faults.items:
            self._log.parent.mkdir(parents=True, exist_ok=True)
            with self._log.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(
                    json.dumps({"pid": os.getpid(), "n": self._n, "request_id": call.request_id})
                    + "\n"
                )
        for f in sorted(self._faults.items):
            parts = f.split(":")
            if f.startswith("life:crash_at_call:") and int(parts[2]) == self._n:
                os._exit(EXIT_CODES["life:crash_at_call"])
            if (
                f.startswith("life:halt_at_call:")
                and int(parts[2]) == self._n
                and f not in self._faults.halts_fired
            ):
                self._faults.halts_fired.add(f)
                raise PlannerHalt(
                    parts[3], f"测试注入：第 {self._n} 次规划调用时规划器停下（{parts[3]}）"
                )
        return self._inner.plan(call)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class E3Context:
    """四个接口共用的上下文：工作目录、测试钩子、按引用加载并核对 E3 对象、重建 E1 运行世界。托管服务注入的东西见模块说明。"""

    def __init__(
        self,
        workspace: E3Workspace,
        *,
        faults: set[str] | None = None,
        planner_factory: Callable[[RunWorld], Any] | None = None,
        control: RunControl | None = None,
        on_start: Callable[[str], None] | None = None,
        registry: SubmissionRegistry | None = None,
        service: str = "offline_bridge",
        workers: int = 1,
    ) -> None:
        self.ws = workspace
        env = {f for f in os.environ.get("HS_WF_E3_FAULT", "").split(",") if f}
        self.faults = _Faults(set(faults or set()) | env)
        self.planner_factory = planner_factory
        self.control = control
        self.on_start = on_start
        self.registry = registry
        self.service = service
        self.workers = workers

    @property
    def hosted(self) -> bool:
        return self.planner_factory is not None

    # ---- 加载与核对（都在 cwd 里调用） ----
    def confirmed(self, ref: ContentRef) -> ConfirmedProfile | None:
        path = Path("workflow/confirmed") / f"{ref.ref_id}.json"
        if not path.is_file():
            return None
        try:
            confirmed = ConfirmedProfile.model_validate(_read_json(path))
        except (ValidationError, ValueError):
            return None
        ok = (
            confirmed.confirmed_id == ref.ref_id
            and confirmed.content_fingerprint == ref.fingerprint
            and confirmed.content_fingerprint == confirmed.expected_fingerprint()
        )
        return confirmed if ok else None

    def request(self, ref: ContentRef) -> RunRequest | None:
        path = Path("workflow/requests") / f"{ref.ref_id}.json"
        if not path.is_file() or _sha(path.read_bytes()) != ref.fingerprint:
            return None
        try:
            return RunRequest.model_validate(_read_json(path))
        except (ValidationError, ValueError):
            return None

    def pack(self, request: RunRequest) -> DataPack | None:
        if request.execution is not None:
            # 正式运行设置：文件内容身份必须是设置记下的那一份，规模按设置（不读仓库里更新过的资料冒充）
            if request.execution.pack_fingerprint != request.pack_fingerprint:
                return None
            try:
                return service_pack(Path(request.pack_path), request.execution)
            except (OSError, ValueError):
                return None
        pack = load_pack(Path(request.pack_path))
        if pack.fingerprint != request.pack_fingerprint:
            return None
        # 与 product.scenarios.closed_loop 同一种改法：只改内存里的运行设置
        if request.repeat_indexes is not None:
            raw = copy.deepcopy(pack.raw)
            raw["pack"]["run"]["repeat_indexes"] = list(request.repeat_indexes)
            pack = DataPack(raw, pack.path)
        return pack

    def record(self, preflight_fp: str) -> dict[str, Any]:
        path = Path("workflow/preflights") / f"{preflight_fp}.json"
        if not path.is_file():
            raise AttentionRequiredError(
                "reference_stale", audit_ref=f"wf-preflight-{preflight_fp[:12]}"
            )
        return _read_json(path)

    def e3_inputs(
        self, preflight_fp: str
    ) -> tuple[ProductPreflight, ConfirmedProfile, RunRequest, DataPack, dict[str, Any]]:
        rec = self.record(preflight_fp)
        if rec.get("kind") != "e3_preflight":
            raise AttentionRequiredError("reference_stale")
        audit = f"wf-preflight-{preflight_fp[:12]}"
        pre = _validated(ProductPreflight, rec.get("preflight"), "archive_conflict", audit)
        if pre.preflight_fingerprint != preflight_fp or pre.expected_fingerprint() != preflight_fp:
            raise AttentionRequiredError(
                "archive_conflict", audit_ref=f"wf-preflight-{preflight_fp[:12]}"
            )
        confirmed = self.confirmed(ContentRef.from_state(rec["confirmed"]))
        request = self.request(ContentRef.from_state(rec["request"]))
        pack = None if request is None else self.pack(request)
        if confirmed is None or request is None or pack is None:
            raise AttentionRequiredError(
                "reference_stale", audit_ref=f"wf-preflight-{preflight_fp[:12]}"
            )
        return pre, confirmed, request, pack, rec

    def run_index(self, run_id: str) -> dict[str, Any]:
        path = Path("workflow/runs") / f"{run_id}.json"
        if not path.is_file():
            raise AttentionRequiredError("run_binding_conflict", audit_ref=f"wf-run-{run_id}")
        idx = _read_json(path)
        if idx.get("run_id") != run_id:
            raise AttentionRequiredError("run_binding_conflict", audit_ref=f"wf-run-{run_id}")
        return idx

    def world(
        self, run_id: str
    ) -> tuple[RunWorld, ProductPreflight, ConfirmedProfile, DataPack, dict[str, Any]]:
        """按运行编号重建 E1 运行世界：读编译出的配置（不重新编译、不写文件），核对 E1 运行身份与预检查记下的一致。"""
        idx = self.run_index(run_id)
        pre, confirmed, request, pack, rec = self.e3_inputs(idx["preflight_fingerprint"])
        if pre.binding is None:
            raise AttentionRequiredError("archive_conflict", audit_ref=f"wf-run-{run_id}")
        try:
            world = e1_preflight(Path(f"compiled/{request.label}/run.yaml"))
        except (RunRefusedError, OSError) as exc:
            raise AttentionRequiredError("archive_conflict", audit_ref=f"wf-run-{run_id}") from exc
        if canonical_fingerprint(world.identities) != pre.binding.identities_fingerprint:
            # 编译出的输入、配置、代码或规则在预检查之后变了：不能冒充原运行继续
            raise AttentionRequiredError("archive_conflict", audit_ref=f"wf-run-{run_id}")
        return world, pre, confirmed, pack, rec

    def planner(self, world: RunWorld) -> Any:
        if self.planner_factory is not None:
            inner = self.planner_factory(world)
        else:
            spec = world.config.planner
            if spec.identity.kind != "mock":
                raise AttentionRequiredError("real_planner_not_supported")
            inner = mock_planner_for(spec)
        if not self.faults.wraps_planner:
            return inner
        return _InstrumentedPlanner(inner, self.faults, Path("workflow/planner_invocations.jsonl"))

    def local_block(self, code: str, profile: ContentRef, inputs: ContentRef) -> PreflightOutcome:
        """引用对不上（文件不在、内容变了、资料包变了）或编译目录名冲突：本层自己挡下，没有调用 E3。"""
        fp = fingerprint(
            {
                "workflow_local_block": code,
                "profile": profile.as_state(),
                "inputs": inputs.as_state(),
            }
        )
        _write_json(
            Path("workflow/preflights") / f"{fp}.json",
            {"kind": "workflow_local_block", "code": code, "message_zh": public_text(code)},
        )
        return PreflightOutcome(
            ref=ContentRef(kind="preflight", ref_id=fp, fingerprint=fp),
            runnable=False,
            block_code=code,
        )


class E3PreflightAdapter:
    def __init__(self, ctx: E3Context) -> None:
        self.ctx = ctx

    def check(self, profile: ContentRef, inputs: ContentRef) -> PreflightOutcome:
        ctx = self.ctx
        with ctx.ws.cwd():
            confirmed = ctx.confirmed(profile)
            request = ctx.request(inputs)
            pack = None if request is None else ctx.pack(request)
            if confirmed is None or request is None or pack is None:
                return ctx.local_block("reference_stale", profile, inputs)
            # 已登记的运行占用了这个编译目录名（归属在登记之后写下）、条件又不同：挡下，不覆盖那次运行的编译输入。
            # 还没有登记运行时可以重新编译（例如提交之前修改了同名输入）。
            owner = Path("workflow/compiled_owners") / f"{request.label}.json"
            claim = {"profile": profile.as_state(), "inputs": inputs.as_state()}
            if owner.is_file() and _read_json(owner) != claim:
                return ctx.local_block("workspace_conflict", profile, inputs)
            with recording() as rec:
                run = run_preflight(
                    pack,
                    confirmed,
                    Path(f"compiled/{request.label}"),
                    repo_root=Path("."),
                    scripts=request.scripts,
                    illness_events=request.illness_events,
                    execution=request.execution,
                )
            pre = run.preflight
            fp = pre.preflight_fingerprint
            # 显示附层：生成预检查时记下的文字绑定，放在这次运行自己的工作目录里，随运行一起删除（不进预检查的内容身份）。
            # 文件名只取身份的前 16 位（Windows 的路径长度有上限）；读取时绑定里的原消息摘要还会再核对一次。写不下来只让显示缺失
            _write_display(
                Path("workflow/display") / f"preflight-{fp[:16]}.json",
                lambda: binding_document(
                    pre.model_dump(mode="json"), rec, pack_fingerprint=pack.fingerprint
                ),
            )
            _write_json(
                Path("workflow/preflights") / f"{fp}.json",
                {
                    "kind": "e3_preflight",
                    "preflight": pre.model_dump(mode="json"),
                    "inventory": None
                    if run.inventory is None
                    else run.inventory.model_dump(mode="json"),
                    "confirmed": profile.as_state(),
                    "request": inputs.as_state(),
                },
            )
            ready = pre.status == "ready"
            return PreflightOutcome(
                ref=ContentRef(kind="preflight", ref_id=fp, fingerprint=fp),
                runnable=ready,
                block_code=None if ready else "preflight_blocked",
            )

    def describe(self, preflight: ContentRef) -> dict[str, Any]:
        with self.ctx.ws.cwd():
            rec = self.ctx.record(preflight.fingerprint)
            if rec.get("kind") == "workflow_local_block":
                return {
                    "workflow_local_check": True,
                    "code": rec["code"],
                    "message_zh": rec["message_zh"],
                }
            pre = _validated(ProductPreflight, rec.get("preflight"), "archive_conflict")
            if pre.expected_fingerprint() != preflight.fingerprint:
                raise AttentionRequiredError("archive_conflict")
            return rec["preflight"]


#: E3 的 ``RunSubmission.idempotency_key`` 字段约束原样拿来校验（不抄数字：E3 改了约束，这里跟着变）。
_KEY_FIELD = RunSubmission.model_fields["idempotency_key"]
_KEY = TypeAdapter(Annotated[(_KEY_FIELD.annotation, *_KEY_FIELD.metadata)])


class E3SubmissionAdapter:
    def __init__(self, ctx: E3Context) -> None:
        self.ctx = ctx

    def key_problems(self, idempotency_key: str) -> str | None:
        """开始流程时调用：幂等键不合 E3 的字段约束就当场拒绝，不等到用户确认之后才在登记时失败。"""
        try:
            _KEY.validate_python(idempotency_key)
        except ValidationError:
            return "idempotency_key_invalid"
        return None

    def submit(
        self, *, idempotency_key: str, preflight: ContentRef, confirmation: dict[str, Any]
    ) -> Receipt:
        ctx = self.ctx
        with ctx.ws.cwd():
            pre, confirmed, request, pack, rec = ctx.e3_inputs(preflight.fingerprint)
            if confirmation.get("preflight_fingerprint") != pre.preflight_fingerprint:
                raise AttentionRequiredError("identity_conflict")
            if self.key_problems(idempotency_key):
                raise AttentionRequiredError("idempotency_key_invalid")
            # 登记之前写下编译目录的归属：之后别的条件想用同一个名字编译，在预检查时就被挡下（不覆盖这次运行的编译输入）
            owner = Path("workflow/compiled_owners") / f"{request.label}.json"
            claim = {"profile": rec["confirmed"], "inputs": rec["request"]}
            if owner.is_file() and _read_json(owner) != claim:
                raise AttentionRequiredError("workspace_conflict")
            _write_json(owner, claim)
            submission = RunSubmission(
                meta=pack.meta(None),
                confirmed=ConfirmedRef(
                    confirmed_id=confirmed.confirmed_id,
                    content_fingerprint=confirmed.content_fingerprint,
                ),
                preflight=PreflightRef(
                    preflight_id=pre.preflight_id, preflight_fingerprint=pre.preflight_fingerprint
                ),
                idempotency_key=idempotency_key,
            )
            receipt = bridge.submit(
                pack,
                confirmed,
                pre,
                submission,
                ctx.registry if ctx.registry is not None else Path("registry"),
                service=ctx.service,
            )
            audit = "wf-receipt-" + _sha(idempotency_key.encode())[:16]
            _write_json(
                Path("workflow/receipts") / f"{audit}.json", receipt.model_dump(mode="json")
            )
            if receipt.status == "rejected":
                codes = {r.code for r in receipt.reasons}
                raise AttentionRequiredError(
                    "identity_conflict" if "idempotency_key_reused" in codes else "reference_stale",
                    audit_ref=audit,
                )
            ctx.faults.exit_if("submit:after_register")
            run_id = receipt.run_id
            assert run_id is not None
            index = {
                "run_id": run_id,
                "preflight_fingerprint": pre.preflight_fingerprint,
                "idempotency_key": idempotency_key,
            }
            path = Path("workflow/runs") / f"{run_id}.json"
            if path.is_file() and _read_json(path) != index:
                raise AttentionRequiredError("run_binding_conflict", audit_ref=f"wf-run-{run_id}")
            _write_json(path, index)
            return Receipt(
                run_id=run_id,
                preflight_fingerprint=pre.preflight_fingerprint,
                newly_registered=receipt.status == "accepted",
            )


class E1RunAdapter:
    def __init__(self, ctx: E3Context) -> None:
        self.ctx = ctx

    def inspect(self, run_id: str) -> RunInspection:
        with self.ctx.ws.cwd():
            self.ctx.world(run_id)  # 编译出的配置与 E1 运行身份没变，才谈得上查询或恢复
            return self._inspect(run_id)

    def _inspect(self, run_id: str) -> RunInspection:
        run_dir = Path("archive") / run_id
        if not run_dir.exists():
            return RunInspection(run_id=run_id, kind="not_started")
        if not (run_dir / "progress.json").is_file():
            # 启动时在建好归档目录与写下进度之间退出：不猜状态、不删目录重建，等维护者核对
            raise AttentionRequiredError("run_partially_initialized", audit_ref=f"wf-run-{run_id}")
        p = _validated(
            E1RunProgress,
            _read_json(run_dir / "progress.json"),
            "archive_conflict",
            f"wf-run-{run_id}",
        )
        if p.run_id != run_id:
            raise AttentionRequiredError("run_binding_conflict", audit_ref=f"wf-run-{run_id}")
        if p.status in ("running", "interrupted"):
            return RunInspection(run_id=run_id, kind="resumable")
        if p.status == "halted":
            code = HALT_CODES.get(p.halt_kind or "", "run_halted")
            if code is None:
                return RunInspection(run_id=run_id, kind="resumable", audit_ref=f"wf-run-{run_id}")
            return RunInspection(
                run_id=run_id, kind="needs_attention", code=code, audit_ref=f"wf-run-{run_id}"
            )
        if p.status in FINISHED_WITH_REPORT:
            accepted = "\n".join(RunStore(run_dir).bodies("accepted")).encode("utf-8")
            data = b"".join(
                accepted if name == "accepted" else (run_dir / name).read_bytes()
                for name in ("progress.json", "accepted", "reports/arm.main.json")
                if name == "accepted" or (run_dir / name).is_file()
            )
            return RunInspection(
                run_id=run_id,
                kind="completed",
                archive=ContentRef(kind="run_archive", ref_id=run_id, fingerprint=_sha(data)),
            )
        raise AttentionRequiredError("archive_conflict", audit_ref=f"wf-run-{run_id}")

    def start(self, run_id: str) -> RunInspection:
        ctx = self.ctx
        with ctx.ws.cwd():
            world, *_ = ctx.world(run_id)
            run_dir = Path("archive") / run_id
            if run_dir.exists():
                raise AttentionRequiredError("archive_conflict", audit_ref=f"wf-run-{run_id}")
            # E3B 收尾 §6：扣次数之前按真正的运行编号再核对一次全部首年请求的输入上限（旧预检查或配置变化不能绕过）；
            # 超过就不启动：没有服务调用，预留的次数由服务退回
            if any(not c.fits for c in first_year_input_checks(world, run_id)):
                raise AttentionRequiredError("request_too_large", audit_ref=f"wf-run-{run_id}")
            planner = ctx.planner(world)
            if ctx.on_start is not None:
                ctx.on_start(run_id)  # 最后一步核对（托管服务：预留转为已使用）；拒绝就不启动
            try:
                if ctx.faults.wraps_planner or ctx.hosted:
                    start_run(
                        world,
                        planner,
                        run_dir,
                        run_id=run_id,
                        workers=ctx.workers,
                        control=ctx.control,
                    )
                else:
                    # E3 原有的启动入口（产品 mock，零网络）
                    bridge.run_offline(world, run_dir, run_id)
            except RunRefusedError as exc:
                raise AttentionRequiredError(
                    "archive_conflict", audit_ref=f"wf-run-{run_id}"
                ) from exc
            seen = self._inspect(run_id)
            if seen.kind == "completed":
                ctx.faults.exit_if("life:after_complete")
            return seen

    def resume(self, run_id: str) -> RunInspection:
        ctx = self.ctx
        with ctx.ws.cwd():
            world, *_ = ctx.world(run_id)
            planner = ctx.planner(world)
            try:
                # E1 原有的恢复入口
                resume_run(
                    world,
                    planner,
                    Path("archive") / run_id,
                    workers=ctx.workers,
                    control=ctx.control,
                )
            except RunRefusedError as exc:
                raise AttentionRequiredError(
                    "archive_conflict", audit_ref=f"wf-run-{run_id}"
                ) from exc
            seen = self._inspect(run_id)
            if seen.kind == "completed":
                ctx.faults.exit_if("life:after_complete")
            return seen


class E3ProjectionAdapter:
    def __init__(self, ctx: E3Context) -> None:
        self.ctx = ctx

    def progress(self, run_id: str) -> dict[str, Any] | None:
        ctx = self.ctx
        with ctx.ws.cwd():
            idx = ctx.run_index(run_id)
            _pre, _confirmed, request, pack, _rec = ctx.e3_inputs(idx["preflight_fingerprint"])
            run_dir = Path("archive") / run_id
            if not (run_dir / "progress.json").is_file():
                return None  # 还没有开始：没有进度，不编一个
            ledger = Path("budget") if request.execution is not None else None
            return bridge.progress(
                pack, run_dir, execution=request.execution, ledger_dir=ledger
            ).model_dump(mode="json")

    def result(self, run_id: str) -> ResultView:
        ctx = self.ctx
        with ctx.ws.cwd():
            cached = Path("workflow/results") / f"{run_id}.json"
            if cached.is_file():
                result = _validated(
                    ProductResult, _read_json(cached), "archive_conflict", f"wf-result-{run_id}"
                )
                if (
                    result.result_fingerprint != result.expected_fingerprint()
                    or result.run_id != run_id
                ):
                    raise AttentionRequiredError(
                        "archive_conflict", audit_ref=f"wf-result-{run_id}"
                    )
                return _view(result)
            world, pre, confirmed, pack, rec = ctx.world(run_id)
            run_dir = Path("archive") / run_id
            p = _validated(
                E1RunProgress,
                _read_json(run_dir / "progress.json"),
                "archive_conflict",
                f"wf-result-{run_id}",
            )
            if p.status not in FINISHED_WITH_REPORT:
                raise AttentionRequiredError("result_unavailable", audit_ref=f"wf-result-{run_id}")
            try:
                outcome = outcome_from_archive(world, run_dir)
            except RunRefusedError as exc:
                raise AttentionRequiredError(
                    "archive_conflict", audit_ref=f"wf-result-{run_id}"
                ) from exc
            inventory = (
                None
                if rec["inventory"] is None
                else _validated(OptionInventoryCoverage, rec["inventory"], "archive_conflict")
            )
            sent = sum(
                1 for r in RunStore(run_dir).records("provider_calls") if r.get("event") == "intent"
            )
            with recording() as texts:
                result = project_result(
                    pack, confirmed, pre, world, outcome, inventory, real_model_calls=sent
                )
            _write_json(cached, result.model_dump(mode="json"))
            _write_display(
                Path("workflow/display") / f"result-{run_id}.json",
                lambda: _result_display(pack, pre, result, texts),
            )
            return _view(result)


#: 结果原样照抄确认过的预检查的文字：（预检查里的位置，结果里的位置）
_COPIED_FROM_PREFLIGHT = (
    ("/planner/description_zh", "/planner/description_zh"),
    ("/planner/max_cost", "/planner/max_cost"),
)


def _result_display(pack: Any, pre: Any, result: ProductResult, texts: Any) -> dict[str, Any]:
    """结果的显示绑定：用这次运行预检查时绑定的资源版本（同一次运行同一版解释）；照抄预检查的那几段按明确的位置对带过来。"""
    path = Path("workflow/display") / f"preflight-{pre.preflight_fingerprint[:16]}.json"
    pre_doc = _read_json(path) if path.is_file() else None
    body = result.model_dump(mode="json")
    return binding_document(
        body,
        texts,
        pack_fingerprint=pack.fingerprint,
        resource_version=doc_version(pre_doc),
        extra=carry_bindings(pre_doc, pre.model_dump(mode="json"), body, _COPIED_FROM_PREFLIGHT),
    )


def _write_display(path: Path, make: Any) -> None:
    """写一份显示绑定。生成或写入失败（磁盘满、资源登记表坏了……）只让这份显示缺失，不让预检查或结果失败。"""
    try:
        _write_json(path, make())
    except Exception:
        return


def _view(result: ProductResult) -> ResultView:
    return ResultView(
        ref=ContentRef(
            kind="result", ref_id=result.result_id, fingerprint=result.result_fingerprint
        ),
        public=result.model_dump(mode="json"),
    )


def e3_services(
    workspace: E3Workspace | Path | str,
    *,
    faults: set[str] | None = None,
    context: E3Context | None = None,
) -> WorkflowServices:
    """真实 E3 / E1 接入的服务组合。托管服务传自己构造的 ``context``（规划器、停止控制、启动核对与登记表）。"""
    ws = workspace if isinstance(workspace, E3Workspace) else E3Workspace(workspace)
    ctx = context if context is not None else E3Context(ws, faults=faults)
    return WorkflowServices(
        preflight=E3PreflightAdapter(ctx),
        submission=E3SubmissionAdapter(ctx),
        life=E1RunAdapter(ctx),
        projection=E3ProjectionAdapter(ctx),
        label="e3",
    )


# ---------------------------------------------------------------------- 合成样例（演示与测试共用；全部是合成人物）

_ILLNESS = {"mode": "injected", "stream_version": "e1-5c-illness-stream-v1", "note": "受控夹具"}
EXAMPLES: dict[str, dict[str, Any]] = {
    # 与 E3 的测试场景（tests/product/test_offline_results.py）同一组合成输入
    "normal": {"person": "u501"},
    "mixed": {
        "person": "u603",
        "repeat_indexes": (0, 1),
        "illness": [
            {
                "person_id": "u603",
                "repeat_index": 1,
                "year_index": 1,
                "month": 1,
                "severity": "major",
                "note": "受控夹具：大病",
            }
        ],
        "scripts": [{"clone_id": "u603.m52.r0", "behavior": "malformed_output", "years": [1]}],
    },
    "all_exit": {
        "person": "u604",
        "draft": {"hard_requirements": (), "cash": ("500", "USD", "2024")},
        "illness": [
            {
                "person_id": "u604",
                "repeat_index": 0,
                "year_index": 1,
                "month": 1,
                "severity": "major",
                "note": "受控夹具：大病",
            }
        ],
    },
    "paused": {
        "person": "u606",
        "scripts": [{"clone_id": "u606.k51.r0", "behavior": "raise_error", "years": [1]}],
    },
    # E3B 六城真实资料包（DECISIONS/0042）：人物是合成的，城市资料是真实资料包
    "e3b_normal": {
        "person": "u701",
        "pack": E3B_PACK_PATH,
        "repeat_indexes": (0, 1, 2, 3),
        "draft": {"abilities": ("edu_at_least_hs",)},
    },
    "e3b_mixed": {
        "person": "u702",
        "pack": E3B_PACK_PATH,
        "repeat_indexes": (0, 1),
        "draft": {
            "occupation_text": "我在门店做售货员，接待顾客、整理货架",
            "selected": "occ.retail_sales",
            "hard_requirements": (),
        },
        "illness": [
            {
                "person_id": "u702",
                "repeat_index": 1,
                "year_index": 1,
                "month": 1,
                "severity": "major",
                "note": "受控夹具：大病",
            }
        ],
        "scripts": [{"clone_id": "u702.41860.r0", "behavior": "malformed_output", "years": [1]}],
    },
    "e3b_exit": {
        "person": "u703",
        "pack": E3B_PACK_PATH,
        "draft": {
            "occupation_text": "我在快餐店柜台点单、做饮品",
            "selected": "occ.counter_drinks",
            "hard_requirements": (),
            "cash": ("300", "USD", "2024"),
        },
        "illness": [
            {
                "person_id": "u703",
                "repeat_index": 0,
                "year_index": 1,
                "month": 1,
                "severity": "major",
                "note": "受控夹具：大病",
            }
        ],
    },
    "e3b_paused": {
        "person": "u704",
        "pack": E3B_PACK_PATH,
        "draft": {"abilities": ("edu_at_least_hs",)},
        "scripts": [{"clone_id": "u704.16980.r0", "behavior": "raise_error", "years": [1]}],
    },
}


def seed_example(
    ws: E3Workspace, example: str, *, name: str | None = None
) -> tuple[ContentRef, ContentRef]:
    """用 E3 的正式入口（草稿 → 审阅 → 确认）造一份合成的已确认画像，再写一份本次输入。返回两个引用。"""
    from product.answers import confirm_profile, review_draft
    from product.fixtures import make_draft

    spec = EXAMPLES[example]
    name = name or example
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", name):
        raise ValueError(f"名字只能用小写字母、数字、下划线与连字符：{name!r}")
    person = spec["person"]
    pack_path = spec.get("pack", DEFAULT_PACK_PATH)
    with ws.cwd():
        pack = load_pack(Path(pack_path))
        file_fp = pack.fingerprint
        if spec.get("repeat_indexes") is not None:
            raw = copy.deepcopy(pack.raw)
            raw["pack"]["run"]["repeat_indexes"] = list(spec["repeat_indexes"])
            pack = DataPack(raw, pack.path)
        draft = make_draft(pack, draft_id=f"draft.{person}", **spec.get("draft", {}))
        review = review_draft(pack, draft)
        confirmed = confirm_profile(
            pack, draft, person_ref=person, acknowledged=review.pending_confirmations
        )
    profile = ws.save_confirmed(confirmed)
    request = RunRequest(
        version=RUN_REQUEST_VERSION,
        label=name,
        pack_path=pack_path,
        pack_fingerprint=file_fp,
        repeat_indexes=spec.get("repeat_indexes"),
        scripts=tuple(MockScript(**s) for s in spec.get("scripts", ())),
        illness_events=None if "illness" not in spec else {**_ILLNESS, "injected": spec["illness"]},
        note=f"合成人物样例 {example}（资料包 {pack_path}）",
    )
    return profile, ws.save_request(name, request)
