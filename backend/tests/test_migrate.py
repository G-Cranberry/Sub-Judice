from sqlalchemy import create_engine, inspect, text

from app.migrate import upgrade


def test_v01_database_gets_new_columns_without_losing_rows(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/old.db")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE cases (id INTEGER PRIMARY KEY, title VARCHAR(300))"))
        c.execute(text("INSERT INTO cases (id, title) VALUES (1, 'old case')"))
    assert sorted(upgrade(eng)) == ["cases.access_token_hash", "cases.custody_head", "cases.custody_seq", "cases.source", "cases.submitted_by"]
    cols = {c["name"] for c in inspect(eng).get_columns("cases")}
    assert {"custody_head", "custody_seq", "access_token_hash"} <= cols
    with eng.connect() as c:
        assert c.execute(text("SELECT title, custody_seq FROM cases")).one() == ("old case", 0)
    assert upgrade(eng) == []  # idempotent
