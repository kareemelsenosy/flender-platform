from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _reload_app_modules():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    main = importlib.import_module("app.main")
    return {
        "main": main,
        "database": importlib.import_module("app.database"),
        "models": importlib.import_module("app.models"),
        "auth": importlib.import_module("app.auth"),
        "api_routes": importlib.import_module("app.routers.api_routes"),
        "search_routes": importlib.import_module("app.routers.search_routes"),
        "review_routes": importlib.import_module("app.routers.review_routes"),
        "generate_routes": importlib.import_module("app.routers.generate_routes"),
        "sheets_routes": importlib.import_module("app.routers.sheets_routes"),
        "notifications": importlib.import_module("app.services.notifications"),
        "task_state": importlib.import_module("app.services.task_state"),
    }


@pytest.fixture(scope="session")
def test_app(tmp_path_factory):
    temp_root = tmp_path_factory.mktemp("flender-smoke")
    os.environ["DATABASE_URL"] = f"sqlite:///{temp_root / 'test.db'}"
    os.environ["UPLOAD_DIR"] = str(temp_root / "uploads")
    os.environ["OUTPUT_DIR"] = str(temp_root / "output")
    os.environ["SECRET_KEY"] = "test-secret-key"
    os.environ["APP_BASE_URL"] = "http://testserver"
    os.environ["EMAIL_VERIFICATION_REQUIRED"] = "false"
    os.environ["INTERNAL_API_ENABLED"] = "true"

    modules = _reload_app_modules()
    modules["database"].init_db()
    modules["upload_dir"] = Path(os.environ["UPLOAD_DIR"])
    modules["output_dir"] = Path(os.environ["OUTPUT_DIR"])
    modules["temp_root"] = temp_root
    return modules


def _clear_database(test_app):
    models = test_app["models"]
    db = test_app["database"].SessionLocal()
    try:
        for model in (
            models.GeneratedFile,
            models.PasswordResetToken,
            models.EmailVerificationCode,
            models.UniqueItem,
            models.UploadedFile,
            models.ColumnMappingFormat,
            models.BrandSearchConfig,
            models.SearchCache,
            models.Session,
            models.User,
        ):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()


def _clear_runtime_state(test_app):
    test_app["search_routes"]._search_progress.clear()
    test_app["generate_routes"]._progress.clear()
    test_app["generate_routes"]._completed_exports.clear()
    test_app["sheets_routes"]._batch_progress.clear()
    test_app["sheets_routes"]._user_batches.clear()
    test_app["sheets_routes"]._completed_batches.clear()
    test_app["notifications"]._store.clear()


@pytest.fixture(autouse=True)
def isolated_state(test_app, monkeypatch):
    monkeypatch.setattr(test_app["notifications"], "add_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(test_app["task_state"], "restore_on_startup", lambda: ({}, {}), raising=False)
    monkeypatch.setattr(test_app["task_state"], "save_batch", lambda *args, **kwargs: None, raising=False)

    _clear_database(test_app)
    _clear_runtime_state(test_app)

    for folder in (test_app["upload_dir"], test_app["output_dir"]):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True, exist_ok=True)

    yield

    _clear_runtime_state(test_app)


@pytest.fixture
def client(test_app):
    with TestClient(test_app["main"].app, base_url="http://testserver") as tc:
        yield tc


@pytest.fixture
def make_user(test_app):
    def _make_user(
        username: str = "alice",
        password: str = "password123",
        email: str = "alice@flendergroup.com",
    ) -> dict:
        db = test_app["database"].SessionLocal()
        try:
            user = test_app["models"].User(
                username=username,
                email=email,
                password_hash=test_app["auth"].hash_password(password),
                email_verified=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            return {"id": user.id, "username": username, "password": password, "email": email}
        finally:
            db.close()

    return _make_user


@pytest.fixture
def login_as(client, make_user):
    def _login_as(**kwargs) -> dict:
        user = make_user(**kwargs)
        response = client.post(
            "/login",
            data={"username": user["username"], "password": user["password"]},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/"
        return user

    return _login_as


@pytest.fixture
def db_session(test_app):
    db = test_app["database"].SessionLocal()
    try:
        yield db
    finally:
        db.close()
