"""泄漏扫描：从 prompt 里抽数字，与真值做**数值**比对（不是字符串匹配）。

为什么不做字符串匹配。0.2 在 IEEE 754 里存成 0.200000000000000011，而一个
由计算得出的同一个权重可能是 0.19999999999999998。这两个数在任何有意义的
尺度上都是同一个数，但 "0.2" 不是后者 repr 的子串，字符串搜索直接漏掉。
浮点表示误差不是边缘情况，它是权重由 §7.2 的两通道映射算出来之后的常态。

所以流程改成：正则抽出所有数字词元 → parse 成 float → 对每个真值及其
若干变换做相对容差比较。

**单位换算只能枚举，不能穷尽。** 这一点必须写在明处而不是假装解决了：
我们枚举 ×100（小数与百分数）、÷100、×12（月与年）、÷12、×1000、÷1000
（千元单位）。一个用了别的换算的泄漏——比如把权重乘以 7 再报告，或者
换算成某种指数——扫不出来。这个扫描是第二道防线，第一道是类型边界，
它才是结构性的。

误报控制随之从字符串问题变成数值问题，两条：

  白名单：该 Observable 里合法出现的数值。真值字段与可见属性撞上同一个
          数是巧合而不是泄漏，不排除的话这个检查会天天喊狼来了，然后被
          人关掉。
  黑名单：0 / 1 / 2 / 100 这类普遍值。它们在任何 prompt 里都会出现。

旧版那个"有效数字地板"（min_value_digits）已经删掉。它有一个结构性缺陷：
默认值 3 意味着任何新加的扫描点默认是瞎的，而定和量表的权重天生只有两位
有效数字（§6.3：人会给 10、20、30）。改成数值比对之后这个参数不再需要，
问题自动消失。

--------------------------------------------------------------------------
向量指纹
--------------------------------------------------------------------------

单值扫描会放过一整类泄漏：七个权重被**整体倾倒**进 prompt。逐个比对时
每个权重都可能因为白名单或黑名单被放过，但七个数一起出现、和为 1（或
100）、且作为多重集与真值权重匹配——这个模式本身就是泄漏的证据。
weight_vector_fingerprint 抓的就是它。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum

# 单值扫描的相对容差。1e-3 足以吸收浮点表示误差与"报告到三位小数"的
# 四舍五入，又不至于把两个真正不同的权重（相差 0.01 以上）判成同一个。
DEFAULT_RTOL = 1e-3

# 向量指纹的相对容差，**刻意比单值扫描松两个量级**。
#
# 理由是证据强度不同。单个数匹配上是弱证据，所以容差必须紧，否则误报
# 淹没一切。而七个数同时匹配、且构成真值权重的多重集，即使每个都只匹配
# 到百分之二，联合起来仍然是压倒性的证据——随机文本里凑出这样一组的
# 概率可以忽略。
#
# 松容差在这里不是妥协，是必需：§6.3 说人会给 10、20、30 这样的整数，
# 所以"权重以整数百分数被倾倒"是最可能的泄漏形态。整数化在量级 13 上
# 的相对误差就有 3.8e-2；用 1e-3 去扫，恰好把最常见的那种泄漏漏掉。
FINGERPRINT_RTOL = 4e-2

# 绝对容差，用于接近零的量：相对容差在 0 附近没有意义。
DEFAULT_ATOL = 1e-9

# 普遍值。出现在任何 prompt 里都不构成证据。
UNIVERSAL_VALUES = (0.0, 1.0, 2.0, 100.0)

# 单位换算的枚举。见模块文档：这是枚举，不是穷尽。
UNIT_TRANSFORMS: tuple[float, ...] = (1.0, 100.0, 0.01, 12.0, 1.0 / 12.0, 1000.0, 0.001)

# 数字词元：可带符号、可带千分位、可带小数与科学计数。
_NUMBER_RE = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?")


class LeakError(AssertionError):
    """真值出现在了序列化后的 prompt 里。"""


@dataclass(frozen=True)
class Leak:
    path: str
    value: float
    matched: float
    transform: float

    def __str__(self) -> str:
        how = "原值" if self.transform == 1.0 else f"×{self.transform:g}"
        return f"{self.path}={self.value!r} 以 {how} 的形式出现为 {self.matched!r}"


def extract_numbers(text: str) -> list[float]:
    """抽出 prompt 里所有能 parse 成 float 的数字词元。"""
    out: list[float] = []
    for token in _NUMBER_RE.findall(text):
        try:
            out.append(float(token.replace(",", "")))
        except ValueError:  # pragma: no cover - 正则已保证可解析
            continue
    return out


def numeric_fields(obj: object, path: str = "") -> dict[str, float]:
    """递归取出对象里所有 float / int，按访问路径作键。

    bool 跳过：它在 Python 里是 int 的子类，但不携带值得隐藏的数值信息。
    """
    out: dict[str, float] = {}

    def walk(value: object, p: str) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            if math.isfinite(value):
                out[p or "<root>"] = float(value)
            return
        if isinstance(value, (str, bytes, Enum)):
            return
        if is_dataclass(value) and not isinstance(value, type):
            for f in fields(value):
                walk(getattr(value, f.name), f"{p}.{f.name}" if p else f.name)
            return
        if isinstance(value, Mapping):
            for k, v in value.items():
                walk(v, f"{p}[{k!r}]" if p else f"[{k!r}]")
            return
        if isinstance(value, Sequence):
            for i, v in enumerate(value):
                walk(v, f"{p}[{i}]" if p else f"[{i}]")

    walk(obj, path)
    return out


def _close(a: float, b: float, rtol: float, atol: float) -> bool:
    return math.isclose(a, b, rel_tol=rtol, abs_tol=atol)


def _is_universal(x: float, rtol: float, atol: float) -> bool:
    return any(_close(x, u, rtol, atol) for u in UNIVERSAL_VALUES)


def find_leaks(
    prompt: str,
    secret: object,
    *,
    allowed: object | None = None,
    exempt_paths: frozenset[str] = frozenset(),
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_ATOL,
) -> list[Leak]:
    """真值里出现在 prompt 中的数（含枚举的单位换算）。

    ``allowed`` 是代理可见的对象（通常是 Observable 的集合）；它里面
    出现过的数值一律放过，因为它们出现在 prompt 里是对的，不是泄漏。
    """
    numbers = extract_numbers(prompt)
    whitelist = list(numeric_fields(allowed).values()) if allowed is not None else []

    leaks: list[Leak] = []
    for path, value in numeric_fields(secret).items():
        if path in exempt_paths:
            continue
        if _is_universal(value, rtol, atol):
            continue
        if any(_close(value, w, rtol, atol) for w in whitelist):
            continue
        hit = _match(value, numbers, whitelist, rtol, atol)
        if hit is not None:
            matched, transform = hit
            leaks.append(Leak(path, value, matched, transform))
    return leaks


def _match(
    value: float, numbers: Iterable[float], whitelist: Sequence[float], rtol: float, atol: float
) -> tuple[float, float] | None:
    for transform in UNIT_TRANSFORMS:
        target = value * transform
        if _is_universal(target, rtol, atol):
            continue
        for n in numbers:
            if not _close(n, target, rtol, atol):
                continue
            # 该数字若同时落在白名单上，它出现在 prompt 里有合法解释。
            if any(_close(n, w, rtol, atol) for w in whitelist):
                continue
            return n, transform
    return None


def assert_no_leak(
    prompt: str,
    secret: object,
    *,
    allowed: object | None = None,
    exempt_paths: frozenset[str] = frozenset(),
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_ATOL,
) -> None:
    leaks = find_leaks(
        prompt, secret, allowed=allowed, exempt_paths=exempt_paths, rtol=rtol, atol=atol
    )
    if leaks:
        raise LeakError(
            "序列化后的 prompt 里出现了真值（CLAUDE.md 硬规则 1）：\n  "
            + "\n  ".join(str(leak) for leak in leaks)
        )


@dataclass(frozen=True)
class FingerprintHit:
    """整组权重被倾倒进 prompt 的证据。"""

    scale: float  # 1.0 表示以小数出现，100.0 表示以百分数出现
    matched: tuple[float, ...]
    total: float

    def __str__(self) -> str:
        unit = "小数" if self.scale == 1.0 else f"×{self.scale:g}"
        return (
            f"{len(self.matched)} 个权重以{unit}形式整组出现，和为 {self.total:.4g}：{self.matched}"
        )


def weight_vector_fingerprint(
    prompt: str,
    weights: Sequence[float],
    *,
    rtol: float = FINGERPRINT_RTOL,
    atol: float = DEFAULT_ATOL,
) -> FingerprintHit | None:
    """检查 prompt 里是否存在与真值权重整体匹配的一组数。

    单值扫描抓不到这个：逐个比对时，每个权重都可能因为白名单或黑名单被
    放过，但七个数一起出现、且作为多重集与真值权重匹配，这个模式本身就
    是泄漏。这是"人设散文里把七个权重一次列全"的签名。

    匹配用**最优先贪心**而不是穷举子集：C(n,7) 在几百个数字上不可行，
    而按降序为每个权重取最接近的未用数字，在权重彼此可分辨（相差大于
    容差）时给出正确结果。两个真值权重接近到容差以内时贪心可能配错，
    此时它仍然会报命中——报的是"有这么一组数"，结论不变。

    和的检验作为附带信息返回而不是作为条件：真值权重按 §7.1 就是和为一
    的，所以整组匹配上，和自然接近 1（或百分数下的 100）。
    """
    numbers = extract_numbers(prompt)
    for scale in (1.0, 100.0):
        targets = sorted((w * scale for w in weights), reverse=True)
        pool = list(numbers)
        matched: list[float] = []
        for t in targets:
            best = None
            best_err = None
            for n in pool:
                err = abs(n - t)
                if _close(n, t, rtol, atol) and (best_err is None or err < best_err):
                    best, best_err = n, err
            if best is None:
                break
            pool.remove(best)
            matched.append(best)
        if len(matched) == len(targets):
            return FingerprintHit(scale=scale, matched=tuple(matched), total=sum(matched))
    return None


def assert_no_weight_vector(
    prompt: str,
    weights: Sequence[float],
    *,
    rtol: float = FINGERPRINT_RTOL,
    atol: float = DEFAULT_ATOL,
) -> None:
    hit = weight_vector_fingerprint(prompt, weights, rtol=rtol, atol=atol)
    if hit is not None:
        raise LeakError(f"权重向量被整组泄漏进 prompt（README §11.5）：{hit}")


# --------------------------------------------------------------------------
# 顺序泄漏（README §10.2 的同一手法，用在人设散文上）
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderLeakageReport:
    """叙述顺序与权重之间是否存在系统关联。"""

    n_personas: int
    mean_rho: float
    t_statistic: float
    p_value: float
    alpha: float

    @property
    def leaks(self) -> bool:
        return self.p_value < self.alpha

    def __str__(self) -> str:
        verdict = "存在系统关联（泄漏）" if self.leaks else "无显著关联"
        return (
            f"{self.n_personas} 个人设，平均 Spearman ρ = {self.mean_rho:+.4f}，"
            f"t = {self.t_statistic:+.3f}, p = {self.p_value:.4g} → {verdict}"
        )


def narration_order_leakage(
    samples: Sequence[tuple[Sequence[str], Mapping[str, float]]],
    *,
    alpha: float = 0.05,
) -> OrderLeakageReport:
    """检验人设散文的维度叙述顺序是否泄漏了权重排序。

    这是数值扫描完全抓不到的一条通道。若人设总是按权重降序铺陈七个维度，
    秩序信息已经在文本里了：模型不需要读懂任何一个数字，只要注意到"他先
    说通勤"，就能在权衡档上答对。回收出来的"偏好推断能力"于是是假的。

    ``samples`` 是 (叙述顺序, 权重字典) 的序列，每个 persona 一条。

    **必须在人群层面检验，不能逐个人设看。** 七个维度的单个 Spearman ρ
    要达到 p<0.05 需要 |ρ| > 0.75 左右，单条样本几乎没有功效；而真正的
    问题也不是某一个人设排巧了，是生成器有系统倾向。所以做法是每个人设
    算一个 ρ，再对这些 ρ 做单样本 t 检验，问它们的均值是否显著偏离零。

    ρ 的符号约定：叙述位置（0 是最先说的）对权重取秩。若重要的维度先说，
    ρ 为负（位置小、权重大）。检验是双侧的，因为倒序排列泄漏的信息量与
    正序完全相同。
    """
    from scipy import stats  # 局部导入：本模块的其余部分不需要 scipy

    if len(samples) < 3:
        raise ValueError(
            f"只有 {len(samples)} 个人设，人群层面的检验没有意义；"
            "顺序泄漏是生成器的系统倾向，不是单个人设的属性"
        )

    rhos: list[float] = []
    for order, weights in samples:
        if len(order) < 3:
            raise ValueError(f"叙述顺序至少要有三个维度才能算秩相关，得到 {list(order)}")
        missing = set(order) - set(weights)
        if missing:
            raise ValueError(f"叙述顺序里的维度 {sorted(missing)} 不在权重字典中")
        positions = list(range(len(order)))
        values = [weights[d] for d in order]
        rho = stats.spearmanr(positions, values).statistic
        # 权重全相等时 ρ 是 nan；那种人设本来也不携带顺序信息。
        if math.isfinite(rho):
            rhos.append(float(rho))

    if len(rhos) < 3:
        raise ValueError("可用的 ρ 少于三个（权重全相等的人设被排除后样本不足）")

    mean_rho = sum(rhos) / len(rhos)

    # 退化情形：所有 ρ 完全相同（例如生成器严格按权重排序，ρ 恒为 −1）。
    # 样本方差为零时 t 统计量是 ±inf，scipy 会因灾难性抵消发出精度警告。
    # 这不是数值问题而是一个确定的结论：要么完全没有关联，要么关联是
    # 完美的，两种情况都不需要检验。
    spread = max(rhos) - min(rhos)
    if spread == 0.0:
        perfect = mean_rho != 0.0
        return OrderLeakageReport(
            n_personas=len(rhos),
            mean_rho=mean_rho,
            t_statistic=math.inf if perfect else 0.0,
            p_value=0.0 if perfect else 1.0,
            alpha=alpha,
        )

    result = stats.ttest_1samp(rhos, popmean=0.0)
    return OrderLeakageReport(
        n_personas=len(rhos),
        mean_rho=mean_rho,
        t_statistic=float(result.statistic),
        p_value=float(result.pvalue),
        alpha=alpha,
    )


def assert_order_uninformative(
    samples: Sequence[tuple[Sequence[str], Mapping[str, float]]],
    *,
    alpha: float = 0.05,
) -> None:
    report = narration_order_leakage(samples, alpha=alpha)
    if report.leaks:
        raise LeakError(
            f"人设的维度叙述顺序与权重存在系统关联，秩序信息已泄漏（README §11.5）：{report}"
        )
