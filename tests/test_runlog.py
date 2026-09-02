import json

import pytest

from common.runlog import CallRecord, RunDir, new_run_id


def test_run_ids_are_sortable_and_unique():
    ids = [new_run_id() for _ in range(50)]
    assert len(set(ids)) == 50
    assert all(i[8] == "T" and i.endswith(tuple("0123456789abcdef")) for i in ids)


def test_run_dir_is_created_and_meta_written(tmp_path):
    run = RunDir(root=tmp_path)
    assert run.path.is_dir()
    run.write_meta(config={"name": "t", "seed": 1})
    meta = json.loads(run.meta_path.read_text(encoding="utf-8"))
    assert meta["run_id"] == run.run_id
    assert meta["config"]["seed"] == 1
    # Provenance of the code that produced the run, not just of the data.
    assert "git_sha" in meta and "git_dirty" in meta and "python" in meta


def test_calls_round_trip_as_jsonl(tmp_path):
    run = RunDir(root=tmp_path)
    for i in range(3):
        run.log_call(
            CallRecord(
                run_id=run.run_id,
                seed=7,
                role="base_b",
                model="qwen3-max-2025-09-23",
                request={"messages": [{"role": "user", "content": f"hello {i}"}]},
                response={"content": "hi"},
                latency_ms=12.5,
                prompt_tokens=10,
                completion_tokens=2,
            )
        )
    rows = run.read_calls()
    assert len(rows) == 3
    assert {r["model"] for r in rows} == {"qwen3-max-2025-09-23"}
    assert rows[0]["request"]["messages"][0]["content"] == "hello 0"


def test_every_line_carries_the_model_version(tmp_path):
    """CLAUDE.md hard rule 6: the version string goes into every log line."""
    with pytest.raises(ValueError, match="hard rule 6"):
        CallRecord(run_id="r", seed=1, role="base_b", model="   ", request={})


def test_serialised_request_is_retained_for_the_leak_test(tmp_path):
    """README §10.2 asserts no ground-truth value appears in a serialised prompt.
    That test can only exist because the prompt is kept here verbatim."""
    run = RunDir(root=tmp_path)
    prompt = "City 3 rent is 2400 and City 7 rent is 1800."
    run.log_call(
        CallRecord(
            run_id=run.run_id,
            seed=1,
            role="base_b",
            model="m-2025-01-01",
            request={"messages": [{"role": "user", "content": prompt}]},
        )
    )
    raw = run.calls_path.read_text(encoding="utf-8")
    assert prompt in json.loads(raw)["request"]["messages"][0]["content"]


def test_unknown_call_fields_are_rejected():
    with pytest.raises(ValueError):
        CallRecord(run_id="r", seed=1, role="base_b", model="m-2025-01-01", request={}, oops=1)
