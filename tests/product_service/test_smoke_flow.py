"""一条正常的服务路径（合成资料包、每城 1 个分身、离线 mock）：链接 → 填写与审阅 → 确认画像 → 排队检查 → 确认开始 → 运行 → 结果。"""

from __future__ import annotations

from tests.product_service.support import finished_run, start


def test_link_to_result_uses_one_run_and_reports_hosted_service(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    done = finished_run(svc, b)
    assert b.quota() == {"total": 3, "used": 1, "reserved": 0, "available": 2}
    assert done["result_available"] is True
    assert "view_result" in done["allowed_actions"]
    r = b.get(f"/runs/{done['run_id']}/result")
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["run_id"] == done["run_id"]
    assert result["real_model_calls"] == 0
    assert result["planner"]["planner_kind"] == "mock"
