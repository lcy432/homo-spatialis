"""E1-7B 真实模型规划器（`DECISIONS/0031`）：千问（DashScope 的 OpenAI 兼容端点），实现与 mock 相同的 ``plan()`` 与 ``effective_spec()``。

它只接收运行器交来的 ``PlannerCall``——由安全投影产生的请求、渲染好的提示与同一份 JSON——不接收运行 bundle、研究真值或评分配置。
它不给模型浏览、执行代码或调用外部工具的能力；结构化输出（JSON object 模式）只帮助保证形状，资格、资金、时间与精力照旧由环境的
检查器判断。

一份候选（一次规划调用）对应 1–2 次实际服务请求：

    估计输入 token（保守规则 ascii1_nonascii2_v1）→ 超过上限就在本地停下，不发送
    → 查本运行的请求明细（``service_history``：新回复、缓存、恢复与重放共用的一份一致性检查）：这份候选已经有**可用的**
      成功回复就直接用（恢复时不重新请求）；有被拒的回复（返回模型不符合 ``returned_model_accepted`` 的规则）就停下，不重试也不换模型；有"已发出、结果没落盘"的
      就停下（不确定）；明细与这次请求对不上就拒绝（``ServiceRecordMismatchError``），不发送、不使用
    → 付费预算预留（次数与费用都按保守上界先扣；不够就停下，不发送）→ 写 intent → 发送（SDK 自动重试关闭）
    → 写 outcome → 按实际用量核算费用；没有用量（超时、断连、出错）保留预留、记为"费用不确定"，不记零
    → 可重试的服务错误（超时、连接、限流、服务端错误）最多再试一次，等待有上限；凭证、账户、请求配置错误不重试，直接停下

服务失败、预算不够、输入超限、发送状态不确定都抛 ``PlannerHalt``：运行器保存现场并停下——它们不是生活结果，也不消耗计划修订的
机会；计划修订也不会得到额外的服务重试额度。回复为空、被截断、不是 JSON 都照常交回，由运行器按格式问题进入有限的修订。

日志不保存密钥、鉴权头、环境变量或包含凭证的配置对象；异常只记类型、状态码与供应商错误码。

两层接口（2026-10-18，``DECISIONS/0035``）：生活引擎只认规划器接口（``plan()`` / ``effective_spec()``，mock 与真实模型都实现）；真实模型
规划器 ``LlmPlanner``（旧名 ``QwenPlanner`` 保留为别名）只认传输层接口 ``Transport.send(请求体, 超时)``。厂商相关的只有三样，按设定的
``provider`` 选：请求体怎样描述（``_provider_body``）、怎样发送（``OpenAICompatibleTransport`` 给千问，``ClaudeAgentSdkTransport`` 给
Claude）、错误与用量怎样翻译回统一的 ``TransportResponse`` / ``TransportError``。预算预留、服务记录、一致性核对与重试次数都在传输层
之上，与厂商无关。计费是可选的：规划器的预算闸门可以不传（运行配置写 ``unmetered_reason``），这时照常调用，费用记为未计量。

Claude Agent SDK 只当作"一个输入、跑一轮、一个输出"的 API 密钥调用方式：``max_turns=1``、不给工具、不读任何设置文件、每次新建空的
工作目录与配置目录；命令行进程只继承白名单里的系统变量，用项目自己的 ``ANTHROPIC_API_KEY`` 与官方端点——不继承桌面 App 注入的
端点与登录变量，不用所有者的 Claude 账号登录态。命令行会另外往真实请求里加一句 Agent SDK 自我介绍与一段运行环境说明，并把思考固定
为自适应；这些由本机抓包核对并写进 ADR0035，请求明细里记的是交给 SDK 的那一份。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agentlayer.life_planning import (
    PROMPT_USER_HEAD,
    PlannerCall,
    PlannerHalt,
    PlannerReply,
    developer_note_problems,
    render_prompt,
)
from agentlayer.life_views import FORBIDDEN_PAYLOAD_KEYS, assert_payload_clean
from common.config import DATED_SUFFIX
from common.life_contracts import LifeContractError, canonical_fingerprint
from common.life_planning_contracts import (
    TOKEN_ESTIMATE_RULE,
    LlmSettings,
    PlannerIdentity,
    PlannerSpec,
)
from common.run_store import RunStore
from common.runlog import ProviderCallRecord

LLM_PLANNER_ID = "llm.qwen.base_b"
#: 规划器编号随 ``model_role`` 定（2026-10-13，DECISIONS/0032）：研究基座 base_b 照旧 ``llm.qwen.base_b``，产品规划角色另有编号。
LLM_PLANNER_IDS = {
    "base_b": LLM_PLANNER_ID,
    "product_planner": "llm.qwen.product_planner",
    "test_planner": "llm.claude.test_planner",
    "test_planner_sonnet": "llm.claude.test_planner_sonnet",
}
LLM_PLANNER_VERSION = "e1-7b-llm-v1"
LLM_IDENTITY_NOTE = "真实模型规划器：DashScope OpenAI 兼容端点上的千问固定快照；只收安全投影的请求，计划由环境检查与结算"
#: 滚动型号的身份说明（2026-10-13，DECISIONS/0032）：固定版本的说明保持原样，所以 Max 配置的身份不变。
LLM_IDENTITY_NOTE_ROLLING = "真实模型规划器：DashScope OpenAI 兼容端点上的千问滚动型号（没有带日期的快照）；只收安全投影的请求，计划由环境检查与结算"
#: 测试规划器的身份说明（2026-10-18，DECISIONS/0035）。千问的两句说明保持原样，所以 Max 与 Flash 的身份不变。
LLM_IDENTITY_NOTE_CLAUDE = (
    "真实模型规划器：Claude Agent SDK 单轮调用（一个输入、一轮、一个输出；无工具、不读设置、隔离环境）上的 Claude 滚动型号"
    "（没有带日期的快照）；只收安全投影的请求，计划由环境检查与结算"
)


def llm_identity(settings: LlmSettings) -> PlannerIdentity:
    """真实模型规划器的身份（运行配置必须声明同一份）。"""
    return PlannerIdentity(
        kind="llm",
        planner_id=LLM_PLANNER_IDS[settings.model_role],
        version=LLM_PLANNER_VERSION,
        model_snapshot=settings.model,
        note=LLM_IDENTITY_NOTE_CLAUDE
        if settings.provider == "claude_agent_sdk"
        else LLM_IDENTITY_NOTE_ROLLING
        if settings.effective_version_policy == "rolling"
        else LLM_IDENTITY_NOTE,
    )


def returned_model_accepted(settings: LlmSettings, returned: str | None) -> bool:
    """服务返回的模型名能不能接受（2026-10-13，DECISIONS/0032）。规则只有两条，写死在这里：

    * 固定版本（``dated_snapshot``，默认）：返回名必须与请求名**完全相同**；
    * 滚动型号（``rolling``，只在产品规划角色）：返回名等于请求名，或者是请求名后面接一个快照日期
      （``-YYYY-MM-DD`` / ``-YYYYMMDD`` / ``-MMDD``，即同一个滚动名当时实际指向的快照）。别的任何名字——别的型号、``-latest``、
      别的后缀——都不接受。实际返回的名字逐次记进请求明细，复现时以它为准。"""
    if not returned:
        return False
    if returned == settings.model:
        return True
    if settings.effective_version_policy != "rolling":
        return False
    return re.fullmatch(re.escape(settings.model) + DATED_SUFFIX, returned) is not None


# ---------------------------------------------------------------------- 输入 token 的保守估计


def estimate_input_tokens(messages: Sequence[dict[str, str]]) -> int:
    """输入 token 的保守估计（规则 ``ascii1_nonascii2_v1``，见 ``common.life_planning_contracts.TOKEN_ESTIMATE_RULE``）。

    ASCII 字符每个记 1（字节级 BPE 的 ASCII 部分的严格上界），非 ASCII 字符每个记 2（常用汉字在千问词表里多是 1 个 token；
    罕见字可能更多，所以这**不是**严格上界），每条消息另加 16、整个请求另加 16。它是估计，不是实际 token；实际 token 只认
    供应商返回的用量，两者分开记录，事后核对估计有没有低于实际。"""
    assert TOKEN_ESTIMATE_RULE == "ascii1_nonascii2_v1"
    total = 16
    for m in messages:
        text = m["content"]
        ascii_n = sum(1 for ch in text if ord(ch) < 128)
        total += ascii_n + 2 * (len(text) - ascii_n) + 16
    return total


# ---------------------------------------------------------------------- 传输层（可替换；离线测试用替身）


@dataclass(frozen=True)
class TransportResponse:
    text: str
    finish_reason: str | None
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_chars: int = 0
    #: 服务商的原始用量明细（只作核对，不作扣额度依据）；千问不填
    provider_usage: dict[str, Any] | None = None


class TransportError(Exception):
    """一次服务请求失败。只带安全字段：种类、是否可重试、HTTP 状态码、供应商错误码（短的、只含字母数字的）。"""

    def __init__(
        self,
        kind: str,
        *,
        retryable: bool,
        http_status: int | None = None,
        provider_code: str | None = None,
    ) -> None:
        super().__init__(kind)
        self.kind, self.retryable = kind, retryable
        self.http_status, self.provider_code = http_status, provider_code


class Transport(Protocol):
    def send(self, body: dict[str, Any], *, timeout: float) -> TransportResponse: ...


#: 账户类的供应商错误码（欠费、额度用完）：不重试，停下等所有者处理。
_ACCOUNT_CODES = frozenset(
    {"Arrearage", "insufficient_quota", "QuotaExhausted", "AccountOverdue", "AllocationQuota"}
)
_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _provider_code(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        nested = body.get("error", body)
        if isinstance(nested, dict):
            code = nested.get("code")
            if isinstance(code, str) and _SAFE_CODE.match(code):
                return code
    code = getattr(exc, "code", None)
    if isinstance(code, str) and _SAFE_CODE.match(code):
        return code
    return None


def classify_sdk_error(exc: Exception) -> TransportError | None:
    """把 OpenAI SDK 的异常换成安全的 ``TransportError``；不是 SDK 的服务错误就返回 None（由调用方当程序错误处理）。"""
    import openai

    status = getattr(exc, "status_code", None)
    code = _provider_code(exc)
    if code in _ACCOUNT_CODES:
        return TransportError("account", retryable=False, http_status=status, provider_code=code)
    if isinstance(exc, openai.APITimeoutError):
        return TransportError("timeout", retryable=True, provider_code=code)
    if isinstance(exc, openai.APIConnectionError):
        return TransportError("connection", retryable=True, provider_code=code)
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return TransportError(
            "credentials", retryable=False, http_status=status, provider_code=code
        )
    if isinstance(exc, openai.RateLimitError):
        return TransportError(
            "rate_limited", retryable=True, http_status=status, provider_code=code
        )
    if isinstance(exc, openai.InternalServerError):
        return TransportError(
            "server_error", retryable=True, http_status=status, provider_code=code
        )
    if isinstance(exc, openai.APIStatusError):
        return TransportError(
            "bad_request", retryable=False, http_status=status, provider_code=code
        )
    return None


class OpenAICompatibleTransport:
    """真实传输：OpenAI 兼容 SDK，SDK 自动重试关闭（``max_retries=0``），超时显式给出。密钥只在这里持有，不进任何记录；
    发送前确认请求体里没有密钥。"""

    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: float) -> None:
        from openai import OpenAI

        self._key = api_key
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=0
        )

    def send(self, body: dict[str, Any], *, timeout: float) -> TransportResponse:
        if self._key and self._key in json.dumps(body, ensure_ascii=False):
            raise TransportError("request_check", retryable=False)
        try:
            reply = self._client.chat.completions.create(timeout=timeout, **body)
        except Exception as exc:
            mapped = classify_sdk_error(exc)
            if mapped is None:
                raise
            raise mapped from None
        choice = reply.choices[0] if reply.choices else None
        usage = reply.usage
        message = None if choice is None else choice.message
        return TransportResponse(
            text="" if message is None else (message.content or ""),
            finish_reason=None if choice is None else choice.finish_reason,
            model=reply.model,
            prompt_tokens=None if usage is None else usage.prompt_tokens,
            completion_tokens=None if usage is None else usage.completion_tokens,
            reasoning_chars=len(getattr(message, "reasoning_content", "") or ""),
        )

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------- 付费预算（实现在 orchestration.life_budget）


class BudgetRefused(Exception):  # noqa: N818 - 这是预算的"不能再发"，不是程序错误
    """预算不够发送下一次请求（次数或费用）。"""


class ServiceRecordMismatchError(LifeContractError):
    """服务请求明细与这次请求对不上（E1-7B 收尾 F1）：不是这个运行、这份候选、这套设定或这份请求正文的记录，或者发送意图与结果
    配不上。运行器把它当作归档问题拒绝继续——在追加任何运行记录、执行生活或再调用规划器之前。"""


class BudgetDuplicate(BudgetRefused):
    """同一次服务尝试在账本里已经有预留：可能是上次预留之后、写发送记录之前中断。不自动重发，由规划器按"发送状态不确定"停下。"""


class BudgetGate(Protocol):
    price_version: str

    def reserve(
        self,
        *,
        key: str,
        run_id: str,
        request_id: str,
        service_attempt: int,
        input_tokens_estimate: int,
        max_output_tokens: int,
    ) -> str: ...

    def reservation(self, key: str) -> dict[str, Any] | None: ...

    def settle(self, *, key: str, prompt_tokens: int, completion_tokens: int) -> str: ...

    def settle_uncertain(self, *, key: str, reason: str) -> str: ...


# ---------------------------------------------------------------------- 请求明细（归档库的 provider_calls 表）


class ProviderCallLog:
    """本运行的服务请求明细：运行目录里 ``archive.sqlite`` 的 ``provider_calls`` 表（``common.run_store``）。追加写入、事务
    提交即落盘；配对与一致性由 ``service_history`` 统一核对。"""

    def __init__(self, run_dir: Path) -> None:
        self.store = RunStore(Path(run_dir))

    def append(self, record: ProviderCallRecord) -> None:
        self.store.append("provider_calls", record.model_dump_json())

    def records(self) -> list[ProviderCallRecord]:
        return [
            ProviderCallRecord.model_validate_json(b) for b in self.store.bodies("provider_calls")
        ]


# ---------------------------------------------------------------------- 服务记录的一致性（E1-7B 收尾 F1）


def expected_provider_body(settings: LlmSettings, payload: dict[str, Any]) -> dict[str, Any]:
    """一份安全载荷、一套设定 → 真正发给供应商的请求体。只有这一份写法：新请求、缓存核对、恢复与重放都用它。"""
    system, user = render_prompt(payload)
    return _provider_body(settings, system, user)


def _provider_body(settings: LlmSettings, system: str, user: str) -> dict[str, Any]:
    """按服务商写请求体。两种写法共用同一对消息（系统提示、用户提示），所以最终请求检查、载荷取回与输入估计照旧适用。

    * 千问：OpenAI 兼容端点的请求体本身（写法与之前逐字节相同）；
    * Claude Agent SDK：交给 SDK 的那一份——模型、两条消息、输出上限与单轮调用的选项。命令行由它拼出真正的 HTTP 正文（另加一句
      Agent SDK 自我介绍与一段运行环境说明，思考固定为自适应），这些不在这里，见 ADR0035 与本机抓包证据。"""
    if settings.provider == "claude_agent_sdk":
        return {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": settings.max_output_tokens,
            "agent_sdk": {
                "max_turns": 1,
                "tools": [],
                "setting_sources": [],
                "strict_mcp_config": True,
                "thinking": settings.thinking,
                "effort": settings.effort,
                "prompt_caching": False,
                "response_format": settings.response_format,
            },
        }
    return {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": settings.temperature,
        "max_tokens": settings.max_output_tokens,
        "response_format": {"type": settings.response_format},
        "extra_body": {"enable_thinking": settings.enable_thinking},
    }


def settings_fingerprint(settings: LlmSettings) -> str:
    return canonical_fingerprint(settings.model_dump(mode="json", exclude={"note"}))


_REQUEST_ID = re.compile(r"^(?P<run>.+)/(?P<clone>[^/]+)/y(?P<year>\d+)/a(?P<cand>\d+)$")


def _payload_from_body(body: Any) -> dict[str, Any] | None:
    """从请求体的用户提示里取回嵌入的 JSON 载荷（渲染只加了固定的开头与代码块标记）；取不回就是 None。"""
    try:
        user = body["messages"][1]["content"]
        head = PROMPT_USER_HEAD + "```json\n"
        if not (isinstance(user, str) and user.startswith(head) and user.endswith("\n```")):
            return None
        payload = json.loads(user[len(head) : -len("\n```")])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


@dataclass(frozen=True)
class ServiceAttempt:
    number: int
    intent: ProviderCallRecord
    outcome: ProviderCallRecord | None


@dataclass(frozen=True)
class ServiceHistory:
    """一个请求编号的服务记录经过一致性检查之后的结论。``problems`` 不为空时，其余字段都不能用。"""

    request_id: str
    attempts: tuple[ServiceAttempt, ...]
    problems: tuple[str, ...]
    settings: LlmSettings

    def _last(self) -> ServiceAttempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def usable(self) -> ServiceAttempt | None:
        """返回模型符合核对规则（returned_model_accepted）的成功回复：可以用来规划。"""
        a = self._last()
        if (
            a
            and a.outcome is not None
            and a.outcome.status == "ok"
            and returned_model_accepted(self.settings, a.outcome.returned_model)
        ):
            return a
        return None

    @property
    def rejected(self) -> ServiceAttempt | None:
        """成功返回、但返回模型不符合核对规则：服务与费用事实保留，回复不能用。"""
        a = self._last()
        if (
            a
            and a.outcome is not None
            and a.outcome.status == "ok"
            and not returned_model_accepted(self.settings, a.outcome.returned_model)
        ):
            return a
        return None

    @property
    def pending(self) -> ServiceAttempt | None:
        """已经写了发送意图、没有结果：可能已经发出，结果不明。"""
        a = self._last()
        return a if a and a.outcome is None else None


def service_history(
    records: Sequence[ProviderCallRecord],
    *,
    request_id: str,
    run_id: str,
    settings: LlmSettings,
    price_version: str | None,
    expected_body: dict[str, Any] | None = None,
) -> ServiceHistory:
    """一个请求编号的全部服务记录是否一致（E1-7B 收尾 F1）。新回复写进明细之后、缓存命中、恢复与重放都只调用这一份。

    核对：请求编号属于这个运行并能拆出分身、年份与第几份候选，每条记录的运行、分身、年份、候选与之相同；服务商、模型角色、
    模型快照与设定指纹与当前设定相同；报价版本相同；发送意图与结果按顺序配对（第 k 次意图之后才有第 k 次结果、上一次有结果才有
    下一次意图、同一次不重复、结果必须有意图、次数不超过 1 + 服务重试）；成功结果之后不再有意图；每次意图的请求正文都等于
    ``expected_body``（没有给时，等于从正文里取回的载荷按当前设定重新渲染的请求体，并且载荷的身份就是这份候选）；输入估计与正文
    相符；结果不带请求正文，成功的结果有回复原文与返回模型，失败的结果有错误种类；``usable_for_planning`` 若写了，必须与"返回模型
    等于设定"一致。这是工程一致性检查，不是防伪造。"""
    problems: list[str] = []
    fp = settings_fingerprint(settings)
    m = _REQUEST_ID.match(request_id)
    if m is None or m["run"] != run_id:
        problems.append(f"{request_id}：请求编号不属于运行 {run_id}，或拆不出分身、年份与候选")
        owner = None
    else:
        owner = (m["clone"], int(m["year"]), int(m["cand"]))
    mine = [r for r in records if r.request_id == request_id]
    attempts: dict[int, list[ProviderCallRecord | None]] = {}
    order: list[int] = []
    terminal = False
    first_body: dict[str, Any] | None = None
    for r in mine:
        where = f"{request_id} 第 {r.attempt} 次服务尝试的 {r.event}"
        if r.run_id != run_id:
            problems.append(f"{where}：运行编号是 {r.run_id}")
        if owner is not None and (r.clone_id, r.year_index, r.candidate) != owner:
            problems.append(
                f"{where}：记下的分身、年份或候选 {(r.clone_id, r.year_index, r.candidate)} 与请求编号不符"
            )
        if (r.provider, r.role, r.model) != (
            settings.provider,
            settings.model_role,
            settings.model,
        ):
            problems.append(f"{where}：服务商、模型角色或模型快照与当前设定不同")
        if r.settings_fingerprint != fp:
            problems.append(f"{where}：设定指纹与当前设定不同")
        if price_version is not None and r.price_version != price_version:
            problems.append(
                f"{where}：报价版本 {r.price_version} 与当前预算的 {price_version} 不同"
            )
        if r.event == "intent":
            if terminal:
                problems.append(f"{where}：这份候选已经有成功的回复，之后不该再发")
            if r.attempt != len(order) + 1:
                problems.append(f"{where}：序号不连续（前面已有 {len(order)} 次）")
            elif order and attempts[order[-1]][1] is None:
                problems.append(f"{where}：上一次尝试还没有结果就又发了一次")
            if r.attempt in attempts:
                problems.append(f"{where}：同一次尝试有两条发送意图")
                continue
            if r.status != "sent" or not isinstance(r.request, dict) or not r.request:
                problems.append(f"{where}：发送意图没有完整的请求正文")
            body = r.request
            if expected_body is not None:
                if body != expected_body:
                    problems.append(
                        f"{where}：请求正文不是这份候选按当前设定渲染出的请求体（正文、模型或解码设置被改过）"
                    )
            else:
                payload = _payload_from_body(body)
                if payload is None:
                    problems.append(f"{where}：请求正文里取不回规划请求")
                else:
                    ident = (
                        payload.get("request_id"),
                        payload.get("clone_id"),
                        payload.get("year_index"),
                        payload.get("attempt"),
                    )
                    if owner is None or ident != (request_id, *owner):
                        problems.append(
                            f"{where}：请求正文里的规划请求属于 {ident[:1]}，不是这份候选"
                        )
                    elif body != expected_provider_body(settings, payload):
                        problems.append(
                            f"{where}：请求正文与按当前设定重新渲染的请求体不同（模型或解码设置被改过）"
                        )
            if first_body is None:
                first_body = body
            elif body != first_body:
                problems.append(f"{where}：服务重试发出的请求正文与第一次不同")
            if isinstance(body, dict) and isinstance(body.get("messages"), list):
                try:
                    if r.input_tokens_estimate != estimate_input_tokens(body["messages"]):
                        problems.append(f"{where}：记下的输入估计与请求正文不符")
                except (KeyError, TypeError):
                    problems.append(f"{where}：请求正文的消息格式不对")
            attempts[r.attempt] = [r, None]
            order.append(r.attempt)
        else:
            slot = attempts.get(r.attempt)
            if slot is None:
                problems.append(f"{where}：有结果却没有对应的发送意图")
                continue
            if slot[1] is not None:
                problems.append(f"{where}：同一次尝试有两条结果")
                continue
            if order[-1] != r.attempt:
                problems.append(f"{where}：结果出现在下一次发送之后")
            slot[1] = r
            if r.request:
                problems.append(f"{where}：结果记录不该带请求正文")
            if r.status == "ok":
                if not isinstance((r.response or {}).get("text"), str) or not r.returned_model:
                    problems.append(f"{where}：成功的结果缺回复原文或返回模型")
                usable = returned_model_accepted(settings, r.returned_model)
                if r.usable_for_planning is not None and r.usable_for_planning != usable:
                    problems.append(f"{where}：记下的“能否用于规划”与返回模型不一致")
                terminal = True
            elif r.status == "error":
                if not r.error_kind or r.response:
                    problems.append(f"{where}：失败的结果缺错误种类或带了回复")
            else:
                problems.append(f"{where}：结果状态 {r.status} 不对")
    if len(order) > 1 + settings.service_retries:
        problems.append(
            f"{request_id}：服务尝试 {len(order)} 次，超过上限 {1 + settings.service_retries}"
        )
    return ServiceHistory(
        request_id=request_id,
        attempts=tuple(ServiceAttempt(n, attempts[n][0], attempts[n][1]) for n in order),  # type: ignore[arg-type]
        problems=tuple(problems),
        settings=settings,
    )


# ---------------------------------------------------------------------- 最终请求的检查


_KEY_LIKE = re.compile(r"\bsk-[A-Za-z0-9]{16,}")
_CREDENTIAL_MARKERS = ("Authorization", "Bearer ", "api_key", "API_KEY")


def final_request_problems(
    body: dict[str, Any], payload: dict[str, Any], *, forbidden_texts: Sequence[str] = ()
) -> list[str]:
    """真正发给供应商的请求体的检查（E1-7B）。请求体只能由这份已扫描的安全投影渲染而来；不能带研究真值、隐藏偏好、评分参数、
    分数或期末评价、患病概率、未来随机事件、凭证或内部运行对象。``forbidden_texts`` 由运行器给出（例如指定疾病事件的说明、内部
    规格编号），这里只检查它们不在请求里。"""
    problems: list[str] = []
    system, user = render_prompt(payload)
    messages = body.get("messages")
    if messages != [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]:
        problems.append("请求体的消息不是由这份安全请求渲染出来的那两条（系统提示与用户提示）")
    try:
        assert_payload_clean(payload)
    except LifeContractError as exc:
        problems.append(f"请求里有内部对象或保留键：{exc}"[:300])
    problems += developer_note_problems(payload)
    text = json.dumps(body, ensure_ascii=False)
    keys = sorted(k for k in FORBIDDEN_PAYLOAD_KEYS if f'"{k}":' in text)
    if keys:
        problems.append(f"请求体里出现了保留键：{keys[:8]}")
    for s in forbidden_texts:
        if s and s in text:
            problems.append(f"请求体里出现了不该给分身看的内容：{s[:60]}")
    if _KEY_LIKE.search(text) or any(m in text for m in _CREDENTIAL_MARKERS):
        problems.append("请求体里出现了像凭证的内容")
    return problems


# ---------------------------------------------------------------------- 规划器


class LlmPlanner:
    """真实模型规划器（``Planner`` 接口），与服务商无关。设定由运行配置给出，``effective_spec()`` 报出同一份；传输层与预算从外面
    注入（离线测试用替身；真实运行用 ``make_transport`` 按服务商选的传输层与 ``orchestration.life_budget.RoundBudget``）。

    计费可选（2026-10-18，DECISIONS/0035）：``budget`` 为 None 时不预留、不核算，请求明细的费用状态记为 ``unmetered``，其余检查照旧。
    运行器只在运行配置写了 ``unmetered_reason`` 时才接受不带账本的真实模型规划器。"""

    def __init__(
        self,
        settings: LlmSettings,
        *,
        transport: Transport,
        budget: BudgetGate | None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(settings) is not LlmSettings:
            raise LifeContractError(f"settings 必须是 LlmSettings，得到 {type(settings).__name__}")
        self.settings = settings
        self.identity = llm_identity(settings)
        self.transport, self.budget = transport, budget
        self._sleep, self._clock = sleep, clock
        self.log: ProviderCallLog | None = None
        self.run_id: str | None = None
        self.requests_sent = 0

    def effective_spec(self) -> PlannerSpec:
        return PlannerSpec(identity=self.identity, llm=self.settings)

    @property
    def settings_fingerprint(self) -> str:
        return settings_fingerprint(self.settings)

    def attach(self, *, archive_path: Path, run_id: str) -> None:
        """绑定到一次运行的归档：服务请求明细写在归档库的 ``provider_calls`` 表。"""
        self.log = ProviderCallLog(Path(archive_path))
        self.run_id = run_id

    def provider_body(self, call: PlannerCall) -> dict[str, Any]:
        """真正发给供应商的请求体（纯函数：同一份请求、同一份设定，请求体逐字节相同；与 ``expected_provider_body`` 同一份写法）。"""
        return _provider_body(self.settings, call.system_prompt, call.user_prompt)

    def _history(self, call: PlannerCall, body: dict[str, Any]) -> ServiceHistory:
        assert self.log is not None and self.run_id is not None
        hist = service_history(
            self.log.records(),
            request_id=call.request_id,
            run_id=self.run_id,
            settings=self.settings,
            price_version=self._price_version,
            expected_body=body,
        )
        if hist.problems:
            raise ServiceRecordMismatchError(
                f"{call.request_id} 的服务请求明细与这次请求对不上：" + "；".join(hist.problems[:4])
            )
        return hist

    def _verdict(self, call: PlannerCall, hist: ServiceHistory) -> PlannerReply | None:
        """按一致性检查的结论处理一份候选的服务历史：可用的回复 → 交回；被拒的回复 → 停下；发送不确定 → 停下；否则 None（可以再发）。"""
        if hist.usable is not None:
            o = hist.usable.outcome
            assert o is not None
            return PlannerReply(
                text=(o.response or {}).get("text", ""),
                transport_attempts=hist.usable.number,
                finish_reason=o.finish_reason,
                usage={"prompt_tokens": o.prompt_tokens, "completion_tokens": o.completion_tokens},
            )
        if hist.rejected is not None:
            o = hist.rejected.outcome
            assert o is not None
            raise PlannerHalt(
                "model_mismatch",
                f"{call.request_id} 的回复来自模型 {o.returned_model}，设定是 {self.settings.model}"
                f"（{self.settings.effective_version_policy}）：返回名不符合核对规则，不使用这份回复"
                "（原文、用量与费用照样保留在请求明细与预算账本里）；不自动重试、不换模型，等所有者决定",
            )
        if hist.pending is not None:
            raise PlannerHalt(
                "uncertain_send",
                f"{call.request_id} 有一次请求可能已经发出、但结果没有可靠落盘：不自动重发，保留预留，等所有者决定",
            )
        return None

    def _record(self, call: PlannerCall, attempt: int, **kw: Any) -> ProviderCallRecord:
        assert self.run_id is not None
        p = call.payload
        return ProviderCallRecord(
            run_id=self.run_id,
            role=self.settings.model_role,
            model=self.settings.model,
            attempt=attempt,
            request_id=call.request_id,
            clone_id=str(p.get("clone_id")),
            year_index=int(p.get("year_index", 0)),
            candidate=int(p.get("attempt", 0)),
            provider=self.settings.provider,
            settings_fingerprint=self.settings_fingerprint,
            price_version=self._price_version,
            **kw,
        )

    @property
    def _price_version(self) -> str | None:
        return None if self.budget is None else self.budget.price_version

    def _money_field(self, stem: str) -> str:
        """请求明细里金额栏的名字：人民币账本 ``reserved_cny`` / ``cost_cny``，美元账本 ``reserved_usd`` / ``cost_usd``。"""
        currency = str(getattr(self.budget, "currency", "CNY")).lower()
        return f"{stem}_{currency}"

    def _reserve(self, call: PlannerCall, key: str, attempt: int, estimate: int) -> str:
        """预留这一次服务尝试。账本里已经有这一次的预留、而且还没有核算、请求明细里也没有它的发送意图（中断在"已预留、发送意图
        还没落盘"——确定没有发出）：沿用那一份预留，不重复预留、不重复计数。已经核算过的同一个键 → ``BudgetDuplicate``。"""
        assert self.run_id is not None
        look = getattr(self.budget, "reservation", None)
        state = look(key) if callable(look) else None
        if state is not None and state.get("close") is None:
            res = state["reserve"]
            same = (
                res.get("run_id"),
                res.get("request_id"),
                res.get("service_attempt"),
                res.get("input_tokens_estimate"),
                res.get("max_output_tokens"),
            ) == (self.run_id, call.request_id, attempt, estimate, self.settings.max_output_tokens)
            if not same:
                raise ServiceRecordMismatchError(f"{key}：账本里的预留与这次请求对不上")
            return str(res[self._money_field("reserved")])
        return self.budget.reserve(
            key=key,
            run_id=self.run_id,
            request_id=call.request_id,
            service_attempt=attempt,
            input_tokens_estimate=estimate,
            max_output_tokens=self.settings.max_output_tokens,
        )

    def plan(self, call: PlannerCall) -> PlannerReply:
        if self.log is None or self.run_id is None:
            raise PlannerHalt("request_check", "规划器还没有绑定到运行归档：没有发送")
        s = self.settings
        body = self.provider_body(call)
        estimate = estimate_input_tokens(body["messages"])
        if estimate > s.max_input_tokens:
            raise PlannerHalt(
                "input_limit",
                f"{call.request_id} 的输入估计 {estimate} token（规则 {s.token_estimate}）超过上限 {s.max_input_tokens}："
                "在本地拒绝，没有发送，也没有删减请求里的任何事实",
            )
        hist = self._history(call, body)
        cached = self._verdict(call, hist)
        if cached is not None:
            # 恢复：这份候选的可用回复已经可靠落盘，直接用，不重新请求
            return cached
        allowed = 1 + s.service_retries
        attempt = len(hist.attempts)
        metered = self.budget is not None
        while attempt < allowed:
            attempt += 1
            key = f"{self.run_id}/{call.request_id}#s{attempt}"
            money: dict[str, Any] = {}
            if metered:
                try:
                    reserved = self._reserve(call, key, attempt, estimate)
                except BudgetDuplicate as exc:
                    raise PlannerHalt(
                        "uncertain_send", f"{call.request_id}：{exc}；不自动重发，等所有者核对"
                    ) from None
                except BudgetRefused as exc:
                    raise PlannerHalt("budget", f"{call.request_id}：{exc}；没有发送") from None
                money = {self._money_field("reserved"): reserved}
            self.log.append(
                self._record(
                    call,
                    attempt,
                    event="intent",
                    status="sent",
                    request=body,
                    input_tokens_estimate=estimate,
                    cost_status="reserved" if metered else "unmetered",
                    **money,
                )
            )
            self.requests_sent += 1
            started = self._clock()
            try:
                resp = self.transport.send(body, timeout=s.timeout_seconds)
            except TransportError as exc:
                elapsed = (self._clock() - started) * 1000
                cost: dict[str, Any] = {"cost_status": "unmetered"}
                if self.budget is not None:
                    kept = self.budget.settle_uncertain(key=key, reason=exc.kind)
                    cost = {self._money_field("cost"): kept, "cost_status": "uncertain_no_usage"}
                self.log.append(
                    self._record(
                        call,
                        attempt,
                        event="outcome",
                        status="error",
                        request={},
                        latency_ms=round(elapsed, 1),
                        error=exc.kind,
                        error_kind=exc.kind,
                        http_status=exc.http_status,
                        provider_code=exc.provider_code,
                        retryable=exc.retryable,
                        **cost,
                    )
                )
                if exc.retryable and attempt < allowed:
                    self._sleep(s.retry_wait_seconds)
                    continue
                kind = {
                    "credentials": "credentials",
                    "account": "account",
                    "bad_request": "bad_request",
                    "request_check": "request_check",
                }.get(exc.kind, "service_unavailable")
                raise PlannerHalt(
                    kind,
                    f"{call.request_id} 第 {attempt} 次服务请求失败（{exc.kind}"
                    + (f"，HTTP {exc.http_status}" if exc.http_status else "")
                    + (f"，{exc.provider_code}" if exc.provider_code else "")
                    + "）"
                    + ("；可重试的次数已经用完" if exc.retryable else "；这种错误不重试")
                    + "。没有换模型，也没有替分身生成计划",
                ) from None
            elapsed = (self._clock() - started) * 1000
            settled: dict[str, Any] = {"cost_status": "unmetered"}
            if self.budget is None:
                pass
            elif resp.prompt_tokens is None or resp.completion_tokens is None:
                settled = {
                    self._money_field("cost"): self.budget.settle_uncertain(
                        key=key, reason="usage_missing"
                    ),
                    "cost_status": "uncertain_no_usage",
                }
            else:
                settled = {
                    self._money_field("cost"): self.budget.settle(
                        key=key,
                        prompt_tokens=resp.prompt_tokens,
                        completion_tokens=resp.completion_tokens,
                    ),
                    "cost_status": "estimated_from_usage",
                }
            self.log.append(
                self._record(
                    call,
                    attempt,
                    event="outcome",
                    status="ok",
                    request={},
                    response={"text": resp.text},
                    latency_ms=round(elapsed, 1),
                    prompt_tokens=resp.prompt_tokens,
                    completion_tokens=resp.completion_tokens,
                    returned_model=resp.model,
                    finish_reason=resp.finish_reason,
                    reasoning_chars=resp.reasoning_chars,
                    usable_for_planning=returned_model_accepted(s, resp.model),
                    provider_usage=resp.provider_usage,
                    **settled,
                )
            )
            tier = getattr(self.budget, "above_price_tier", None)
            if callable(tier) and resp.prompt_tokens is not None and tier(resp.prompt_tokens):
                raise PlannerHalt(
                    "budget",
                    f"{call.request_id} 的实际输入 {resp.prompt_tokens} token 超过报价这一档的适用上限："
                    "费用按已记录的低档报价估算会偏低，回复与用量已经落盘；不再发送，等所有者核对报价",
                )
            # 新回复与缓存走同一份检查：返回模型不符合规则 → 停下（结果与费用已经记下）；可用 → 交回
            reply = self._verdict(call, self._history(call, body))
            assert reply is not None
            return reply
        raise PlannerHalt(
            "service_unavailable",
            f"{call.request_id} 的 {allowed} 次服务请求在之前都已经失败：不再发送（计数不因恢复而清零）",
        )


#: 旧名字（E1-7B、产品默认模型那两轮的代码与测试都这样叫）：同一个通用规划器
QwenPlanner = LlmPlanner


# ---------------------------------------------------------------------- Claude Agent SDK 传输层（DECISIONS/0035）

#: 命令行进程只继承这些系统变量。其余——尤其是桌面 App 注入的 ANTHROPIC_BASE_URL、CLAUDE_CODE_* 与宿主登录变量——全部去掉，
#: 免得命令行改用所有者的 Claude 账号登录态或别的端点。
AGENT_SDK_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "LANG",
    "LC_ALL",
    # 代理只是网络设置（千问路径的 OpenAI SDK 同样会读它们）；不涉及身份或登录态
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
)
_ENV_LOCK = threading.Lock()

#: Anthropic 的结束原因 → 统一的（OpenAI 兼容的）写法：运行器只把 ``stop`` 当作正常结束（``length`` 是被输出上限截断）；原始值另记在
#: 用量明细里。没有列出的（refusal、pause_turn、tool_use 等）原样交回，运行器按"没有正常结束"进入修订。
_ANTHROPIC_STOP = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length"}

#: AssistantMessage.error / HTTP 状态码 → 统一的错误种类与能不能重试
_AGENT_SDK_ERRORS = {
    "authentication_failed": ("credentials", False),
    "billing_error": ("account", False),
    "rate_limit": ("rate_limited", True),
    "server_error": ("server_error", True),
    "invalid_request": ("bad_request", False),
}


def agent_sdk_error(status: int | None, error: str | None) -> TransportError:
    """命令行报告的失败 → 安全的 ``TransportError``（只带种类、状态码与 SDK 的错误名）。"""
    code = error if isinstance(error, str) and _SAFE_CODE.match(error) else None
    if status in (401, 403) or error == "authentication_failed":
        return TransportError(
            "credentials", retryable=False, http_status=status, provider_code=code
        )
    if status == 402 or error == "billing_error":
        return TransportError("account", retryable=False, http_status=status, provider_code=code)
    if status == 429 or error == "rate_limit":
        return TransportError(
            "rate_limited", retryable=True, http_status=status, provider_code=code
        )
    if (status is not None and status >= 500) or error == "server_error":
        return TransportError(
            "server_error", retryable=True, http_status=status, provider_code=code
        )
    if (status is not None and 400 <= status < 500) or error == "invalid_request":
        return TransportError(
            "bad_request", retryable=False, http_status=status, provider_code=code
        )
    return TransportError("service_error", retryable=False, http_status=status, provider_code=code)


def agent_sdk_env(
    *,
    api_key: str | None,
    base_url: str | None,
    config_dir: str | None,
    max_output_tokens: int,
    timeout_seconds: float,
) -> dict[str, str]:
    """交给命令行的显式环境变量：项目自己的密钥与官方端点、隔离的配置目录、关闭命令行内部重试、提示缓存、遥测、错误上报、自动更新
    与非必要流量，输出上限。"""
    # 订阅模式（api_key / config_dir 为 None）：不写密钥，也不隔离配置目录——命令行用本机已登录的订阅凭证；其余设置不变
    own = {}
    if api_key is not None:
        own["ANTHROPIC_API_KEY"] = api_key
    if base_url is not None:
        own["ANTHROPIC_BASE_URL"] = base_url
    if config_dir is not None:
        own |= {"CLAUDE_CONFIG_DIR": config_dir, "HOME": config_dir, "USERPROFILE": config_dir}
    return {
        **own,
        "CLAUDE_CODE_MAX_RETRIES": "0",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_output_tokens),
        "API_TIMEOUT_MS": str(int(timeout_seconds * 1000)),
        "DISABLE_PROMPT_CACHING": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }


#: 订阅模式另外放行：命令行要靠它们找到本机的登录凭证（只是路径，不是凭证本身）
SUBSCRIPTION_ENV_EXTRA = ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH")


@contextmanager
def _isolated_environ(extra: Sequence[str] = ()) -> Iterator[None]:
    """启动命令行期间只保留白名单里的系统变量（SDK 会把本进程的环境整份传给命令行，只能这样去掉不该继承的变量）。串行使用；结束后
    原样恢复。"""
    with _ENV_LOCK:
        saved = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(
                {
                    k: v
                    for k, v in saved.items()
                    if k.upper() in AGENT_SDK_ENV_ALLOWLIST or k.upper() in extra
                }
            )
            yield
        finally:
            os.environ.clear()
            os.environ.update(saved)


class ClaudeAgentSdkTransport:
    """真实传输：Claude Agent SDK 当作"一个输入、跑一轮、一个输出"的 API 密钥调用（DECISIONS/0035）。

    每次 ``send`` 只调用一次 ``query()``：系统提示与用户提示原样交给 SDK，``max_turns=1``、``tools=[]``（没有任何内置工具）、
    ``setting_sources=[]``（不读 CLAUDE.md、用户设置或项目设置）、``strict_mcp_config``（不读 MCP 配置），工作目录与配置目录每次新建、
    用完删除，并且都在仓库之外（不在 git 仓库里，命令行不会附带 git 状态）。命令行内部重试关闭（服务重试由规划器统一控制），提示缓存
    关闭（只按普通输入计价）。密钥只在这里持有，不进任何记录；发送前确认请求体里没有密钥。

    本机抓包（runs/2026-10-18-haiku-test-planner/probe/）核对过：成功与 401 / 429 / 5xx 时一次调用只发 1 个 HTTP 请求；没有 CLAUDE.md
    或项目文件；工具列表为空；输出上限生效。命令行另加一句 Agent SDK 自我介绍与一段运行环境说明（临时目录、系统版本、当天日期），
    思考固定为自适应；400 时会去掉两项可选功能再重发（最多共 3 次，Anthropic 不对 400 计费）。"""

    #: 命令行进程启动与收尾的余量（秒）：整次调用的上限是请求超时加上它
    startup_margin_seconds: float = 60.0

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
        timeout_seconds: float,
        cli_path: str | None = None,
        query_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._key = api_key
        self._base_url = base_url
        self._timeout = timeout_seconds
        self._cli_path = cli_path
        self._query_fn = query_fn

    def send(self, body: dict[str, Any], *, timeout: float) -> TransportResponse:
        if self._key and self._key in json.dumps(body, ensure_ascii=False):
            raise TransportError("request_check", retryable=False)
        sdk = body.get("agent_sdk")
        messages = body.get("messages")
        if not isinstance(sdk, dict) or not isinstance(messages, list) or len(messages) != 2:
            raise TransportError("request_check", retryable=False)
        subscription = self._key is None
        with (
            tempfile.TemporaryDirectory(prefix="life-planner-cwd-") as workdir,
            tempfile.TemporaryDirectory(prefix="life-planner-cfg-") as config_dir,
        ):
            env = agent_sdk_env(
                api_key=self._key,
                base_url=self._base_url,
                config_dir=None if subscription else config_dir,
                max_output_tokens=int(body["max_tokens"]),
                timeout_seconds=timeout,
            )
            with _isolated_environ(SUBSCRIPTION_ENV_EXTRA if subscription else ()):
                try:
                    events = asyncio.run(
                        asyncio.wait_for(
                            self._run(body, messages, sdk, workdir, env),
                            timeout=timeout + self.startup_margin_seconds,
                        )
                    )
                except TimeoutError:
                    raise TransportError("timeout", retryable=True) from None
        return self._response(events)

    async def _run(
        self,
        body: dict[str, Any],
        messages: list[dict[str, str]],
        sdk: dict[str, Any],
        workdir: str,
        env: dict[str, str],
    ) -> list[Any]:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKError, ResultError, query

        options = ClaudeAgentOptions(
            model=body["model"],
            system_prompt=messages[0]["content"],
            tools=list(sdk.get("tools", [])),
            allowed_tools=[],
            setting_sources=list(sdk.get("setting_sources", [])),
            strict_mcp_config=bool(sdk.get("strict_mcp_config", True)),
            max_turns=int(sdk.get("max_turns", 1)),
            effort=sdk.get("effort"),
            cwd=workdir,
            env=env,
            cli_path=self._cli_path,
        )
        run = self._query_fn or query
        events: list[Any] = []
        try:
            async for message in run(prompt=messages[1]["content"], options=options):
                events.append(message)
        except ResultError:
            # 命令行报告了失败的结果之后以非零状态退出：结果消息已经收到，按它判断失败种类
            if not events:
                raise TransportError("process_error", retryable=False) from None
        except ClaudeSDKError:
            raise TransportError("process_error", retryable=False) from None
        return events

    def _response(self, events: list[Any]) -> TransportResponse:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ThinkingBlock

        assistant = [m for m in events if isinstance(m, AssistantMessage)]
        results = [m for m in events if isinstance(m, ResultMessage)]
        result = results[-1] if results else None
        error = next((m.error for m in assistant if m.error), None)
        if result is None or result.is_error or error:
            raise agent_sdk_error(None if result is None else result.api_error_status, error)
        text = "".join(b.text for m in assistant for b in m.content if isinstance(b, TextBlock))
        reasoning = sum(
            len(b.thinking or "")
            for m in assistant
            for b in m.content
            if isinstance(b, ThinkingBlock)
        )
        usage = result.usage or {}
        n_in = [
            usage.get(k)
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        ]
        prompt_tokens = sum(int(v or 0) for v in n_in) if n_in[0] is not None else None
        output = usage.get("output_tokens")
        details = usage.get("output_tokens_details") or {}
        raw_stop = result.stop_reason or (assistant[-1].stop_reason if assistant else None)
        return TransportResponse(
            text=text,
            finish_reason=_ANTHROPIC_STOP.get(raw_stop, raw_stop) if raw_stop else None,
            model=assistant[-1].model if assistant else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=None if output is None else int(output),
            reasoning_chars=reasoning,
            provider_usage={
                "input_tokens": usage.get("input_tokens"),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                "output_tokens": output,
                "thinking_tokens": details.get("thinking_tokens"),
                "stop_reason": raw_stop,
                "num_turns": result.num_turns,
                "assistant_messages": len(assistant),
                "terminal_reason": result.terminal_reason,
                "sdk_total_cost_usd": result.total_cost_usd,
            },
        )


def make_transport(settings: LlmSettings, *, api_key: str | None) -> Transport:
    """按服务商选传输层（真实运行用；离线测试直接注入替身）。订阅认证（auth: claude_subscription）不要密钥。"""
    if settings.provider == "claude_agent_sdk":
        sub = settings.auth == "claude_subscription"
        return ClaudeAgentSdkTransport(
            api_key=None if sub else api_key,
            base_url=None if sub else settings.base_url,
            timeout_seconds=settings.timeout_seconds,
        )
    assert api_key is not None
    return OpenAICompatibleTransport(
        api_key=api_key, base_url=settings.base_url, timeout_seconds=settings.timeout_seconds
    )
