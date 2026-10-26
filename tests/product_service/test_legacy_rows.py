"""服务库里留着旧版（v3.3）写下的草稿、审阅与确认画像：新版照原样读回（DECISIONS/0046 的兼容读取；独立复核 R1）。

旧行的正文与显示绑定原样取自 ``tests/fixtures/v33_objects/``（旧版服务真实写下的，见那里的说明），只把归属换成这个测试的人：

* 读画像、读审阅、英文显示：正文与当初逐字相同，英文按原来的绑定与原来的资源版本完整给出；读取不写库；
* 旧画像仍不能准备新模拟（``preflight_stale``，新确认必须用新问卷）；旧草稿读取时给迁移说明。

已经开始的旧运行在升级之后恢复（两进程：旧版代码开始并停下、新版代码打开同一个服务库恢复）记在本机证据
``runs/e3-compat-fix-20261025/``；这里的测试不运行旧版代码。全部离线。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from product.service.clock import days_after
from tests.product_service.support import db_dump, start
from tests.product_service.test_display_service import display

OLD = Path(__file__).resolve().parents[1] / "fixtures" / "v33_objects"
OLD_RESOURCES = "e3-display-res-v1-a7813e49b0e3fbdc"


def _text(name: str) -> str:
    return (OLD / f"{name}.json").read_text(encoding="utf-8")


def _insert_old_rows(svc, principal: str) -> dict[str, str]:
    """把旧版写下的三行放进服务库（测试准备：模拟升级之前就在库里的数据）。"""
    draft, review, profile = (json.loads(_text(n)) for n in ("draft", "review", "profile"))
    now = svc.store.now()
    later = days_after(now, 30)
    con = sqlite3.connect(svc.store.path)
    try:
        con.execute(
            "INSERT INTO drafts (draft_id, principal_id, revision, body, created_at, updated_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (draft["draft_id"], principal, draft["revision"], _text("draft"), now, now, later),
        )
        con.execute(
            "INSERT INTO reviews (review_id, principal_id, draft_id, draft_revision, draft_fingerprint,"
            " pack_fingerprint, body, created_at, display_body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                review["review_id"],
                principal,
                draft["draft_id"],
                review["draft"]["revision"],
                review["draft"]["draft_fingerprint"],
                review["meta"]["data_pack"]["content_fingerprint"],
                _text("review"),
                now,
                _text("review_display"),
            ),
        )
        con.execute(
            "INSERT INTO profiles (confirmed_id, principal_id, draft_id, draft_revision, review_id,"
            " content_fingerprint, body, created_at, expires_at, display_body)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                profile["confirmed_id"],
                principal,
                draft["draft_id"],
                profile["draft"]["revision"],
                profile["review_id"],
                profile["content_fingerprint"],
                _text("profile"),
                now,
                later,
                _text("profile_display"),
            ),
        )
        con.commit()
    finally:
        con.close()
    return {
        "draft": draft["draft_id"],
        "review": review["review_id"],
        "profile": profile["confirmed_id"],
    }


def test_old_rows_read_back_exactly_and_show_their_original_english(tmp_path, monkeypatch):
    from product.display import catalog

    # 显示资源用仓库里冻结的版本（旧绑定指向的那一版还在）；登记表换成空的：当前版本与这里无关
    monkeypatch.setattr(catalog, "REGISTRY", tmp_path / "registry.yaml")
    svc = start(tmp_path)
    principal, b = svc.person()
    ids = _insert_old_rows(svc, principal)
    db = db_dump(svc.store.path)
    profile = b.get(f"/profiles/{ids['profile']}").json()
    assert profile["confirmed"] == json.loads(_text("profile"))  # 不补 v3.4 的空字段
    for kind, ident, part in (
        ("profile", ids["profile"], "/confirmed/"),
        ("review", ids["review"], "/review/"),
    ):
        env = display(b, kind, ident, "en").json()
        old = [f for f in env["fields"] if f["pointer"].startswith(part)]
        assert old and all(
            f["status"] == "provided" and f["resource_version"] == OLD_RESOURCES for f in old
        ), (kind, [f for f in old if f["status"] != "provided"][:3])
        zh = display(b, kind, ident, "zh").json()
        assert zh["source"] == env["source"]
    assert db_dump(svc.store.path) == db  # 读取不写库、不改写旧绑定
    draft = b.get(f"/drafts/{ids['draft']}").json()
    assert draft["draft"] == json.loads(_text("draft"))
    assert draft["migration"]["consent_required"]  # 旧问卷收集过的通勤上限：迁移前要同意清空
    r = b.post("/run-intents", json={"confirmed_id": ids["profile"]})
    assert (
        r.status_code == 409 and r.json()["code"] == "preflight_stale"
    )  # 旧画像不能直接准备新模拟
