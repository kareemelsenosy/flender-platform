"""A search a restart cut off must not show "Searching" on the dashboard for ever."""
from app.services.task_state import reset_interrupted_searches


def test_interrupted_searches_go_back_to_review(test_app, db_session, make_user):
    models = test_app["models"]
    uid = make_user()["id"]
    sessions = {}
    for name, done in (("half searched", True), ("never searched", False)):
        s = models.Session(user_id=uid, name=name, source_type="excel_upload", status="searching")
        db_session.add(s)
        db_session.flush()
        db_session.add(models.UniqueItem(session_id=s.id, item_code="A",
                                         search_status="done" if done else "pending"))
        sessions[name] = s
    db_session.commit()

    assert reset_interrupted_searches(db_session) == 1
    db_session.expire_all()
    assert sessions["half searched"].status == "reviewing"
    assert sessions["never searched"].status == "searching"
