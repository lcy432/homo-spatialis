"""合成替身：用文件模拟 E3 的预检查、提交、投影与 E1 的运行，只用来检验外层流程本身。

**这不是 E1，也不是 E3。** 没有生活数学、没有评分、没有模型调用；“规划调用”只是一个计数器加一份保存的假回复。它的作用是
让流程测试能在真实的跨进程条件下数清楚：登记了几个运行、每一年执行了几次、“规划调用”发生了几次，以及在指定位置真的
崩溃（``os._exit``，不写任何收尾）之后，恢复会不会重复执行。

根目录布局（全部是 UTF-8 JSON，LF 换行）::

    inputs/<名字>.json          画像或输入（ContentRef 的 ref_id 就是名字，fingerprint 是文件字节的 SHA-256）
    preflights/<身份>.json      预检查记录（冻结当时的输入）
    submissions.json            幂等键 → {预检查身份, 运行编号}
    runs/<运行编号>/run.json     运行状态；run.seal 是 run.json 的 SHA-256（被改动就是归档冲突）
    runs/<运行编号>/replies/     每年保存的“规划回复”（恢复时复用，不再计一次调用）
    runs/<运行编号>/executions.jsonl  每执行一个年度追加一行（测试用它数重复执行）
    runs/<运行编号>/result.json  运行结束时写的“结果”

输入文件的字段（合成）：``years``（年数）、``cities``、``outcomes``（每城的 C / E / M 占位分类）、``max_paid_calls``、
``blocked``（预检查不通过）、``halt``（``{"after_year": k, "kind": service_unavailable | uncertain_send | budget_blocked |
model_mismatch}``，在第 k 年之后按 E1 的方式停下）。

故障注入：构造参数 ``faults`` 或环境变量 ``HS_WF_FAULT``（逗号分隔）：

* ``submit:after_register``：登记写完后进程立刻退出（退出码 41）；
* ``life:mid_year:<k>``：第 k 年的“规划回复”保存后、年度执行前退出（42）；
* ``life:after_complete``：运行结果写完后、返回之前退出（43）；
* ``projection:raise``：读结果时抛出带哨兵文字与路径的异常（检验公开视图不泄漏）。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from workflows.ports import (
    AttentionRequiredError,
    PreflightOutcome,
    Receipt,
    ResultView,
    RunInspection,
    WorkflowServices,
)
from workflows.state import ContentRef, fingerprint

SYNTHETIC_PREFLIGHT_VERSION = "synthetic-preflight-v0"
LEAK_SENTINEL = "SENTINEL-INTERNAL C:/Users/someone/secret/path.txt"
EXIT_CODES = {"submit:after_register": 41, "life:mid_year": 42, "life:after_complete": 43}


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class SyntheticWorld:
    """合成根目录 + 四个替身服务。"""

    def __init__(self, root: Path | str, *, faults: set[str] | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        env = {f for f in os.environ.get("HS_WF_FAULT", "").split(",") if f}
        self.faults = set(faults or set()) | env

    # ---- 准备输入 ----
    def write_input(self, name: str, obj: dict[str, Any], *, kind: str) -> ContentRef:
        path = self.root / "inputs" / f"{name}.json"
        _write_json(path, obj)
        return self.ref(name, kind=kind)

    def ref(self, name: str, *, kind: str) -> ContentRef:
        path = self.root / "inputs" / f"{name}.json"
        return ContentRef(kind=kind, ref_id=name, fingerprint=_sha_bytes(path.read_bytes()))

    def services(self) -> WorkflowServices:
        return WorkflowServices(
            preflight=_Preflight(self),
            submission=_Submission(self),
            life=_Life(self),
            projection=_Projection(self),
            label="synthetic",
        )

    # ---- 测试用的计数 ----
    def registry(self) -> dict[str, Any]:
        path = self.root / "submissions.json"
        return _read_json(path) if path.is_file() else {}

    def run_dirs(self) -> list[str]:
        base = self.root / "runs"
        return sorted(p.name for p in base.iterdir()) if base.is_dir() else []

    def executions(self, run_id: str) -> list[dict[str, Any]]:
        path = self.root / "runs" / run_id / "executions.jsonl"
        if not path.is_file():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]

    def run_state(self, run_id: str) -> dict[str, Any]:
        return _read_json(self.root / "runs" / run_id / "run.json")

    # ---- 内部 ----
    def _fault(self, name: str) -> None:
        if name in self.faults:
            os._exit(EXIT_CODES[name])

    def _run_dir(self, run_id: str) -> Path:
        return self.root / "runs" / run_id

    def _save_run(self, run: dict[str, Any]) -> None:
        d = self._run_dir(run["run_id"])
        _write_json(d / "run.json", run)
        (d / "run.seal").write_text(
            _sha_bytes((d / "run.json").read_bytes()), encoding="utf-8", newline="\n"
        )

    def _load_run(self, run_id: str) -> dict[str, Any]:
        d = self._run_dir(run_id)
        if not (d / "run.json").is_file() or not (d / "run.seal").is_file():
            raise AttentionRequiredError("archive_conflict", audit_ref=f"syn-missing-{run_id}")
        if (
            _sha_bytes((d / "run.json").read_bytes())
            != (d / "run.seal").read_text(encoding="utf-8").strip()
        ):
            raise AttentionRequiredError("archive_conflict", audit_ref=f"syn-seal-{run_id}")
        return _read_json(d / "run.json")


class _Preflight:
    def __init__(self, world: SyntheticWorld) -> None:
        self.w = world

    def check(self, profile: ContentRef, inputs: ContentRef) -> PreflightOutcome:
        current_profile = self.w.ref(profile.ref_id, kind=profile.kind)
        current_inputs = self.w.ref(inputs.ref_id, kind=inputs.kind)
        data = _read_json(self.w.root / "inputs" / f"{inputs.ref_id}.json")
        body = {
            "version": SYNTHETIC_PREFLIGHT_VERSION,
            "profile": current_profile.fingerprint,
            "inputs": current_inputs.fingerprint,
            "frozen_inputs": data,
        }
        fp = fingerprint(body)
        if current_profile != profile or current_inputs != inputs:
            block = "reference_stale"
        elif data.get("blocked"):
            block = "preflight_blocked"
        else:
            block = None
        _write_json(self.w.root / "preflights" / f"{fp}.json", {**body, "block": block})
        return PreflightOutcome(
            ref=ContentRef(kind="preflight", ref_id=fp, fingerprint=fp),
            runnable=block is None,
            block_code=block,
        )

    def describe(self, preflight: ContentRef) -> dict[str, Any]:
        rec = _read_json(self.w.root / "preflights" / f"{preflight.ref_id}.json")
        data = rec["frozen_inputs"]
        cities = list(data.get("cities", []))
        years = int(data.get("years", 0))
        return {
            "placeholder": True,
            "summary_zh": (
                f"{len(cities)} 座城市 × 每城 1 个分身 × {years} 年；最多 {data.get('max_paid_calls')} 次规划请求"
                "（合成替身：不调用任何模型，不代表 E3 的真实运行前检查）"
            ),
            "cities": cities,
            "years": years,
            "max_paid_calls": data.get("max_paid_calls"),
            "block_code": rec.get("block"),
        }


class _Submission:
    def __init__(self, world: SyntheticWorld) -> None:
        self.w = world

    def submit(
        self, *, idempotency_key: str, preflight: ContentRef, confirmation: dict[str, Any]
    ) -> Receipt:
        registry = self.w.registry()
        if idempotency_key in registry:
            entry = registry[idempotency_key]
            if entry["preflight_fingerprint"] != preflight.fingerprint:
                raise AttentionRequiredError(
                    "identity_conflict", audit_ref=f"syn-key-{idempotency_key[:12]}"
                )
            return Receipt(
                run_id=entry["run_id"],
                preflight_fingerprint=entry["preflight_fingerprint"],
                newly_registered=False,
            )
        if confirmation.get("preflight_fingerprint") != preflight.fingerprint:
            raise AttentionRequiredError("identity_conflict")
        run_id = (
            "syn-" + fingerprint({"key": idempotency_key, "preflight": preflight.fingerprint})[:16]
        )
        d = self.w._run_dir(run_id)
        if (d / "run.json").is_file():
            existing = self.w._load_run(run_id)
            if existing["preflight_fingerprint"] != preflight.fingerprint:
                raise AttentionRequiredError("run_binding_conflict")
        else:
            rec = _read_json(self.w.root / "preflights" / f"{preflight.ref_id}.json")
            self.w._save_run(
                {
                    "run_id": run_id,
                    "preflight_fingerprint": preflight.fingerprint,
                    "frozen_inputs": rec["frozen_inputs"],
                    "status": "not_started",
                    "years_done": [],
                    "planner_calls": 0,
                    "halts_consumed": [],
                    "halt_kind": None,
                }
            )
        registry[idempotency_key] = {
            "preflight_fingerprint": preflight.fingerprint,
            "run_id": run_id,
        }
        _write_json(self.w.root / "submissions.json", registry)
        self.w._fault("submit:after_register")
        return Receipt(
            run_id=run_id, preflight_fingerprint=preflight.fingerprint, newly_registered=True
        )


class _Life:
    """模拟 E1 的启动 / 恢复语义：年度之间可以恢复；已保存的回复复用；停止按 E1 的方式分成可恢复与需要处理。"""

    def __init__(self, world: SyntheticWorld) -> None:
        self.w = world

    def inspect(self, run_id: str) -> RunInspection:
        run = self.w._load_run(run_id)
        status = run["status"]
        if status == "not_started":
            return RunInspection(run_id=run_id, kind="not_started")
        if status == "completed":
            return RunInspection(run_id=run_id, kind="completed", archive=self._archive(run_id))
        if status == "running":  # 进程在两个支持的边界之间退出：从已保存的记录接着跑
            return RunInspection(run_id=run_id, kind="resumable")
        if status == "halted":
            kind = run["halt_kind"]
            if kind == "service_unavailable":
                return RunInspection(
                    run_id=run_id, kind="resumable", audit_ref=f"syn-halt-{run_id}"
                )
            return RunInspection(
                run_id=run_id, kind="needs_attention", code=kind, audit_ref=f"syn-halt-{run_id}"
            )
        raise AttentionRequiredError("archive_conflict")

    def start(self, run_id: str) -> RunInspection:
        run = self.w._load_run(run_id)
        if run["status"] != "not_started":
            raise AttentionRequiredError("archive_conflict", audit_ref=f"syn-restart-{run_id}")
        run["status"] = "running"
        self.w._save_run(run)
        return self._advance(run)

    def resume(self, run_id: str) -> RunInspection:
        run = self.w._load_run(run_id)
        if run["status"] == "halted" and run["halt_kind"] == "service_unavailable":
            run["halts_consumed"].append(run["years_done"][-1] if run["years_done"] else 0)
            run["status"], run["halt_kind"] = "running", None
            self.w._save_run(run)
        elif run["status"] != "running":
            raise AttentionRequiredError("archive_conflict", audit_ref=f"syn-resume-{run_id}")
        return self._advance(run)

    def _advance(self, run: dict[str, Any]) -> RunInspection:
        run_id = run["run_id"]
        d = self.w._run_dir(run_id)
        data = run["frozen_inputs"]
        halt = data.get("halt") or {}
        for year in range(1, int(data["years"]) + 1):
            if year in run["years_done"]:
                continue
            reply_path = d / "replies" / f"year_{year}.json"
            if not reply_path.is_file():
                if run["planner_calls"] + 1 > int(data.get("max_paid_calls", 0)):
                    run["status"], run["halt_kind"] = "halted", "budget_blocked"
                    self.w._save_run(run)
                    return self.inspect(run_id)
                run["planner_calls"] += 1
                self.w._save_run(run)
                _write_json(reply_path, {"year": year, "plan": f"synthetic plan for year {year}"})
            if f"life:mid_year:{year}" in self.w.faults:
                os._exit(EXIT_CODES["life:mid_year"])
            reply_fp = _sha_bytes(reply_path.read_bytes())
            with (d / "executions.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(
                    json.dumps({"run_id": run_id, "year": year, "reply": reply_fp}, sort_keys=True)
                    + "\n"
                )
            run["years_done"].append(year)
            self.w._save_run(run)
            if halt.get("after_year") == year and year not in run["halts_consumed"]:
                run["status"], run["halt_kind"] = "halted", halt["kind"]
                self.w._save_run(run)
                return self.inspect(run_id)
        outcomes = data.get("outcomes", {})
        _write_json(
            d / "result.json",
            {
                "run_id": run_id,
                "years": int(data["years"]),
                "cities": {c: {"category": outcomes.get(c, "M")} for c in data.get("cities", [])},
            },
        )
        run["status"] = "completed"
        self.w._save_run(run)
        self.w._fault("life:after_complete")
        return self.inspect(run_id)

    def _archive(self, run_id: str) -> ContentRef:
        d = self.w._run_dir(run_id)
        data = (d / "executions.jsonl").read_bytes() + b"\n--\n" + (d / "result.json").read_bytes()
        return ContentRef(kind="run_archive", ref_id=run_id, fingerprint=_sha_bytes(data))


class _Projection:
    def __init__(self, world: SyntheticWorld) -> None:
        self.w = world

    def progress(self, run_id: str) -> dict[str, Any]:
        run = self.w._load_run(run_id)
        return {
            "placeholder": True,
            "run_id": run_id,
            "status": run["status"],
            "halt_kind": run["halt_kind"],
            "years_done": list(run["years_done"]),
            "years_total": int(run["frozen_inputs"]["years"]),
            "planner_calls": run["planner_calls"],
        }

    def result(self, run_id: str) -> ResultView:
        if "projection:raise" in self.w.faults:
            raise RuntimeError(LEAK_SENTINEL)
        run = self.w._load_run(run_id)
        path = self.w._run_dir(run_id) / "result.json"
        if run["status"] != "completed" or not path.is_file():
            raise AttentionRequiredError("result_unavailable")
        raw = path.read_bytes()
        body = json.loads(raw.decode("utf-8"))
        counts = {
            k: sum(1 for c in body["cities"].values() if c["category"] == k)
            for k in ("C", "E", "M")
        }
        public = {
            "placeholder": True,
            "run_id": run_id,
            "cities": body["cities"],
            "counts": {**counts, "N": sum(counts.values())},
            "note_zh": "合成替身的占位结果：分类照输入抄写，没有任何生活计算；真实结果由 E3 投影 E1 的评价与汇总。",
        }
        return ResultView(
            ref=ContentRef(kind="result", ref_id=run_id, fingerprint=_sha_bytes(raw)), public=public
        )
