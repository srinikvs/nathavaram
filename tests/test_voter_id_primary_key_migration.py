"""Migration 0007: voter_id becomes the primary key and the columns are reshaped."""

from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from app.config import ROOT

TOMBSTONE_UUID = "11111111-2222-3333-4444-555555555555"
TOMBSTONE_PLACEHOLDER = TOMBSTONE_UUID.replace("-", "")[:32]
STAMP = "2026-11-12T10:00:00.000000Z"
OLD_HEAD = "0006_fold_house_no_into_address"

REQUESTED_COLUMNS = [
    "voter_id", "booth", "booth_s_no", "ward", "ward_s_no", "first_name", "last_name",
    "relative_name", "house_no", "age", "street", "mobile", "caste", "village",
    "panchayath", "mandal", "current_city", "is_local", "party", "constituency",
]
SYNC_COLUMNS = {"created_at", "updated_at", "revision", "deleted_at", "source_id"}


def _config(db_path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + db_path.as_posix())
    return cfg


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """A database at revision 0006 holding folded-address rows and one tombstone."""
    db_path = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + db_path.as_posix())
    command.upgrade(_config(db_path), OLD_HEAD)
    rows = [
        # (id, voter_id, first_name, surname, address, gender, profession, deleted_at, revision)
        ("a1", "AAA1000001", "Asha", "Rao", "H.No 12-A, Main St", "Female", "Teacher", None, 1),
        ("a2", "AAA1000002", "Bala", None, "H.No 7", "Male", "Farmer", None, 2),
        ("a3", "AAA1000003", "Chandu", "Dev", "Temple Road", "Male", None, None, 3),
        ("a4", "AAA1000004", "Devi", None, None, "Female", None, None, 4),
        (TOMBSTONE_UUID, TOMBSTONE_PLACEHOLDER, "", None, None, "Unknown", None, STAMP, 5),
    ]
    con = sqlite3.connect(db_path)
    for row_id, voter_id, first, surname, address, gender, profession, deleted, revision in rows:
        con.execute(
            "INSERT INTO voters (id, booth, bs_no_2024, ward, ward_s_no, voter_id, first_name,"
            " surname, address, village, permanent_address, is_local, gender, age, c_city,"
            " profession, party, created_at, updated_at, revision, deleted_at, source_id)"
            " VALUES (?, 'B1', '101', 'W2', '15', ?, ?, ?, ?, 'Vill', 'Perm lane', 1, ?, 40,"
            " 'Hyderabad', ?, 'TDP', ?, ?, ?, ?, 'web:admin')",
            (row_id, voter_id, first, surname, address, gender, profession, STAMP, STAMP, revision, deleted),
        )
    con.commit()
    con.close()
    return db_path


def _rows(db_path) -> dict[str, sqlite3.Row]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    found = {row["voter_id"]: row for row in con.execute("SELECT * FROM voters")}
    con.close()
    return found


def _table_info(db_path) -> list[tuple]:
    con = sqlite3.connect(db_path)
    info = list(con.execute("PRAGMA table_info(voters)"))
    con.close()
    return info


def test_upgrade_makes_voter_id_the_only_primary_key(old_db):
    command.upgrade(_config(old_db), "head")

    info = _table_info(old_db)
    assert [row[1] for row in info if row[5]] == ["voter_id"]
    assert "id" not in [row[1] for row in info]


def test_upgrade_has_exactly_the_requested_columns(old_db):
    command.upgrade(_config(old_db), "head")

    names = [row[1] for row in _table_info(old_db)]
    assert [name for name in names if name not in SYNC_COLUMNS] == REQUESTED_COLUMNS


def test_upgrade_renames_columns_and_keeps_values(old_db):
    command.upgrade(_config(old_db), "head")

    row = _rows(old_db)["AAA1000001"]
    assert row["booth_s_no"] == "101"
    assert row["last_name"] == "Rao"
    assert row["current_city"] == "Hyderabad"
    assert row["village"] == "Vill"
    assert row["party"] == "TDP"
    assert row["is_local"] == 1
    assert row["revision"] == 1
    assert row["created_at"] == STAMP
    assert row["panchayath"] is None and row["mandal"] is None


def test_upgrade_splits_house_no_back_out_of_address(old_db):
    command.upgrade(_config(old_db), "head")

    rows = _rows(old_db)
    assert (rows["AAA1000001"]["house_no"], rows["AAA1000001"]["street"]) == ("12-A", "Main St")
    assert (rows["AAA1000002"]["house_no"], rows["AAA1000002"]["street"]) == ("7", None)
    assert (rows["AAA1000003"]["house_no"], rows["AAA1000003"]["street"]) == (None, "Temple Road")
    assert (rows["AAA1000004"]["house_no"], rows["AAA1000004"]["street"]) == (None, None)


def test_upgrade_keeps_tombstone_under_its_placeholder_key(old_db):
    command.upgrade(_config(old_db), "head")

    tombstone = _rows(old_db)[TOMBSTONE_PLACEHOLDER]
    assert tombstone["deleted_at"] == STAMP
    assert tombstone["first_name"] == ""
    assert tombstone["street"] is None and tombstone["house_no"] is None


def test_upgrade_drops_gender_permanent_address_and_profession(old_db):
    command.upgrade(_config(old_db), "head")

    names = {row[1] for row in _table_info(old_db)}
    assert not {"gender", "permanent_address", "profession"} & names


def test_upgrade_keeps_every_row(old_db):
    command.upgrade(_config(old_db), "head")

    assert len(_rows(old_db)) == 5


def test_downgrade_restores_old_shape_with_folded_address(old_db):
    command.upgrade(_config(old_db), "head")
    command.downgrade(_config(old_db), OLD_HEAD)

    names = {row[1] for row in _table_info(old_db)}
    assert {"id", "surname", "bs_no_2024", "c_city", "address", "gender"} <= names
    rows = _rows(old_db)
    assert rows["AAA1000001"]["address"] == "H.No 12-A, Main St"
    assert rows["AAA1000002"]["address"] == "H.No 7"
    assert rows["AAA1000003"]["address"] == "Temple Road"
    assert rows["AAA1000001"]["gender"] == "Unknown"
    assert rows["AAA1000001"]["surname"] == "Rao"
    assert len(rows) == 5


def test_upgrade_on_empty_table_works(tmp_path, monkeypatch):
    db_path = tmp_path / "empty.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + db_path.as_posix())

    command.upgrade(_config(db_path), "head")

    assert _rows(db_path) == {}


def test_house_no_split_keeps_long_or_wordy_text_in_street(tmp_path, monkeypatch):
    db_path = tmp_path / "long.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + db_path.as_posix())
    command.upgrade(_config(db_path), OLD_HEAD)
    wordy = "H.No 12 near the old temple opposite the primary school and the post office on the main road"
    long_number = "H.No " + "9" * 70 + ", Main St"
    con = sqlite3.connect(db_path)
    for n, address in enumerate((wordy, long_number), start=1):
        con.execute(
            "INSERT INTO voters (id, voter_id, first_name, gender, address, created_at, updated_at, revision)"
            " VALUES (?, ?, 'X', 'Male', ?, ?, ?, ?)",
            (f"id{n}", f"LNG000000{n}", address, STAMP, STAMP, n),
        )
    con.commit()
    con.close()

    command.upgrade(_config(db_path), "head")

    rows = _rows(db_path)
    for key, address in (("LNG0000001", wordy), ("LNG0000002", long_number)):
        assert rows[key]["house_no"] is None
        assert rows[key]["street"] == address


def test_upgrade_resumes_after_a_run_that_died_after_the_rename(old_db):
    con = sqlite3.connect(old_db)
    for name in ("uq_voters_voter_id_active", "ix_voters_revision", "ix_voters_booth", "ix_voters_ward"):
        con.execute(f"DROP INDEX {name}")
    con.execute("ALTER TABLE voters RENAME TO voters_old")
    con.execute("CREATE TABLE voters (voter_id VARCHAR(32) NOT NULL PRIMARY KEY)")  # half-built
    con.commit()
    con.close()

    command.upgrade(_config(old_db), "head")

    assert len(_rows(old_db)) == 5
    con = sqlite3.connect(old_db)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "voters_old" not in tables


def test_constituency_is_added_last_and_downgrade_drops_it(old_db):
    command.upgrade(_config(old_db), "head")

    names = [row[1] for row in _table_info(old_db)]
    assert [n for n in names if n not in SYNC_COLUMNS][-1] == "constituency"
    assert _rows(old_db)["AAA1000001"]["constituency"] is None

    command.downgrade(_config(old_db), "0007_voter_id_primary_key")

    assert "constituency" not in [row[1] for row in _table_info(old_db)]
    assert len(_rows(old_db)) == 5
