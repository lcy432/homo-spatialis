"""学习与岗位（`DECISIONS/0022`，职业规则 e1-5a-career-v1）：课程按实际参加的月份累计，学满授予能力，能力下一年起才能用；
岗位资格按年初状态判断。

K51 的数字：会计课学费 1,200（一次付清），每月 40 小时、每小时负担 1.2，学满 12 个月授予会计资格；会计岗位年薪 36,000
（每月 3,000），要求会计资格。上课的年份住近处一居室（往返 30 分钟），每月负担 160 + 7.5 + 15 + 12 + 48 = 242.5，不超过额度
250（住远处是 252.5，超额）。
"""

from __future__ import annotations

from econ.life_settlement import settle_year

from .conftest import CLONE_K, K51, live_year, tax_for, whole, year_action
from .settle_support import cents, item

COURSE = "k51.training.accounting_course"


def _near(job="clerk", start=1, end=12):
    return [
        whole("k51.housing.near"),
        whole("k51.food.basic"),
        whole(f"k51.job.{job}", start, end),
    ]


def _settle(bundle, items, state=None):
    state = state or bundle.initial_state(CLONE_K)
    return settle_year(
        protocol=bundle.protocol,
        state=state,
        action=year_action(state, items),
        catalog=bundle.catalog_for(K51),
        tax=tax_for(bundle, K51),
    )


def test_twelve_months_of_the_course_grant_the_ability_from_next_year(bundle):
    r = _settle(bundle, [*_near(), whole(COURSE)])
    assert r.status == "completed"
    (learn,) = r.outcome.career.learning
    assert (learn.progress_before, learn.progress_after, learn.months_required) == (0, 12, 12)
    assert (learn.completed_month, learn.granted, learn.usable_from_year) == (
        12,
        ("accounting_cert",),
        2,
    )
    assert cents(learn.fee_charged) == 120000
    held = {a.ability_id: a for a in r.next_state.abilities}
    assert held["accounting_cert"].usable_from_year == 2
    assert held["accounting_cert"].source_opportunity_id == COURSE


def test_the_fee_is_paid_once_in_the_first_month(bundle):
    r = _settle(bundle, [*_near(), whole(COURSE)])
    paid = [m for m in r.outcome.months if cents(m.spent) > 140000 + 38000]
    assert [m.month for m in paid] == [1]  # 1 月：房租 1,400 + 食品 380 + 学费 1,200
    assert cents(item(r, COURSE).amount_paid) == 120000


def test_half_a_course_is_progress_not_a_credential_and_is_not_charged_again(bundle, scoring):
    """1–6 月上课：进度 6/12，不授予；第 2 年再上 6 个月不再收学费，学满时授予，第 3 年起可用。
    （跨年要经过年度交接：下一年的精力额度由上一年的完整评分确定，所以这里走完整的一年。）"""
    y1 = live_year(bundle, scoring, bundle.initial_state(CLONE_K), [*_near(), whole(COURSE, 1, 6)])
    assert y1.settlement.outcome.career.learning[0].progress_after == 6
    assert y1.next_state.abilities == ()
    (progress,) = y1.next_state.training
    assert (progress.months_participated, progress.fee_paid) == (6, True)
    y2 = live_year(bundle, scoring, y1.next_state, [*_near(), whole(COURSE, 1, 6)])
    (learn,) = y2.settlement.outcome.career.learning
    assert (learn.progress_before, learn.progress_after, cents(learn.fee_charged)) == (6, 12, 0)
    assert learn.usable_from_year == 3


def test_a_job_needing_the_credential_is_refused_before_it_is_usable(bundle):
    """没有会计资格选会计岗位：年初就能看出的计划问题（可以修订），不付钱、不执行。"""
    r = _settle(bundle, [*_near(job="accountant")])
    assert r.status == "input_error" and r.outcome is None
    assert [p.code for p in r.problems] == ["qualification_missing"]


def test_the_credential_opens_the_better_job_the_next_year(bundle, scoring):
    y1 = live_year(bundle, scoring, bundle.initial_state(CLONE_K), [*_near(), whole(COURSE)])
    y2 = live_year(bundle, scoring, y1.next_state, _near(job="accountant"))
    out = y2.settlement.outcome
    assert y2.settlement.status == "completed"
    assert [cents(m.income_gross) for m in out.months] == [300000] * 12  # 年薪 36,000
    assert out.career.current_position_ids == ("k51.job.accountant",)
    assert y2.next_state.employment.current_position_ids == ("k51.job.accountant",)


def test_changing_jobs_mid_year_records_each_position(bundle):
    """1–6 月文员、7–12 月兼职：两段各记各的月份，年末的当前岗位是兼职。"""
    items = [
        whole("k51.housing.far"),
        whole("k51.food.basic"),
        whole("k51.job.clerk", 1, 6),
        whole("k51.job.shop_parttime", 7, 12),
    ]
    r = _settle(bundle, items)
    months = {p.opportunity_id: p.months for p in r.outcome.career.positions}
    assert months == {
        "k51.job.clerk": (1, 2, 3, 4, 5, 6),
        "k51.job.shop_parttime": (7, 8, 9, 10, 11, 12),
    }
    assert r.outcome.career.current_position_ids == ("k51.job.shop_parttime",)
