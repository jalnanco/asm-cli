#!/usr/bin/env python3
import json
import os
import sqlite3
import stat
import subprocess
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


ASM_PATH = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).with_name("asm")


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class AsmTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.logs_dir = self.root / "logs"
        self.logs_dir.mkdir()
        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)
        self.env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH','')}"
        self.env["ASM_MAX_SESSIONS"] = "20"
        self.env["ASM_CACHE_TTL_SECONDS"] = "0"
        self._seed_fake_bins()
        self._seed_claude_session()
        self._seed_codex_sessions()
        self._seed_opencode_session()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _seed_fake_bins(self) -> None:
        template = textwrap.dedent(
            """\
            #!/bin/sh
            printf 'cwd=%s argv=%s\\n' "$PWD" "$*" >> "{log}"
            exit 0
            """
        )
        for name in ["claude", "codex", "opencode", "cursor-agent", "gemini", "fzf", "jq", "sqlite3", "osascript", "lsof", "pbcopy", "python3", "rg"]:
            if name in {"fzf", "jq", "sqlite3", "osascript", "lsof", "pbcopy", "python3", "rg"}:
                real = shutil.which(name)  # type: ignore[name-defined]
                if real:
                    write_executable(
                        self.bin_dir / name,
                        f"#!/bin/sh\nexec {real} \"$@\"\n",
                    )
                    continue
            log = self.logs_dir / f"{name}.log"
            write_executable(self.bin_dir / name, template.format(log=log))

    def _seed_claude_session(self) -> None:
        project_dir = self.home / ".claude" / "projects" / "-Users-test"
        project_dir.mkdir(parents=True, exist_ok=True)
        session_file = project_dir / "11111111-1111-4111-8111-111111111111.jsonl"
        rows = [
            {
                "type": "user",
                "message": {"role": "user", "content": "build a regression suite"},
                "timestamp": "2026-04-02T08:00:00Z",
                "sessionId": "11111111-1111-4111-8111-111111111111",
                "cwd": str(self.home / "workspace"),
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "I will add tests and doctor output."}],
                },
                "timestamp": "2026-04-02T08:01:00Z",
                "sessionId": "11111111-1111-4111-8111-111111111111",
                "cwd": str(self.home / "workspace"),
            },
        ]
        with session_file.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        workspace = self.home / "workspace"
        workspace.mkdir(exist_ok=True)
        now = time.time()
        os.utime(session_file, (now, now))

    def _seed_opencode_session(self) -> None:
        db_dir = self.home / ".local" / "share" / "opencode"
        db_dir.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(db_dir / "opencode.db")
        db.executescript(
            """
            CREATE TABLE session (
              id TEXT PRIMARY KEY,
              title TEXT,
              directory TEXT,
              time_updated INTEGER
            );
            CREATE TABLE message (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              time_created INTEGER NOT NULL,
              time_updated INTEGER NOT NULL,
              data TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT INTO session (id,title,directory,time_updated) VALUES (?,?,?,?)",
            (
                "ses_old",
                "old opencode session",
                str(self.home),
                1_700_000_000_000,
            ),
        )
        db.commit()
        db.close()

    def _seed_codex_sessions(self) -> None:
        codex_dir = self.home / ".codex"
        sessions_dir = codex_dir / "sessions" / "2026" / "04" / "02"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        exact_id = "019d470b-a125-7533-ad4e-dc1d1219a9df"
        decoy_id = "119d470b-a125-7533-ad4e-dc1d1219a9df"
        exact_workspace = self.home / "codex-space"
        wrong_workspace = self.home / "wrong-space"
        exact_workspace.mkdir(exist_ok=True)
        wrong_workspace.mkdir(exist_ok=True)

        exact_file = sessions_dir / f"rollout-2026-04-02T08-00-00-{exact_id}.jsonl"
        decoy_file = sessions_dir / f"rollout-2026-04-02T07-00-00-{decoy_id}.jsonl"

        for target, cwd in [(exact_file, exact_workspace), (decoy_file, wrong_workspace)]:
            with target.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "session_meta", "payload": {"cwd": str(cwd)}}) + "\n")
                fh.write(
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": "review the API patch"}],
                            },
                        }
                    )
                    + "\n"
                )

        index_file = codex_dir / "session_index.jsonl"
        with index_file.open("w", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "id": exact_id,
                        "thread_name": "review the API patch",
                        "updated_at": "2026-04-02T08:02:03.123456Z",
                    }
                )
                + "\n"
            )

    def run_asm(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ASM_PATH), *args],
            text=True,
            capture_output=True,
            env=self.env,
            check=check,
        )

    def test_help_flag(self) -> None:
        cp = self.run_asm("--help")
        self.assertIn("asm - AI session manager", cp.stdout)
        self.assertIn("doctor", cp.stdout)

    def test_unknown_subcommand_shows_usage(self) -> None:
        cp = self.run_asm("nope", check=False)
        self.assertEqual(cp.returncode, 1)
        self.assertIn("unknown subcommand: nope", cp.stderr)
        self.assertIn("Usage:", cp.stderr)

    def test_doctor_reports_shell_mode(self) -> None:
        cp = self.run_asm("doctor")
        self.assertIn("shell integration: inactive", cp.stdout)
        self.assertIn("cache ttl:", cp.stdout)

    def test_list_prefers_recent_claude_over_old_opencode(self) -> None:
        cp = self.run_asm("list")
        first = cp.stdout.strip().splitlines()[0]
        self.assertFalse(first.startswith("opencode:"))
        self.assertTrue(
            first.startswith("claude:11111111-1111-4111-8111-111111111111")
            or first.startswith("codex:019d470b-a125-7533-ad4e-dc1d1219a9df")
        )

    def test_resume_without_wrapper_runs_child_shell_command(self) -> None:
        self.run_asm("resume", "claude:11111111-1111-4111-8111-111111111111")
        log = (self.logs_dir / "claude.log").read_text(encoding="utf-8")
        self.assertIn("--resume 11111111-1111-4111-8111-111111111111", log)
        self.assertIn(f"cwd={self.home / 'workspace'}", log)

    def test_codex_lookup_is_exact(self) -> None:
        self.run_asm("resume", "codex:019d470b-a125-7533-ad4e-dc1d1219a9df")
        log = (self.logs_dir / "codex.log").read_text(encoding="utf-8")
        self.assertIn("resume 019d470b-a125-7533-ad4e-dc1d1219a9df", log)
        self.assertIn(f"cwd={self.home / 'codex-space'}", log)
        self.assertNotIn("wrong-space", log)

    def test_export_contains_recent_excerpt_and_reopen_command(self) -> None:
        cp = self.run_asm("export", "claude:11111111-1111-4111-8111-111111111111")
        export_path = Path(cp.stdout.strip())
        text = export_path.read_text(encoding="utf-8")
        self.assertIn("Recent Transcript Excerpt", text)
        self.assertIn("build a regression suite", text)
        self.assertIn("claude --dangerously-skip-permissions --resume 11111111-1111-4111-8111-111111111111", text)

    def test_deep_handoff_preview_contains_deep_sections(self) -> None:
        cp = self.run_asm(
            "handoff",
            "--to",
            "codex",
            "--mode",
            "deep",
            "--preview",
            "claude:11111111-1111-4111-8111-111111111111",
        )
        text = cp.stdout
        self.assertIn("## Current State", text)
        self.assertIn("## Decisions Already Made", text)
        self.assertIn("## Recent Transcript", text)
        self.assertIn("## Suggested First Prompt", text)

    def test_active_tab_falls_back_to_all_when_missing(self) -> None:
        db_path = self.home / ".local" / "share" / "asm" / "meta.sqlite"
        self.run_asm("help")
        db = sqlite3.connect(db_path)
        db.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('active_tab', 'missing')")
        db.commit()
        db.close()
        cp = self.run_asm("__prompt_text")
        self.assertEqual(cp.stdout.strip(), "asm[All|child] →")

    def test_list_creates_cache_file(self) -> None:
        self.run_asm("list")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        self.assertTrue(cache_file.exists())

    def test_render_active_cache_ignores_missing_temp_file(self) -> None:
        missing = self.root / "missing-cache.json"
        cp = self.run_asm("__render_active_cache", str(missing))
        self.assertEqual(cp.stdout, "")

    def test_no_color_disables_ansi_in_list_output(self) -> None:
        env = self.env.copy()
        env["NO_COLOR"] = "1"
        cp = subprocess.run(
            [str(ASM_PATH), "list"],
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
        self.assertNotIn("\x1b[", cp.stdout)

    def test_header_counts_follow_cache_payload(self) -> None:
        self.run_asm("help")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                [
                    {"ref": "claude:11111111-1111-4111-8111-111111111111", "tab_name": "review"},
                    {"ref": "codex:019d470b-a125-7533-ad4e-dc1d1219a9df", "tab_name": ""},
                ]
            ),
            encoding="utf-8",
        )
        db_path = self.home / ".local" / "share" / "asm" / "meta.sqlite"
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT OR IGNORE INTO tabs (name, sort_order, created_at) VALUES ('review', 1, '2026-04-02T00:00:00Z')"
        )
        db.commit()
        db.close()
        env = self.env.copy()
        env["NO_COLOR"] = "1"
        cp = subprocess.run(
            [str(ASM_PATH), "__header_text"],
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
        self.assertEqual(cp.stdout.strip(), "TABS:  All:2  |  review")

    def test_standalone_script_reports_missing_runtime_helper(self) -> None:
        standalone = self.root / "standalone" / "asm"
        standalone.parent.mkdir(parents=True, exist_ok=True)
        standalone.write_text(ASM_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        standalone.chmod(standalone.stat().st_mode | stat.S_IXUSR)
        cp = subprocess.run(
            [str(standalone), "list"],
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("runtime helper not found", cp.stderr)

    def test_merge_cache_payload_merges_meta_tabs_and_tags(self) -> None:
        work = self.root / "merge"
        work.mkdir()
        sessions = work / "sessions.json"
        meta = work / "meta.json"
        tabs = work / "tabs.json"
        tags = work / "tags.json"
        sessions.write_text(
            json.dumps(
                {
                    "agent": "claude",
                    "id": "11111111-1111-4111-8111-111111111111",
                    "title": "hello",
                    "cwd": str(self.home / "workspace"),
                    "updated_epoch": 100,
                    "updated_display": "2026-04-02T08:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        meta.write_text(
            json.dumps(
                [
                    {
                        "agent": "claude",
                        "session_id": "11111111-1111-4111-8111-111111111111",
                        "pinned": 1,
                        "alias": "fav",
                        "last_opened_at": "2026-04-02T09:00:00Z",
                    }
                ]
            ),
            encoding="utf-8",
        )
        tabs.write_text(
            json.dumps(
                [
                    {
                        "agent": "claude",
                        "session_id": "11111111-1111-4111-8111-111111111111",
                        "tab_name": "review",
                    }
                ]
            ),
            encoding="utf-8",
        )
        tags.write_text(
            json.dumps(
                [
                    {
                        "agent": "claude",
                        "session_id": "11111111-1111-4111-8111-111111111111",
                        "tag": "hot",
                    }
                ]
            ),
            encoding="utf-8",
        )
        cp = self.run_asm("__merge_cache_payload", str(sessions), str(meta), str(tabs), str(tags))
        payload = json.loads(cp.stdout)
        row = payload[0]
        self.assertEqual(row["alias"], "fav")
        self.assertEqual(row["tab_name"], "review")
        self.assertEqual(row["tags_text"], "#hot")
        self.assertEqual(row["pinned"], 1)

    def test_extract_current_iterm_window_snapshot(self) -> None:
        work = self.root / "snapshot"
        work.mkdir()
        source = work / "source.json"
        target = work / "target.json"
        source.write_text(
            json.dumps(
                {
                    "version": 1,
                    "created_at": "2026-04-02T08:00:00Z",
                    "host": "test-host",
                    "window_count": 2,
                    "session_count": 3,
                    "windows": [
                        {
                            "window_order": 1,
                            "is_current_window": False,
                            "tabs": [{"sessions": [{"restore_command": "echo old"}]}],
                        },
                        {
                            "window_order": 2,
                            "is_current_window": True,
                            "tabs": [
                                {"sessions": [{"restore_command": "echo current"}, {"restore_command": "echo split"}]}
                            ],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.run_asm("__extract_current_iterm_window_snapshot", str(source), str(target), "current-window")
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["window_count"], 1)
        self.assertEqual(payload["session_count"], 2)
        self.assertEqual(payload["windows"][0]["window_order"], 1)
        self.assertTrue(payload["windows"][0]["is_current_window"])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
