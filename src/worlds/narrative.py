"""人设散文：生成一次，过三道闸，冻结进仓库。

--------------------------------------------------------------------------
为什么模型看不到任何一个数字
--------------------------------------------------------------------------

最自然的做法是把七个权重递给模型说"照这个写段自我描述"。**不这么做。**

权重是答案侧的（AgentInput 的构造函数会拒收 PersonaParams）。就算这次调用
不是代理在做区位决策，把真值数字送进任何一次模型调用都会带来一个不必要
的风险：模型可能把它们原样或近似地写进输出，而输出是要进 prompt 的。

所以中间隔一层**定性化**：权重先变成粗粒度的档位词（"压倒性的顾虑"、
"几乎不在意"），模型只看到档位词。它没见过数字，就写不出数字——这比
"写完再扫一遍" 强，因为后者只能发现已经发生的泄漏。

代价是散文只承载秩序信息，不承载精确权重。这正合 §11.5 的"隐含"档：
一个人描述自己时本来也只会说个大概，不会报小数。

--------------------------------------------------------------------------
三道闸
--------------------------------------------------------------------------

即便如此，输出仍要过闸，因为定性化只是降低了泄漏概率，不是消除：

  一、数值扫描      真值的任何一个数（含枚举的单位换算）不得出现
  二、向量指纹      七个权重不得被整组倾倒
  三、顺序秩相关    人群层面检验叙述顺序与权重无系统关联

第三道必须在**人群层面**做，单个人设七维的秩相关几乎没有功效，而真正的
问题也不是某一个人设排巧了，是生成器有系统倾向。

--------------------------------------------------------------------------
生成一次，冻结
--------------------------------------------------------------------------

散文是唯一要花调用成本的部分，也是唯一必须冻结的部分。权重可以随便重抽
（同一个 seed 给同一批人），散文不行：每跑一次实验就重生成一批散文的话，
两次实验的结果不可比，而"人设"这个自变量就不再是固定的。

这与 §5.4 说数据映射"只跑一次、人工复核、冻结进仓库"是同一条纪律。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import numpy as np

from common.leakcheck import (
    assert_no_leak,
    assert_no_weight_vector,
    narration_order_leakage,
)
from econ.types import Persona
from worlds.dimensions import ALL_DIMENSIONS, MATERIAL

FROZEN_PATH = Path("data/frozen/personas_v1.json")

# 档位划分。权重是七维单纯形上的点，所以 1/7 ≈ 0.143 是"平均水平"。
BUCKETS: tuple[tuple[float, str], ...] = (
    (0.30, "the thing that dominates everything else"),
    (0.18, "a major consideration"),
    (0.10, "moderately important"),
    (0.04, "a minor factor"),
    (0.00, "something they barely think about"),
)

# 每个维度给模型的生活化说法。刻意不用项目内部的术语（"舒适度"、"标度值"），
# 因为要写出来的是一个人会说的话，不是一份问卷的回执。
DIMENSION_PROSE: dict[str, str] = {
    MATERIAL: "money: pay, rent, how much is left over at the end of the month",
    "safety": "feeling safe walking around where they live",
    "healthcare": "being able to see a doctor without a fight",
    "opportunity": "chances to learn things and get further in their field",
    "leisure": "having things to do: sport, music, going out",
    "environment": "air, noise, weather, green space",
    "commute": "how long it takes to get to work and whether they must drive",
}


class NarrativeError(RuntimeError):
    """散文没能通过闸门。"""


class NarrativeClient(Protocol):
    """写一段散文。实现由调用方注入，所以测试不需要网络。"""

    def write(self, system: str, user: str) -> str: ...


SYSTEM_PROMPT = (
    "You write short, plausible first-person self-descriptions for a research "
    "dataset about where people choose to live.\n"
    "Rules, all of them strict:\n"
    "- 2 to 4 sentences. Everyday voice, the way someone talks about themselves.\n"
    "- Never use any digit or written-out number.\n"
    "- Never rank things explicitly. No 'most important', 'second', 'above all'.\n"
    "- Never name the categories you were given. Show the priority through "
    "concrete detail: what they put up with, what they refuse, what they notice.\n"
    "- Mention the items in the order given. Do not reorder them.\n"
    "- No city names, no countries, no employers."
)


def _bucket(weight: float) -> str:
    for threshold, label in BUCKETS:
        if weight >= threshold:
            return label
    return BUCKETS[-1][1]  # pragma: no cover - 0.0 兜底已在表内


def qualitative_brief(persona: Persona, order: tuple[str, ...]) -> str:
    """把权重变成档位词，按给定顺序排列。**模型只看到这个。**

    ``order`` 由调用方随机化并记录。顺序必须随机，否则散文会按权重降序
    铺陈，秩序信息就泄漏了——模型不需要读懂任何数字，只要注意到"他先说
    通勤"就能在权衡档上答对。
    """
    weights = {MATERIAL: persona.params.w_material, **dict(persona.params.theta)}
    lines = [f"- {DIMENSION_PROSE[d]}: {_bucket(weights[d])}" for d in order]
    return "This person's priorities, in the order you should mention them:\n" + "\n".join(lines)


def _weights_of(persona: Persona) -> dict[str, float]:
    return {MATERIAL: persona.params.w_material, **dict(persona.params.theta)}


def _check_one(persona: Persona, text: str) -> None:
    """闸门一与二。闸门三是人群层面的，在 generate_narratives 末尾统一做。"""
    if not text.strip():
        raise NarrativeError(f"{persona.persona_id} 的散文是空的")
    assert_no_leak(text, persona.params)
    assert_no_weight_vector(text, list(_weights_of(persona).values()))


def generate_narratives(
    personas: list[Persona],
    client: NarrativeClient,
    *,
    seed: int = 0,
    max_retries: int = 3,
    order_alpha: float = 0.05,
) -> list[Persona]:
    """给每个人设写一段散文，逐个过闸，最后再过一次人群层面的顺序检验。

    单个失败重试 ``max_retries`` 次；仍然失败就抛，**不静默跳过**。跳过
    会让人设集合出现非随机的缺口：过不了闸的往往是权重结构特殊的那些，
    丢掉它们等于让样本带上选择性。
    """
    rng = np.random.default_rng(seed)
    out: list[Persona] = []

    for persona in personas:
        order = tuple(rng.permutation(list(ALL_DIMENSIONS)))
        brief = qualitative_brief(persona, order)

        last: Exception | None = None
        for _ in range(max_retries):
            text = client.write(SYSTEM_PROMPT, brief).strip()
            try:
                _check_one(persona, text)
            except Exception as exc:
                last = exc
                continue
            out.append(
                replace(
                    persona, brief=replace(persona.brief, narrative=text, dimension_order=order)
                )
            )
            break
        else:
            raise NarrativeError(
                f"{persona.persona_id} 连续 {max_retries} 次没通过闸门，最后一次：{last}"
            )

    report = narration_order_leakage(
        [(p.brief.dimension_order, _weights_of(p)) for p in out], alpha=order_alpha
    )
    if report.leaks:
        raise NarrativeError(
            f"叙述顺序在人群层面与权重相关，秩序信息已泄漏：{report}。检查顺序随机化是不是被绕过了"
        )
    return out


#: 冻结文件的格式版本。**旧格式（裸列表、无指纹）一律拒绝绑定**，
#: 不拿当前真值补一个指纹再宣称验证通过——那等于用被检查的东西去检查自己。
FROZEN_FORMAT_VERSION = 2


def truth_digest(persona: Persona) -> str:
    """一个人设答案侧的指纹。**只进内部清单，不进代理可见文本。**

    取七维权重加能力分位，四舍五入到 1e-9 再哈希。用摘要而不是权重本身，
    是为了让这个文件即使被误发出去也不直接泄漏真值（`freeze` 的原始理由）；
    但它足以判定"同一个 ID 背后还是不是同一个人"。
    """
    pp = persona.params
    payload = {
        "alpha": round(float(pp.alpha), 9),
        "beta": round(float(pp.beta), 9),
        "theta": {k: round(float(v), 9) for k, v in sorted(pp.theta.items())},
        "ability_quantile": getattr(persona, "ability_quantile", None),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def freeze(
    personas: list[Persona],
    path: Path = FROZEN_PATH,
    *,
    persona_seed: int | None = None,
    config_digest: str | None = None,
) -> Path:
    """把散文冻结进仓库。**只存代理能看到的东西**，外加一份内部指纹清单。

    权重由 seed 决定，随时可以重算；散文不能。分开存还有一层好处：这个
    文件即使被误发出去也不泄漏真值——存的是摘要，不是权重。

    **`persona_id` 是流水号**（`p00000`…），不同 seed/config 抽出来的人会用
    同一批 ID。只按 ID 绑定，换了先验或种子之后匹配臂会在不报错的情况下
    变成错配臂。所以这里同时记生成参数与逐人真值指纹，加载时验。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": FROZEN_FORMAT_VERSION,
        "generation": {
            "persona_seed": persona_seed,
            "config_digest": config_digest,
            "n": len(personas),
        },
        "personas": [
            {
                "persona_id": p.persona_id,
                "literalness": p.brief.literalness.value,
                "narrative": p.brief.narrative,
                "dimension_order": list(p.brief.dimension_order),
                # 内部字段，`attach_frozen` 不会把它放进 brief
                "truth_digest": truth_digest(p),
            }
            for p in personas
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def attach_frozen(
    personas: list[Persona], path: Path = FROZEN_PATH, *, require_digest: bool = True
) -> list[Persona]:
    """把冻结的散文贴回按 seed 重算出来的人设上。

    按 `persona_id` 对齐，**再按真值指纹校验**。缺一个人就抛：静默跳过会得到
    一批没有散文的人设，而它们在实验里看起来跟"无人设对照组"一模一样。
    指纹对不上也抛：那说明同一个 ID 背后换了人，而那种错配同样不报错。

    `require_digest=False` 只给"明知在读旧格式、且只想看散文"的诊断用途，
    **不得用于任何会进入实验的路径**。
    """
    raw = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(raw, list):
        raise NarrativeError(
            f"{path} 是旧格式（裸列表，无生成参数与真值指纹）。"
            f"旧格式不能绑定：persona_id 是流水号，换 seed 之后同一个 ID 背后是"
            f"另一个人，而绑定不会报错。请用当前 seed/config 重新 freeze 一次；"
            f"**不要**拿现在的真值给旧文件补指纹——那是用被检查的东西检查自己"
        )
    version = raw.get("format_version")
    if version != FROZEN_FORMAT_VERSION:
        raise NarrativeError(
            f"{path} 的 format_version={version!r}，当前要求 {FROZEN_FORMAT_VERSION}"
        )

    frozen = {row["persona_id"]: row for row in raw["personas"]}
    out: list[Persona] = []
    mismatched: list[str] = []
    for p in personas:
        row = frozen.get(p.persona_id)
        if row is None:
            raise NarrativeError(f"冻结文件里没有 {p.persona_id} 的散文")
        stored = row.get("truth_digest")
        if require_digest:
            if stored is None:
                raise NarrativeError(f"{p.persona_id} 没有 truth_digest，无法确认它还是同一个人")
            if stored != truth_digest(p):
                mismatched.append(p.persona_id)
        out.append(
            replace(
                p,
                brief=replace(
                    p.brief,
                    narrative=row["narrative"],
                    dimension_order=tuple(row["dimension_order"]),
                ),
            )
        )
    if mismatched:
        raise NarrativeError(
            f"{len(mismatched)} 个人设的真值指纹与冻结文件不符（前几个："
            f"{mismatched[:5]}）。同一个 ID 背后换了人——多半是换了 seed、先验或"
            f"生成算法。继续绑定会得到一条**错配臂**，而它不会报错"
        )
    return out


@dataclass(frozen=True)
class DashScopeNarrativeClient:
    """真实实现。厂商无关的 OpenAI 兼容客户端，只换 base_url / api_key / model。"""

    model: str
    base_url: str
    api_key: str
    temperature: float = 1.0

    def write(self, system: str, user: str) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
            max_tokens=200,
        )
        return response.choices[0].message.content or ""
