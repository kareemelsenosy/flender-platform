from __future__ import annotations


def test_expand_batch_jobs_keeps_one_job_per_sheet_url(test_app):
    sheets_routes = test_app["sheets_routes"]
    url_one = "https://docs.google.com/spreadsheets/d/abc123/edit#gid=0"
    url_two = "https://docs.google.com/spreadsheets/d/xyz789/edit#gid=0"

    jobs = sheets_routes._expand_batch_jobs(
        [url_one, url_two],
        {
            url_one: ["ReOrder_Dubai", "PreOrder_Carhartt WIP_2026-04"],
            url_two: ["Main"],
        },
    )

    assert len(jobs) == 2
    assert jobs[0]["url"] == url_one
    assert jobs[0]["selected_tabs"] == ["ReOrder_Dubai", "PreOrder_Carhartt WIP_2026-04"]
    assert jobs[1]["url"] == url_two
    assert jobs[1]["selected_tabs"] == ["Main"]


def test_import_batch_initializes_one_parallel_job_per_sheet_url(
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

    url_one = "https://docs.google.com/spreadsheets/d/abc123/edit#gid=0"
    url_two = "https://docs.google.com/spreadsheets/d/xyz789/edit#gid=0"

    response = client.post(
        "/sheets/import-batch",
        json={
            "urls": [url_one, url_two],
            "selected_tabs": {
                url_one: ["Sheet One", "Sheet Two"],
                url_two: ["Main"],
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
    assert batch["jobs"][0]["url"] == url_one
    assert batch["jobs"][1]["url"] == url_two
