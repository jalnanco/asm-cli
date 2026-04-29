#!/usr/bin/env python3
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 2
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_meta (
  agent TEXT NOT NULL,
  session_id TEXT NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0,
  alias TEXT NOT NULL DEFAULT '',
  last_opened_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (agent, session_id)
);
CREATE TABLE IF NOT EXISTS tabs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tab_sessions (
  agent TEXT NOT NULL,
  session_id TEXT NOT NULL,
  tab_id INTEGER NOT NULL,
  PRIMARY KEY (agent, session_id),
  FOREIGN KEY (tab_id) REFERENCES tabs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS session_tags (
  agent TEXT NOT NULL,
  session_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (agent, session_id, tag)
);
CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_relations (
  child_agent TEXT NOT NULL,
  child_session_id TEXT NOT NULL,
  parent_agent TEXT NOT NULL,
  parent_session_id TEXT NOT NULL,
  relation_kind TEXT NOT NULL DEFAULT 'clone',
  created_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (child_agent, child_session_id)
);
"""

MIGRATIONS = {
    1: """
CREATE TABLE IF NOT EXISTS session_relations (
  child_agent TEXT NOT NULL,
  child_session_id TEXT NOT NULL,
  parent_agent TEXT NOT NULL,
  parent_session_id TEXT NOT NULL,
  relation_kind TEXT NOT NULL DEFAULT 'clone',
  created_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (child_agent, child_session_id)
);
""",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ref(ref: str) -> tuple[str, str]:
    agent, separator, session_id = ref.partition(":")
    if separator != ":" or not agent or not session_id:
        raise ValueError(f"invalid ref: {ref}")
    return agent, session_id


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> int:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version == 0:
        conn.executescript(SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return SCHEMA_VERSION
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"unsupported schema version: {version}")
    while version < SCHEMA_VERSION:
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise RuntimeError(f"missing migration from schema version: {version}")
        conn.executescript(migration)
        version += 1
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    return version


def print_json(rows: list[dict[str, object]]) -> int:
    sys.stdout.write(json.dumps(rows, ensure_ascii=False))
    if rows:
        sys.stdout.write("\n")
    return 0


def cmd_ensure_schema(db_path: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
    return 0


def cmd_schema_version(db_path: str) -> int:
    with connect(db_path) as conn:
        version = ensure_schema(conn)
    print(version)
    return 0


def cmd_list_tabs(db_path: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute("SELECT name FROM tabs ORDER BY sort_order ASC, name ASC").fetchall()
    for row in rows:
        print(row["name"])
    return 0


def cmd_get_active_tab(db_path: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT value FROM app_state WHERE key = 'active_tab'").fetchone()
        active = row["value"] if row else ""
        if active == "All":
            print("All")
            return 0
        if active:
            exists = conn.execute("SELECT COUNT(*) FROM tabs WHERE name = ?", (active,)).fetchone()[0]
            if int(exists) != 0:
                print(active)
                return 0
    print("All")
    return 0


def cmd_set_active_tab(db_path: str, tab_name: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES ('active_tab', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (tab_name,),
        )
        conn.commit()
    return 0


def create_tab(conn: sqlite3.Connection, tab_name: str) -> None:
    conn.execute(
        """
        INSERT INTO tabs (name, sort_order, created_at)
        VALUES (
          ?,
          COALESCE((SELECT MAX(sort_order) + 1 FROM tabs), 1),
          ?
        )
        ON CONFLICT(name) DO NOTHING
        """,
        (tab_name, utc_now()),
    )


def cmd_create_tab(db_path: str, tab_name: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        create_tab(conn, tab_name)
        conn.commit()
    return 0


def cmd_assign_ref_to_tab(db_path: str, ref: str, tab_name: str) -> int:
    agent, session_id = parse_ref(ref)
    with connect(db_path) as conn:
        ensure_schema(conn)
        create_tab(conn, tab_name)
        conn.execute(
            """
            INSERT INTO tab_sessions (agent, session_id, tab_id)
            VALUES (
              ?,
              ?,
              (SELECT id FROM tabs WHERE name = ?)
            )
            ON CONFLICT(agent, session_id) DO UPDATE SET
              tab_id = excluded.tab_id
            """,
            (agent, session_id, tab_name),
        )
        conn.commit()
    return 0


def cmd_remove_ref_from_tab(db_path: str, ref: str) -> int:
    agent, session_id = parse_ref(ref)
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM tab_sessions WHERE agent = ? AND session_id = ?", (agent, session_id))
        conn.commit()
    return 0


def cmd_tab_count(db_path: str, tab_name: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        if tab_name == "All":
            count = conn.execute("SELECT COUNT(*) FROM session_meta").fetchone()[0]
        else:
            count = conn.execute(
                """
                SELECT COUNT(*)
                FROM tab_sessions ts
                JOIN tabs t ON t.id = ts.tab_id
                WHERE t.name = ?
                """,
                (tab_name,),
            ).fetchone()[0]
    print(count)
    return 0


def cmd_toggle_tag(db_path: str, ref: str, tag: str) -> int:
    agent, session_id = parse_ref(ref)
    with connect(db_path) as conn:
        ensure_schema(conn)
        cur = conn.execute(
            "DELETE FROM session_tags WHERE agent = ? AND session_id = ? AND tag = ?",
            (agent, session_id, tag),
        )
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO session_tags (agent, session_id, tag, created_at) VALUES (?, ?, ?, ?)",
                (agent, session_id, tag, utc_now()),
            )
        conn.commit()
    return 0


def cmd_list_ref_tags(db_path: str, ref: str) -> int:
    agent, session_id = parse_ref(ref)
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT tag
            FROM session_tags
            WHERE agent = ? AND session_id = ?
            ORDER BY tag ASC
            """,
            (agent, session_id),
        ).fetchall()
    for row in rows:
        print(row["tag"])
    return 0


def cmd_list_all_tags(db_path: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT tag
            FROM session_tags
            GROUP BY tag
            ORDER BY COUNT(*) DESC, tag ASC
            """
        ).fetchall()
    for row in rows:
        print(row["tag"])
    return 0


def cmd_export_session_meta(db_path: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT
              agent,
              session_id,
              pinned,
              COALESCE(alias, '') AS alias,
              COALESCE(last_opened_at, '') AS last_opened_at
            FROM session_meta
            """
        ).fetchall()
    return print_json([dict(row) for row in rows])


def cmd_export_tab_assignments(db_path: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT
              ts.agent,
              ts.session_id,
              t.name AS tab_name
            FROM tab_sessions ts
            JOIN tabs t ON t.id = ts.tab_id
            """
        ).fetchall()
    return print_json([dict(row) for row in rows])


def cmd_export_session_tags(db_path: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT
              agent,
              session_id,
              tag
            FROM session_tags
            ORDER BY agent ASC, session_id ASC, tag ASC
            """
        ).fetchall()
    return print_json([dict(row) for row in rows])


def cmd_export_session_relations(db_path: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT
              child_agent,
              child_session_id,
              parent_agent,
              parent_session_id,
              relation_kind,
              created_at
            FROM session_relations
            ORDER BY created_at DESC, child_agent ASC, child_session_id ASC
            """
        ).fetchall()
    return print_json([dict(row) for row in rows])


def cmd_update_last_opened(db_path: str, ref: str) -> int:
    agent, session_id = parse_ref(ref)
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO session_meta (agent, session_id, last_opened_at)
            VALUES (?, ?, ?)
            ON CONFLICT(agent, session_id) DO UPDATE SET
              last_opened_at = excluded.last_opened_at
            """,
            (agent, session_id, utc_now()),
        )
        conn.commit()
    return 0


def cmd_toggle_pin(db_path: str, ref: str) -> int:
    agent, session_id = parse_ref(ref)
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO session_meta (agent, session_id, pinned)
            VALUES (?, ?, 1)
            ON CONFLICT(agent, session_id) DO UPDATE SET
              pinned = CASE session_meta.pinned WHEN 1 THEN 0 ELSE 1 END
            """,
            (agent, session_id),
        )
        conn.commit()
    return 0


def cmd_set_pin(db_path: str, ref: str, desired: str) -> int:
    agent, session_id = parse_ref(ref)
    desired_pin = 0 if desired == "0" else 1
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO session_meta (agent, session_id, pinned)
            VALUES (?, ?, ?)
            ON CONFLICT(agent, session_id) DO UPDATE SET
              pinned = excluded.pinned
            """,
            (agent, session_id, desired_pin),
        )
        conn.commit()
    return 0


def cmd_set_alias(db_path: str, ref: str, alias_text: str) -> int:
    agent, session_id = parse_ref(ref)
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO session_meta (agent, session_id, alias)
            VALUES (?, ?, ?)
            ON CONFLICT(agent, session_id) DO UPDATE SET
              alias = excluded.alias
            """,
            (agent, session_id, alias_text),
        )
        conn.commit()
    return 0


def cmd_record_relation(db_path: str, child_ref: str, parent_ref: str, relation_kind: str = "clone") -> int:
    child_agent, child_session_id = parse_ref(child_ref)
    parent_agent, parent_session_id = parse_ref(parent_ref)
    if child_ref == parent_ref:
        raise ValueError("child and parent refs must differ")
    with connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO session_relations (
              child_agent,
              child_session_id,
              parent_agent,
              parent_session_id,
              relation_kind,
              created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(child_agent, child_session_id) DO UPDATE SET
              parent_agent = excluded.parent_agent,
              parent_session_id = excluded.parent_session_id,
              relation_kind = excluded.relation_kind,
              created_at = excluded.created_at
            """,
            (child_agent, child_session_id, parent_agent, parent_session_id, relation_kind, utc_now()),
        )
        conn.commit()
    return 0


def main() -> int:
    try:
        cmd = sys.argv[1]
        db_path = sys.argv[2]
        if cmd == "ensure-schema":
            return cmd_ensure_schema(db_path)
        if cmd == "schema-version":
            return cmd_schema_version(db_path)
        if cmd == "list-tabs":
            return cmd_list_tabs(db_path)
        if cmd == "get-active-tab":
            return cmd_get_active_tab(db_path)
        if cmd == "set-active-tab":
            return cmd_set_active_tab(db_path, sys.argv[3])
        if cmd == "create-tab":
            return cmd_create_tab(db_path, sys.argv[3])
        if cmd == "assign-ref-to-tab":
            return cmd_assign_ref_to_tab(db_path, sys.argv[3], sys.argv[4])
        if cmd == "remove-ref-from-tab":
            return cmd_remove_ref_from_tab(db_path, sys.argv[3])
        if cmd == "tab-count":
            return cmd_tab_count(db_path, sys.argv[3])
        if cmd == "toggle-tag":
            return cmd_toggle_tag(db_path, sys.argv[3], sys.argv[4])
        if cmd == "list-ref-tags":
            return cmd_list_ref_tags(db_path, sys.argv[3])
        if cmd == "list-all-tags":
            return cmd_list_all_tags(db_path)
        if cmd == "export-session-meta":
            return cmd_export_session_meta(db_path)
        if cmd == "export-tab-assignments":
            return cmd_export_tab_assignments(db_path)
        if cmd == "export-session-tags":
            return cmd_export_session_tags(db_path)
        if cmd == "export-session-relations":
            return cmd_export_session_relations(db_path)
        if cmd == "update-last-opened":
            return cmd_update_last_opened(db_path, sys.argv[3])
        if cmd == "toggle-pin":
            return cmd_toggle_pin(db_path, sys.argv[3])
        if cmd == "set-pin":
            return cmd_set_pin(db_path, sys.argv[3], sys.argv[4])
        if cmd == "set-alias":
            return cmd_set_alias(db_path, sys.argv[3], sys.argv[4])
        if cmd == "record-relation":
            relation_kind = sys.argv[5] if len(sys.argv) > 5 else "clone"
            return cmd_record_relation(db_path, sys.argv[3], sys.argv[4], relation_kind)
    except (IndexError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
