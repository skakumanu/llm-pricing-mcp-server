"""v1.87.0 conversation persistence with org attribution and retention (Spec AC9)."""
import asyncio
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import agent.conversation as conv  # noqa: E402
from agent.conversation import ConversationStore, SQLiteConversationStore  # noqa: E402
from src.config.settings import Settings  # noqa: E402

DAY = 86400


def run(coro):
    return asyncio.run(coro)


def _backdate(db_path, conv_id, days):
    with sqlite3.connect(db_path) as c:
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (time.time() - days * DAY, conv_id))


def _sqlite(tmp_path, retention=90):
    path = str(tmp_path / "conversations.db")
    store = SQLiteConversationStore(path, max_turns=5, retention_days=retention)
    run(store.initialize())
    return store, path


async def _save(store, cid, org=None, text="hi"):
    _, h = await store.get_or_create(cid, org_id=org)
    h.add("user", text)
    await store.save(cid, h, org_id=org)


def test_default_retention_is_90_days():
    assert Settings(_env_file=None).conversation_retention_days == 90


def test_sqlite_purges_old_keeps_recent(tmp_path):
    store, path = _sqlite(tmp_path)
    run(_save(store, "old"))
    run(_save(store, "new"))
    _backdate(path, "old", 91)
    _backdate(path, "new", 1)
    assert run(store.purge_expired()) == 1
    ids = {c["id"] for c in run(store.list_conversations())}
    assert ids == {"new"}


def test_boundary_just_inside_window_is_kept(tmp_path):
    store, path = _sqlite(tmp_path)
    run(_save(store, "edge"))
    _backdate(path, "edge", 89)
    assert run(store.purge_expired()) == 0


@pytest.mark.parametrize("days", [0, -1])
def test_zero_or_negative_disables_purge(tmp_path, days):
    store, path = _sqlite(tmp_path, retention=days)
    run(_save(store, "old"))
    _backdate(path, "old", 4000)
    assert run(store.purge_expired()) == 0
    assert len(run(store.list_conversations())) == 1


def test_purge_runs_at_init(tmp_path):
    store, path = _sqlite(tmp_path)
    run(_save(store, "old"))
    _backdate(path, "old", 200)
    run(conv.init_conversation_store(path, 5, 90))
    try:
        assert run(conv.get_conversation_store().list_conversations()) == []
    finally:
        conv._conversation_store = None


def test_purge_runs_after_a_save_without_restart(tmp_path):
    store, path = _sqlite(tmp_path)
    run(_save(store, "old"))
    _backdate(path, "old", 120)
    run(_save(store, "fresh"))
    assert {c["id"] for c in run(store.list_conversations())} == {"fresh"}


def test_purged_conversation_leaves_the_cache(tmp_path):
    store, path = _sqlite(tmp_path)
    run(_save(store, "old", text="remember me"))
    _backdate(path, "old", 120)
    run(store.purge_expired())
    _, h = run(store.get_or_create("old"))
    assert len(h) == 0


def test_org_attribution_and_never_detached(tmp_path):
    store, path = _sqlite(tmp_path)
    run(_save(store, "c1", org="org-1"))
    run(_save(store, "c1", org=None, text="later anonymous turn"))
    rows = run(store.list_for_org("org-1"))
    assert [r["id"] for r in rows] == ["c1"] and len(rows[0]["messages"]) == 2
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT org_id FROM conversations WHERE id='c1'").fetchone()[0] == "org-1"


def test_anonymous_conversation_has_null_org(tmp_path):
    store, path = _sqlite(tmp_path)
    run(_save(store, "anon"))
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT org_id FROM conversations WHERE id='anon'").fetchone()[0] is None
    assert run(store.list_for_org("org-1")) == []


def test_delete_for_org_only_touches_that_org(tmp_path):
    store, _ = _sqlite(tmp_path)
    run(_save(store, "a1", org="org-a"))
    run(_save(store, "a2", org="org-a"))
    run(_save(store, "b1", org="org-b"))
    assert run(store.delete_for_org("org-a")) == 2
    assert run(store.list_for_org("org-a")) == []
    assert len(run(store.list_for_org("org-b"))) == 1
    _, h = run(store.get_or_create("a1"))  # cache evicted too
    assert len(h) == 0


def test_existing_database_without_org_column_is_migrated(tmp_path):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY, messages TEXT NOT NULL, updated_at REAL NOT NULL)")
        c.execute("INSERT INTO conversations VALUES ('legacy', '[]', ?)", (time.time(),))
    store = SQLiteConversationStore(path, retention_days=90)
    run(store.initialize())
    run(store.initialize())  # idempotent
    run(_save(store, "legacy", org="org-1"))
    assert [r["id"] for r in run(store.list_for_org("org-1"))] == ["legacy"]


# in-memory store parity ------------------------------------------------------


def test_memory_store_purges_old_keeps_recent():
    store = ConversationStore(max_turns=5, retention_days=90)
    run(_save(store, "old"))
    run(_save(store, "new"))
    store._updated["old"] = time.time() - 91 * DAY
    store._updated["new"] = time.time() - 1 * DAY
    assert run(store.purge_expired()) == 1
    assert set(store._conversations) == {"new"}


def test_memory_store_purges_after_save_and_zero_disables():
    store = ConversationStore(max_turns=5, retention_days=90)
    run(_save(store, "old"))
    store._updated["old"] = time.time() - 200 * DAY
    run(_save(store, "fresh"))
    assert set(store._conversations) == {"fresh"}
    off = ConversationStore(max_turns=5, retention_days=0)
    run(_save(off, "old"))
    off._updated["old"] = time.time() - 4000 * DAY
    assert run(off.purge_expired()) == 0 and "old" in off._conversations


def test_memory_store_attribution_list_and_delete():
    store = ConversationStore(max_turns=5)
    run(_save(store, "c1", org="org-1"))
    run(_save(store, "c1", org=None, text="anon"))
    run(_save(store, "c2", org="org-2"))
    rows = run(store.list_for_org("org-1"))
    assert [r["id"] for r in rows] == ["c1"] and len(rows[0]["messages"]) == 2
    assert run(store.delete_for_org("org-1")) == 1
    assert run(store.list_for_org("org-1")) == [] and len(run(store.list_for_org("org-2"))) == 1


def test_list_conversations_output_shape_unchanged(tmp_path):
    store, _ = _sqlite(tmp_path)
    run(_save(store, "x", org="o"))
    assert set(run(store.list_conversations())[0]) == {"id", "updated_at", "turn_count", "preview"}


def test_first_owner_wins_sqlite(tmp_path):
    store, _ = _sqlite(tmp_path)

    async def go():
        cid, h = await store.get_or_create("sup-x", org_id="orgA")
        await store.save(cid, h, org_id="orgA")
        await store.save(cid, h, org_id="orgB")
        return await store.owner_of(cid), await store.list_for_org("orgB")

    owner, b_rows = run(go())
    assert owner == "orgA" and b_rows == []


def test_first_owner_wins_memory():
    store = ConversationStore()

    async def go():
        cid, h = await store.get_or_create("sup-x", org_id="orgA")
        await store.save(cid, h, org_id="orgA")
        await store.save(cid, h, org_id="orgB")
        return await store.owner_of(cid)

    assert run(go()) == "orgA"
