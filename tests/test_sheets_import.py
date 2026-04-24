from __future__ import annotations


def test_expand_batch_jobs_splits_selected_tabs(test_app):
    sheets_routes = test_app["sheets_routes"]

    jobs = sheets_routes._expand_batch_jobs(
        ["https://docs.google.com/spreadsheets/d/abc123/edit#gid=0"],
        {
            "https://docs.google.com/spreadsheets/d/abc123/edit#gid=0": [
                "ReOrder_Dubai",
                "PreOrder_Carhartt WIP_2026-04",
            ]
        },
    )

    assert len(jobs) == 2
    assert jobs[0]["selected_tabs"] == ["ReOrder_Dubai"]
    assert jobs[0]["label"] == "ReOrder_Dubai"
    assert jobs[1]["selected_tabs"] == ["PreOrder_Carhartt WIP_2026-04"]
    assert jobs[1]["label"] == "PreOrder_Carhartt WIP_2026-04"


def test_import_batch_initializes_one_job_per_selected_tab(
    client,
    login_as,
    test_app,
    monkeypatch,
):
    login_as()
    sheets_routes = test_app["sheets_routes"]

    cred_path = test_app["temp_root"] / "google-test-creds.json"
    cred_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sheets_routes, "_get_credentials_path", lambda _uid: str(cred_path))

    def fake_create_task(coro):
        coro.close()

        class _DummyTask:
            pass

        return _DummyTask()

    monkeypatch.setattr(sheets_routes.asyncio, "create_task", fake_create_task)

    url = "https://docs.google.com/spreadsheets/d/abc123/edit#gid=0"
    response = client.post(
        "/sheets/import-batch",
        json={
            "urls": [url],
            "selected_tabs": {
                url: ["Sheet One", "Sheet Two"],
            },
            "save_images": True,
            "search_missing": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 2

    batch = sheets_routes._batch_progress[data["batch_id"]]
    assert batch["total"] == 2
    assert [job["label"] for job in batch["jobs"]] == ["Sheet One", "Sheet Two"]
