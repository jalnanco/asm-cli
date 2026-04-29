#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import os
import pty
import select
import shlex
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


DEFAULT_ASM_PATH = Path(__file__).resolve().parents[1] / "bin" / "asm"
VERSION_PATH = Path(__file__).resolve().parents[1] / "VERSION"
ASM_PATH = DEFAULT_ASM_PATH
if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
    ASM_PATH = Path(sys.argv[1]).resolve()


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
        self.env["ASM_ITERM_SCAN_TIMEOUT"] = "2"
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
        passthrough_bins = {"fzf", "jq", "sqlite3", "lsof", "pbcopy", "python3", "rg"}
        for name in ["claude", "codex", "opencode", "cursor-agent", "gemini", "fzf", "jq", "sqlite3", "lsof", "pbcopy", "python3", "rg"]:
            if name in passthrough_bins:
                real = shutil.which(name)  # type: ignore[name-defined]
                if real:
                    write_executable(
                        self.bin_dir / name,
                        f"#!/bin/sh\nexec {real} \"$@\"\n",
                    )
                    continue
            log = self.logs_dir / f"{name}.log"
            write_executable(self.bin_dir / name, template.format(log=log))

        real_uname = shutil.which("uname")
        if not real_uname:
            raise RuntimeError("uname is required for self-test")
        write_executable(
            self.bin_dir / "uname",
            textwrap.dedent(
                f"""\
                #!/bin/sh
                case "${{1:-}}" in
                  -s)
                    if [ -n "${{ASM_TEST_UNAME_S:-}}" ]; then
                      printf '%s\\n' "$ASM_TEST_UNAME_S"
                      exit 0
                    fi
                    ;;
                  -m)
                    if [ -n "${{ASM_TEST_UNAME_M:-}}" ]; then
                      printf '%s\\n' "$ASM_TEST_UNAME_M"
                      exit 0
                    fi
                    ;;
                esac
                exec {real_uname} "$@"
                """
            ),
        )
        write_executable(
            self.bin_dir / "open",
            textwrap.dedent(
                """\
                #!/bin/sh
                if [ "${1:-}" = "-Ra" ] && { [ "${2:-}" = "iTerm" ] || [ "${2:-}" = "iTerm2" ]; }; then
                  if [ "${ASM_TEST_OPEN_ITERM:-0}" = "1" ]; then
                    exit 0
                  fi
                  exit 1
                fi
                exit 1
                """
            ),
        )
        write_executable(
            self.bin_dir / "pgrep",
            textwrap.dedent(
                """\
                #!/bin/sh
                mode="${ASM_TEST_PGREP_MODE:-none}"
                if [ "${1:-}" = "-x" ] && [ "${2:-}" = "iTerm2" ]; then
                  case "$mode" in
                    exact|both)
                      exit 0
                      ;;
                  esac
                  exit 1
                fi
                if [ "${1:-}" = "-f" ] && [ "${2:-}" = "/Contents/MacOS/iTerm2$" ]; then
                  case "$mode" in
                    path|both)
                      exit 0
                      ;;
                  esac
                  exit 1
                fi
                exit 1
                """
            ),
        )
        write_executable(
            self.bin_dir / "osascript",
            textwrap.dedent(
                """\
                #!/bin/sh
                script=""
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "-e" ] && [ "$#" -ge 2 ]; then
                    script="$2"
                    shift 2
                    continue
                  fi
                  shift
                done

                if printf '%s' "$script" | grep -Fq 'if application "iTerm2" is running then'; then
                  printf '%s\\n' "${ASM_TEST_OSASCRIPT_RUNNING_RESULT:-no}"
                  exit "${ASM_TEST_OSASCRIPT_RUNNING_EXIT:-0}"
                fi

                if printf '%s' "$script" | grep -Fq 'return (count of windows) as string'; then
                  printf '%s\\n' "${ASM_TEST_OSASCRIPT_WINDOW_COUNT_RESULT:-0}"
                  exit "${ASM_TEST_OSASCRIPT_WINDOW_COUNT_EXIT:-0}"
                fi

                if printf '%s' "$script" | grep -Fq 'return "ok"'; then
                  if [ -n "${ASM_TEST_OSASCRIPT_OK_RESULT:-}" ]; then
                    printf '%s\\n' "$ASM_TEST_OSASCRIPT_OK_RESULT"
                  fi
                  exit "${ASM_TEST_OSASCRIPT_OK_EXIT:-0}"
                fi

                if [ -n "${ASM_TEST_OSASCRIPT_DEFAULT_RESULT:-}" ]; then
                  printf '%s\\n' "$ASM_TEST_OSASCRIPT_DEFAULT_RESULT"
                fi
                exit "${ASM_TEST_OSASCRIPT_DEFAULT_EXIT:-0}"
                """
            ),
        )

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

    def _seed_qmd_bin(self) -> None:
        write_executable(
            self.bin_dir / "qmd",
            textwrap.dedent(
                """\
                #!/bin/sh
                filtered=""
                while [ "$#" -gt 0 ]; do
                  case "$1" in
                    --index=*)
                      ;;
                    *)
                      if [ -z "$filtered" ]; then
                        filtered="$1"
                      else
                        filtered="$filtered
$1"
                      fi
                      ;;
                  esac
                  shift
                done
                if [ -n "${ASM_TEST_QMD_LOG:-}" ]; then
                  printf '%s\n' "$filtered" >> "$ASM_TEST_QMD_LOG"
                fi
                set -- $filtered

                case "${1:-}" in
                  collection)
                    case "${2:-}" in
                      list)
                        if [ -n "${ASM_TEST_QMD_COLLECTION_LIST:-}" ]; then
                          printf '%s\n' "$ASM_TEST_QMD_COLLECTION_LIST"
                        fi
                        exit "${ASM_TEST_QMD_COLLECTION_LIST_EXIT:-0}"
                        ;;
                      remove)
                        exit "${ASM_TEST_QMD_COLLECTION_REMOVE_EXIT:-0}"
                        ;;
                      add)
                        exit "${ASM_TEST_QMD_COLLECTION_ADD_EXIT:-0}"
                        ;;
                    esac
                    ;;
                  ls)
                    count="${ASM_TEST_QMD_LS_COUNT:-0}"
                    i=0
                    while [ "$i" -lt "$count" ]; do
                      printf 'doc-%s\n' "$i"
                      i=$((i + 1))
                    done
                    exit 0
                    ;;
                  query)
                    if [ -n "${ASM_TEST_QMD_QUERY_JSON:-}" ]; then
                      printf '%s\n' "$ASM_TEST_QMD_QUERY_JSON"
                    else
                      printf '[]\n'
                    fi
                    exit "${ASM_TEST_QMD_QUERY_EXIT:-0}"
                    ;;
                  update)
                    exit "${ASM_TEST_QMD_UPDATE_EXIT:-0}"
                    ;;
                esac

                exit 1
                """
            ),
        )

    def _seed_logging_osascript(self, log_path: Path) -> None:
        write_executable(
            self.bin_dir / "osascript",
            textwrap.dedent(
                f"""\
                #!/bin/sh
                script=""
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "-e" ] && [ "$#" -ge 2 ]; then
                    script="$2"
                    shift 2
                    continue
                  fi
                  shift
                done

                kind="other"
                if printf '%s' "$script" | grep -Fq 'if application "iTerm2" is running then'; then
                  kind="running"
                  printf 'yes\\n'
                elif printf '%s' "$script" | grep -Fq 'return (count of windows) as string'; then
                  kind="window-count"
                  printf '1\\n'
                elif printf '%s' "$script" | grep -Fq 'return "ok"'; then
                  kind="ok"
                  printf 'ok\\n'
                elif printf '%s' "$script" | grep -Fq 'create window'; then
                  kind="window"
                  printf '201\\n'
                elif printf '%s' "$script" | grep -Fq 'create tab'; then
                  kind="tab"
                elif printf '%s' "$script" | grep -Fq 'split vertically'; then
                  kind="split-vertical"
                elif printf '%s' "$script" | grep -Fq 'split horizontally'; then
                  kind="split-horizontal"
                elif printf '%s' "$script" | grep -Fq 'set bounds of window id'; then
                  kind="bounds"
                elif printf '%s' "$script" | grep -Fq 'set name to targetTabTitle'; then
                  kind="title"
                fi

                if [ -n "${{ASM_TEST_OSASCRIPT_FAIL_KIND:-}}" ] && [ "$kind" = "$ASM_TEST_OSASCRIPT_FAIL_KIND" ]; then
                  printf 'kind=%s\\tcmd=%s\\tprofile=%s\\tdirection=%s\\twindow=%s\\tbounds=%s\\ttitle=%s\\tstatus=fail\\n' \
                    "$kind" \
                    "${{ASM_ITERM_CMD:-}}" \
                    "${{ASM_ITERM_PROFILE:-}}" \
                    "${{ASM_ITERM_DIRECTION:-}}" \
                    "${{ASM_ITERM_WINDOW_ID:-}}" \
                    "${{ASM_ITERM_BOUNDS:-}}" \
                    "${{ASM_ITERM_TAB_TITLE:-}}" >> "{log_path}"
                  exit 1
                fi

                printf 'kind=%s\\tcmd=%s\\tprofile=%s\\tdirection=%s\\twindow=%s\\tbounds=%s\\ttitle=%s\\n' \
                  "$kind" \
                  "${{ASM_ITERM_CMD:-}}" \
                  "${{ASM_ITERM_PROFILE:-}}" \
                  "${{ASM_ITERM_DIRECTION:-}}" \
                  "${{ASM_ITERM_WINDOW_ID:-}}" \
                  "${{ASM_ITERM_BOUNDS:-}}" \
                  "${{ASM_ITERM_TAB_TITLE:-}}" >> "{log_path}"

                exit 0
                """
            ),
        )

    def _seed_selecting_fzf(self, log_path: Path) -> None:
        write_executable(
            self.bin_dir / "fzf",
            textwrap.dedent(
                f"""\
                #!/bin/sh
                printf '%s\\n' "$*" > "{log_path}"
                awk 'NR > 3 && NF {{ print; exit }}'
                """
            ),
        )

    def _seed_jira_curl(self) -> None:
        write_executable(
            self.bin_dir / "curl",
            textwrap.dedent(
                """\
                #!/bin/sh
                if [ -n "${ASM_TEST_CURL_LOG:-}" ]; then
                  printf '%s\n' "$@" > "$ASM_TEST_CURL_LOG"
                fi
                url=""
                for arg in "$@"; do
                  url="$arg"
                done
                case "$url" in
                  */rest/api/3/search)
                    if [ -n "${ASM_TEST_CURL_SEARCH_RESPONSE+x}" ]; then
                      printf '%s\n' "$ASM_TEST_CURL_SEARCH_RESPONSE"
                    else
                      printf '%s\n' '{"issues":[]}'
                    fi
                    exit "${ASM_TEST_CURL_SEARCH_EXIT:-0}"
                    ;;
                  */rest/api/3/issue/*)
                    if [ -n "${ASM_TEST_CURL_ISSUE_RESPONSE+x}" ]; then
                      printf '%s\n' "$ASM_TEST_CURL_ISSUE_RESPONSE"
                    else
                      printf '%s\n' '{}'
                    fi
                    exit "${ASM_TEST_CURL_ISSUE_EXIT:-0}"
                    ;;
                esac
                exit 1
                """
            ),
        )

    def _seed_open_logger(self, log_path: Path) -> None:
        write_executable(
            self.bin_dir / "open",
            textwrap.dedent(
                f"""\
                #!/bin/sh
                printf '%s\n' "$@" > "{log_path}"
                if [ "${{1:-}}" = "-Ra" ] && {{ [ "${{2:-}}" = "iTerm" ] || [ "${{2:-}}" = "iTerm2" ]; }}; then
                  exit "${{ASM_TEST_OPEN_ITERM:-0}}"
                fi
                exit 0
                """
            ),
        )

    def _seed_tty_process_bins(self, tty: str, cwd: Path, child_command: str) -> None:
        tty_name = tty.replace("/dev/", "")
        write_executable(
            self.bin_dir / "ps",
            textwrap.dedent(
                f"""\
                #!/bin/sh
                if [ "${{1:-}}" = "-t" ] && [ "${{2:-}}" = "{tty_name}" ]; then
                  cat <<'EOF'
                  10 1 10 Ss -zsh
                  11 10 11 S+ {child_command}
                EOF
                  exit 0
                fi
                exit 1
                """
            ),
        )
        write_executable(
            self.bin_dir / "lsof",
            textwrap.dedent(
                f"""\
                #!/bin/sh
                if [ "${{1:-}}" = "-a" ] && [ "${{2:-}}" = "-p" ] && [ "${{3:-}}" = "11" ] && [ "${{4:-}}" = "-d" ] && [ "${{5:-}}" = "cwd" ] && [ "${{6:-}}" = "-Fn" ]; then
                  printf 'n%s\\n' "{cwd}"
                  exit 0
                fi
                if [ "${{1:-}}" = "-a" ] && [ "${{2:-}}" = "-p" ] && [ "${{3:-}}" = "10" ] && [ "${{4:-}}" = "-d" ] && [ "${{5:-}}" = "cwd" ] && [ "${{6:-}}" = "-Fn" ]; then
                  printf 'n%s\\n' "{cwd}"
                  exit 0
                fi
                exit 0
                """
            ),
        )

    def _seed_git_branch(self, repo_path: Path, branch: str) -> None:
        git_dir = repo_path / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        (git_dir / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")

    def _append_claude_usage(self, *, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> None:
        session_file = self.home / ".claude" / "projects" / "-Users-test" / "11111111-1111-4111-8111-111111111111.jsonl"
        with session_file.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "usage": {
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "cache_read_input_tokens": cache_read,
                                "cache_creation_input_tokens": cache_write,
                            },
                        },
                        "timestamp": "2026-04-02T08:02:00Z",
                        "sessionId": "11111111-1111-4111-8111-111111111111",
                        "cwd": str(self.home / "workspace"),
                    }
                )
                + "\n"
            )

    def run_asm(
        self,
        *args: str,
        check: bool = True,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = self.env.copy()
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [str(ASM_PATH), *args],
            text=True,
            capture_output=True,
            env=env,
            check=check,
        )

    def run_asm_with_tty(
        self,
        *args: str,
        env_overrides: dict[str, str] | None = None,
    ) -> tuple[str, int]:
        env = self.env.copy()
        if env_overrides:
            env.update(env_overrides)

        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            [str(ASM_PATH), *args],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            close_fds=True,
        )
        os.close(slave_fd)

        chunks: list[bytes] = []
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.2)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(master_fd, 8192)
            except OSError:
                break
            if not data:
                if proc.poll() is not None:
                    break
                continue
            chunks.append(data)

        proc.wait(timeout=10)
        os.close(master_fd)
        return b"".join(chunks).decode("utf-8", errors="ignore"), proc.returncode

    def _seed_fzf_script(self, script: str) -> None:
        write_executable(self.bin_dir / "fzf", script)

    def run_asm_tui(self, *inputs: str, env_overrides: dict[str, str] | None = None) -> tuple[str, int]:
        env = self.env.copy()
        env["NO_COLOR"] = "1"
        env["ASM_FORCE_REFRESH"] = "1"
        if env_overrides:
            env.update(env_overrides)

        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            [str(ASM_PATH)],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            close_fds=True,
        )
        os.close(slave_fd)

        chunks: list[bytes] = []

        def read_some(timeout: float = 0.5) -> None:
            while True:
                ready, _, _ = select.select([master_fd], [], [], timeout)
                if not ready:
                    return
                try:
                    data = os.read(master_fd, 8192)
                except OSError:
                    return
                if not data:
                    return
                chunks.append(data)
                timeout = 0.05

        deadline = time.time() + 5.0
        while time.time() < deadline:
            read_some(0.2)
            output = b"".join(chunks).decode("utf-8", errors="ignore")
            if "SESSIONS:" in output or "\u001b[?1049h" in output:
                break
        time.sleep(0.2)
        for value in inputs:
            os.write(master_fd, value.encode("utf-8"))
            read_some(0.8)

        proc.wait(timeout=10)
        read_some(0.2)
        os.close(master_fd)
        return b"".join(chunks).decode("utf-8", errors="ignore"), proc.returncode

    def run_zsh(self, script: str, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = self.env.copy()
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            ["zsh", "-lc", script],
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )

    def test_help_flag(self) -> None:
        cp = self.run_asm("--help")
        self.assertIn("asm - AI session manager", cp.stdout)
        self.assertIn("doctor", cp.stdout)
        self.assertIn("open [--list] [--print] <preset>", cp.stdout)
        self.assertIn("Examples: `asm resume 3`, `asm inspect 2`, `asm handoff 5`", cp.stdout)
        self.assertIn("jira [mine|view <N|ISSUE-KEY>|open <N|ISSUE-KEY>]", cp.stdout)

    def test_version_command_reads_version_file(self) -> None:
        cp = self.run_asm("version")
        self.assertEqual(cp.stdout.strip(), VERSION_PATH.read_text(encoding="utf-8").strip())

    def test_unknown_option_shows_usage(self) -> None:
        cp = self.run_asm("--bogus", check=False)
        self.assertEqual(cp.returncode, 1)
        self.assertIn("Usage:", cp.stderr)

    def test_query_subcommand_seeds_picker_with_query(self) -> None:
        fzf_log = self.root / "fzf.log"
        cmd_file = self.root / "query.cmd"
        self._seed_selecting_fzf(fzf_log)
        test_path = f"{self.bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin"

        cp = self.run_asm(
            "query",
            "regression suite",
            env_overrides={"ASM_CMD_FILE": str(cmd_file), "PATH": test_path},
        )
        self.assertEqual(cp.returncode, 0)
        self.assertIn("regression suite", fzf_log.read_text(encoding="utf-8"))
        command = cmd_file.read_text(encoding="utf-8")
        self.assertIn("command asm resume", command)
        self.assertIn("claude:11111111-1111-4111-8111-111111111111", command)

    def test_top_level_query_seeds_picker_with_query(self) -> None:
        fzf_log = self.root / "fzf.log"
        cmd_file = self.root / "query.cmd"
        self._seed_selecting_fzf(fzf_log)
        test_path = f"{self.bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin"

        cp = self.run_asm(
            "build",
            "regression",
            env_overrides={"ASM_CMD_FILE": str(cmd_file), "PATH": test_path},
        )
        self.assertEqual(cp.returncode, 0)
        self.assertIn("build regression", fzf_log.read_text(encoding="utf-8"))
        command = cmd_file.read_text(encoding="utf-8")
        self.assertIn("command asm resume", command)
        self.assertIn("claude:11111111-1111-4111-8111-111111111111", command)

    def test_query_prefers_lexical_match_without_qmd(self) -> None:
        fzf_log = self.root / "fzf.log"
        cmd_file = self.root / "query.cmd"
        self._seed_selecting_fzf(fzf_log)
        test_path = f"{self.bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin"
        self.run_asm("alias", "opencode:ses_old", "archive benchmark backlog")

        cp = self.run_asm(
            "query",
            "archive",
            env_overrides={"ASM_CMD_FILE": str(cmd_file), "PATH": test_path},
        )
        self.assertEqual(cp.returncode, 0)
        self.assertIn("archive", fzf_log.read_text(encoding="utf-8"))
        command = cmd_file.read_text(encoding="utf-8")
        self.assertIn("command asm resume", command)
        self.assertIn("opencode:ses_old", command)

    def test_query_uses_qmd_rank_as_semantic_signal(self) -> None:
        fzf_log = self.root / "fzf.log"
        cmd_file = self.root / "query.cmd"
        self._seed_selecting_fzf(fzf_log)
        self._seed_qmd_bin()

        cp = self.run_asm(
            "query",
            "semantic handoff",
            env_overrides={
                "ASM_CMD_FILE": str(cmd_file),
                "ASM_TEST_QMD_COLLECTION_LIST": "asm_sessions_v1 (qmd://collection/asm_sessions_v1)",
                "ASM_TEST_QMD_QUERY_JSON": json.dumps([{"file": "/tmp/opencode___ses_old.md"}]),
            },
        )
        self.assertEqual(cp.returncode, 0)
        self.assertIn("semantic handoff", fzf_log.read_text(encoding="utf-8"))
        command = cmd_file.read_text(encoding="utf-8")
        self.assertIn("command asm resume", command)
        self.assertIn("opencode:ses_old", command)

    def test_jira_mine_lists_assigned_issues_and_caches_results(self) -> None:
        curl_log = self.root / "curl.log"
        self._seed_jira_curl()
        search_response = json.dumps(
            {
                "issues": [
                    {
                        "key": "ASM-101",
                        "fields": {
                            "summary": "Fix restore failure handling",
                            "status": {"name": "In Progress"},
                            "priority": {"name": "High"},
                            "assignee": {"displayName": "Example User"},
                            "updated": "2026-04-16T01:23:45.000+0000",
                            "issuetype": {"name": "Task"},
                        },
                    },
                    {
                        "key": "ASM-102",
                        "fields": {
                            "summary": "Add jira mine command",
                            "status": {"name": "To Do"},
                            "priority": {"name": "Medium"},
                            "assignee": {"displayName": "Example User"},
                            "updated": "2026-04-15T08:00:00.000+0000",
                            "issuetype": {"name": "Story"},
                        },
                    },
                ]
            },
            ensure_ascii=False,
        )
        cp = self.run_asm(
            "jira",
            "mine",
            env_overrides={
                "ASM_JIRA_SITE": "https://example.atlassian.net",
                "ASM_JIRA_EMAIL": "you@example.com",
                "ASM_JIRA_API_TOKEN": "token",
                "ASM_TEST_CURL_SEARCH_RESPONSE": search_response,
                "ASM_TEST_CURL_LOG": str(curl_log),
            },
        )
        self.assertIn("Jira mine", cp.stdout)
        self.assertIn("1. ASM-101", cp.stdout)
        self.assertIn("2. ASM-102", cp.stdout)
        self.assertIn("view with: asm jira view N | open with: asm jira open N", cp.stdout)
        self.assertIn("currentUser()", curl_log.read_text(encoding="utf-8"))

        jira_cache = self.home / ".local" / "share" / "asm" / "jira-last.json"
        payload = json.loads(jira_cache.read_text(encoding="utf-8"))
        self.assertEqual(payload[0]["key"], "ASM-101")
        self.assertEqual(payload[1]["key"], "ASM-102")

    def test_jira_config_set_show_and_clear(self) -> None:
        cp = self.run_asm(
            "jira",
            "config",
            "set",
            "--site",
            "example.atlassian.net",
            "--email",
            "you@example.com",
            "--token",
            "example-token",
            "--max-issues",
            "7",
        )
        self.assertIn("saved jira config:", cp.stdout)

        config_file = self.home / ".local" / "share" / "asm" / "jira-config.json"
        payload = json.loads(config_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["site"], "https://example.atlassian.net")
        self.assertEqual(payload["email"], "you@example.com")
        self.assertEqual(payload["api_token"], "example-token")
        self.assertEqual(payload["max_issues"], "7")

        cp = self.run_asm("jira", "config", "show")
        self.assertIn("Jira config", cp.stdout)
        self.assertIn("site: https://example.atlassian.net", cp.stdout)
        self.assertIn("email: you@example.com", cp.stdout)
        self.assertIn("api token: [set]", cp.stdout)
        self.assertIn("max issues: 7", cp.stdout)

        cp = self.run_asm("jira", "config", "clear")
        self.assertIn("cleared jira config:", cp.stdout)
        self.assertFalse(config_file.exists())

    def test_jira_mine_uses_saved_config_file_without_env(self) -> None:
        self._seed_jira_curl()
        search_response = json.dumps(
            {
                "issues": [
                    {
                        "key": "ASM-101",
                        "fields": {
                            "summary": "Fix restore failure handling",
                            "status": {"name": "In Progress"},
                            "priority": {"name": "High"},
                            "assignee": {"displayName": "Example User"},
                            "updated": "2026-04-16T01:23:45.000+0000",
                            "issuetype": {"name": "Task"},
                        },
                    }
                ]
            },
            ensure_ascii=False,
        )
        self.run_asm(
            "jira",
            "config",
            "set",
            "--site",
            "example.atlassian.net",
            "--email",
            "you@example.com",
            "--token",
            "example-token",
        )
        cp = self.run_asm(
            "jira",
            "mine",
            env_overrides={
                "ASM_TEST_CURL_SEARCH_RESPONSE": search_response,
            },
        )
        self.assertIn("1. ASM-101", cp.stdout)

    def test_jira_view_accepts_cached_number_selector(self) -> None:
        self._seed_jira_curl()
        search_response = json.dumps(
            {
                "issues": [
                    {
                        "key": "ASM-101",
                        "fields": {
                            "summary": "Fix restore failure handling",
                            "status": {"name": "In Progress"},
                            "priority": {"name": "High"},
                            "assignee": {"displayName": "Example User"},
                            "updated": "2026-04-16T01:23:45.000+0000",
                            "issuetype": {"name": "Task"},
                        },
                    },
                    {
                        "key": "ASM-102",
                        "fields": {
                            "summary": "Add jira mine command",
                            "status": {"name": "To Do"},
                            "priority": {"name": "Medium"},
                            "assignee": {"displayName": "Example User"},
                            "updated": "2026-04-15T08:00:00.000+0000",
                            "issuetype": {"name": "Story"},
                            "parent": {
                                "key": "ASM-100",
                                "fields": {"summary": "asm improvements"},
                            },
                        },
                    },
                ]
            },
            ensure_ascii=False,
        )
        issue_response = json.dumps(
            {
                "key": "ASM-102",
                "fields": {
                    "summary": "Add jira mine command",
                    "status": {"name": "To Do"},
                    "priority": {"name": "Medium"},
                    "assignee": {"displayName": "Example User"},
                    "updated": "2026-04-15T08:00:00.000+0000",
                    "issuetype": {"name": "Story"},
                    "parent": {
                        "key": "ASM-100",
                        "fields": {"summary": "asm improvements"},
                    },
                },
            },
            ensure_ascii=False,
        )
        common_env = {
            "ASM_JIRA_SITE": "https://example.atlassian.net",
            "ASM_JIRA_EMAIL": "you@example.com",
            "ASM_JIRA_API_TOKEN": "token",
            "ASM_TEST_CURL_SEARCH_RESPONSE": search_response,
            "ASM_TEST_CURL_ISSUE_RESPONSE": issue_response,
        }
        self.run_asm("jira", "mine", env_overrides=common_env)
        cp = self.run_asm("jira", "view", "2", env_overrides=common_env)
        self.assertIn("# ASM-102 Add jira mine command", cp.stdout)
        self.assertIn("- Status: To Do", cp.stdout)
        self.assertIn("- Parent: ASM-100 asm improvements", cp.stdout)
        self.assertIn("- URL: https://example.atlassian.net/browse/ASM-102", cp.stdout)

    def test_jira_open_accepts_cached_number_selector(self) -> None:
        open_log = self.root / "open.log"
        self._seed_jira_curl()
        self._seed_open_logger(open_log)
        search_response = json.dumps(
            {
                "issues": [
                    {
                        "key": "ASM-101",
                        "fields": {
                            "summary": "Fix restore failure handling",
                            "status": {"name": "In Progress"},
                            "priority": {"name": "High"},
                            "assignee": {"displayName": "Example User"},
                            "updated": "2026-04-16T01:23:45.000+0000",
                            "issuetype": {"name": "Task"},
                        },
                    }
                ]
            },
            ensure_ascii=False,
        )
        common_env = {
            "ASM_JIRA_SITE": "https://example.atlassian.net",
            "ASM_JIRA_EMAIL": "you@example.com",
            "ASM_JIRA_API_TOKEN": "token",
            "ASM_TEST_CURL_SEARCH_RESPONSE": search_response,
        }
        self.run_asm("jira", "mine", env_overrides=common_env)
        cp = self.run_asm("jira", "open", "1", env_overrides=common_env)
        self.assertIn("https://example.atlassian.net/browse/ASM-101", cp.stdout)
        self.assertEqual(
            open_log.read_text(encoding="utf-8").strip(),
            "https://example.atlassian.net/browse/ASM-101",
        )

    def test_doctor_reports_shell_mode(self) -> None:
        cp = self.run_asm("doctor")
        self.assertIn("shell integration: inactive", cp.stdout)
        self.assertIn("asm version:", cp.stdout)
        self.assertIn("cache ttl:", cp.stdout)

    def test_install_script_installs_wrapper_and_libexec(self) -> None:
        prefix = self.root / "prefix"
        install_script = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"
        env = self.env.copy()
        env["PREFIX"] = str(prefix)
        env["PATH"] = f"{self.bin_dir}:{env.get('PATH', '')}"

        subprocess.run(
            ["bash", str(install_script)],
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )

        installed_asm = prefix / "bin" / "asm"
        installed_libexec = prefix / "libexec" / "asm-cli"
        self.assertTrue(installed_asm.exists())
        self.assertTrue((installed_libexec / "asm").exists())
        self.assertTrue((installed_libexec / "asm_runtime.py").exists())
        self.assertTrue((installed_libexec / "asm_store.py").exists())
        self.assertTrue((installed_libexec / "asm_inspect.py").exists())
        self.assertTrue((installed_libexec / "VERSION").exists())

        cp = subprocess.run(
            [str(installed_asm), "version"],
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
        self.assertEqual(cp.stdout.strip(), VERSION_PATH.read_text(encoding="utf-8").strip())

    def test_doctor_uses_iterm_process_path_fallback(self) -> None:
        cp = self.run_asm(
            "doctor",
            env_overrides={
                "ASM_TEST_UNAME_S": "Darwin",
                "ASM_TEST_UNAME_M": "arm64",
                "ASM_TEST_OPEN_ITERM": "1",
                "ASM_TEST_PGREP_MODE": "path",
            },
        )
        self.assertIn("iTerm2 installed: true", cp.stdout)
        self.assertIn("iTerm2 running: true", cp.stdout)

    def test_doctor_uses_osascript_running_fallback(self) -> None:
        cp = self.run_asm(
            "doctor",
            env_overrides={
                "ASM_TEST_UNAME_S": "Darwin",
                "ASM_TEST_UNAME_M": "arm64",
                "ASM_TEST_OPEN_ITERM": "1",
                "ASM_TEST_PGREP_MODE": "none",
                "ASM_TEST_OSASCRIPT_RUNNING_RESULT": "yes",
                "ASM_TEST_OSASCRIPT_WINDOW_COUNT_RESULT": "2",
                "ASM_TEST_OSASCRIPT_OK_RESULT": "ok",
            },
        )
        self.assertIn("iTerm2 running: true", cp.stdout)
        self.assertIn("iTerm2 windows: 2", cp.stdout)
        self.assertIn("AppleScript to iTerm2: true", cp.stdout)

    def test_doctor_reports_iterm_not_running_when_probes_fail(self) -> None:
        cp = self.run_asm(
            "doctor",
            env_overrides={
                "ASM_TEST_UNAME_S": "Darwin",
                "ASM_TEST_UNAME_M": "arm64",
                "ASM_TEST_OPEN_ITERM": "1",
                "ASM_TEST_PGREP_MODE": "none",
                "ASM_TEST_OSASCRIPT_RUNNING_RESULT": "no",
            },
        )
        self.assertIn("iTerm2 installed: true", cp.stdout)
        self.assertIn("iTerm2 running: false", cp.stdout)

    def test_list_prefers_recent_claude_over_old_opencode(self) -> None:
        cp = self.run_asm("list")
        lines = [line for line in cp.stdout.strip().splitlines() if line.strip() and not line.startswith("──")]
        first = lines[0]
        self.assertNotIn("OPN", first)
        self.assertTrue("CLD" in first or "CDX" in first)

    def test_list_outputs_visible_row_numbers(self) -> None:
        cp = self.run_asm("list", "-n", "2")
        session_lines = [
            line
            for line in cp.stdout.strip().splitlines()
            if line.strip() and not line.startswith("──") and ("CLD" in line or "CDX" in line or "OPN" in line)
        ]
        self.assertGreaterEqual(len(session_lines), 2)
        self.assertRegex(session_lines[0], r"^\s*1\.")
        self.assertRegex(session_lines[1], r"^\s*2\.")
        self.assertIn("open with: asm resume N | asm inspect N | asm handoff N", cp.stdout)

    def test_resume_without_wrapper_runs_child_shell_command_when_tty_is_present(self) -> None:
        output, code = self.run_asm_with_tty("resume", "claude:11111111-1111-4111-8111-111111111111")
        self.assertEqual(code, 0, output)
        log = (self.logs_dir / "claude.log").read_text(encoding="utf-8")
        self.assertIn("--resume 11111111-1111-4111-8111-111111111111", log)
        self.assertIn(f"cwd={self.home / 'workspace'}", log)

    def test_resume_without_wrapper_prints_command_when_tty_is_missing(self) -> None:
        cp = self.run_asm("resume", "claude:11111111-1111-4111-8111-111111111111")
        self.assertIn(
            f"cd {self.home / 'workspace'} && claude --dangerously-skip-permissions --resume 11111111-1111-4111-8111-111111111111",
            cp.stdout,
        )
        self.assertIn("interactive command requires a terminal", cp.stderr)
        self.assertFalse((self.logs_dir / "claude.log").exists())

    def test_resume_rejects_unknown_ref(self) -> None:
        cp = self.run_asm("resume", "claude:not-real", check=False)
        self.assertEqual(cp.returncode, 1)
        self.assertIn("session not found: claude:not-real", cp.stderr)

    def test_resume_accepts_last_list_number(self) -> None:
        self.run_asm("list", "-n", "2")
        last_list = (self.home / ".local" / "share" / "asm" / "last-list-refs.txt").read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(last_list), 1)
        _, session_id = last_list[0].split(":", 1)

        cp = self.run_asm("resume", "1")
        self.assertIn(session_id, cp.stdout)

    def test_resume_rejects_out_of_range_list_number(self) -> None:
        self.run_asm("list", "-n", "1")
        cp = self.run_asm("resume", "2", check=False)
        self.assertEqual(cp.returncode, 1)
        self.assertIn("list selection out of range: 2", cp.stderr)

    def test_codex_lookup_is_exact(self) -> None:
        output, code = self.run_asm_with_tty("resume", "codex:019d470b-a125-7533-ad4e-dc1d1219a9df")
        self.assertEqual(code, 0, output)
        log = (self.logs_dir / "codex.log").read_text(encoding="utf-8")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", log)
        self.assertIn("resume 019d470b-a125-7533-ad4e-dc1d1219a9df", log)
        self.assertIn(f"cwd={self.home / 'codex-space'}", log)
        self.assertNotIn("wrong-space", log)

    def test_shell_integration_replays_nested_commands(self) -> None:
        driver = self.bin_dir / "asm"
        driver.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                if [ "${1:-}" = "first" ]; then
                  printf '%s\\n' 'printf "%s\\n" "second" > "$ASM_CHAIN_LOG"' > "$ASM_CMD_FILE"
                  exit 0
                fi
                exit 0
                """
            ),
            encoding="utf-8",
        )
        driver.chmod(driver.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        chain_log = self.root / "chain.log"
        init_snippet = self.run_asm("init", "zsh").stdout
        script = textwrap.dedent(
            """\
            eval "$(cat <<'EOF'
            {init_snippet}
            EOF
            )"
            asm first
            """
        )
        cp = self.run_zsh(
            script.format(init_snippet=init_snippet.rstrip()),
            env_overrides={"ASM_CHAIN_LOG": str(chain_log)},
        )
        self.assertEqual(cp.returncode, 0)
        self.assertEqual(chain_log.read_text(encoding="utf-8").strip(), "second")

    def test_list_includes_recent_unindexed_codex_session(self) -> None:
        sessions_dir = self.home / ".codex" / "sessions" / "2026" / "04" / "08"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        session_id = "019d6d7a-4cd0-79e0-b0ff-fa03a7b99af6"
        workspace = self.home / "fresh-codex-space"
        workspace.mkdir(exist_ok=True)
        session_file = sessions_dir / f"rollout-2026-04-08T23-23-44-{session_id}.jsonl"
        session_file.write_text(
            "\n".join(
                [
                    json.dumps({"type": "session_meta", "payload": {"cwd": str(workspace)}}),
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": "# AGENTS.md instructions for /tmp/home <INSTRUCTIONS> bootstrap",
                                    }
                                ],
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": "why is asm missing my latest codex session"}
                                ],
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        fresh_time = time.time() + 60
        os.utime(session_file, (fresh_time, fresh_time))

        self.run_asm("list")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
        record = next((row for row in rows if row["ref"] == f"codex:{session_id}"), None)

        self.assertIsNotNone(record)
        self.assertEqual(record["cwd"], str(workspace))
        self.assertEqual(record["title"], "why is asm missing my latest codex session")

    def test_render_lines_filters_by_active_tab(self) -> None:
        cache_file = self.root / "render-cache.json"
        cache_file.write_text(
            json.dumps(
                [
                    {
                        "ref": "claude:11111111-1111-4111-8111-111111111111",
                        "agent": "claude",
                        "id": "11111111-1111-4111-8111-111111111111",
                        "title": "review patch",
                        "cwd": str(self.home / "workspace"),
                        "updated_epoch": 1712000000,
                        "updated_display": "2026-04-02T08:00:00Z",
                        "tab_name": "review",
                        "tags_text": "#hot",
                    },
                    {
                        "ref": "codex:019d470b-a125-7533-ad4e-dc1d1219a9df",
                        "agent": "codex",
                        "id": "019d470b-a125-7533-ad4e-dc1d1219a9df",
                        "title": "wrong tab",
                        "cwd": str(self.home / "workspace"),
                        "updated_epoch": 1712000100,
                        "updated_display": "2026-04-02T08:01:40Z",
                        "tab_name": "other",
                    },
                ]
            ),
            encoding="utf-8",
        )
        runtime = Path(__file__).resolve().parents[1] / "bin" / "asm_runtime.py"
        cp = subprocess.run(
            [sys.executable, str(runtime), "render-lines", str(cache_file), "review", "1712000200"],
            text=True,
            capture_output=True,
            check=True,
        )
        refs = [line.split("\t", 1)[0] for line in cp.stdout.splitlines() if line.strip()]
        self.assertEqual(refs, ["claude:11111111-1111-4111-8111-111111111111"])
        self.assertIn("#hot", cp.stdout)
        self.assertNotIn("codex:019d470b-a125-7533-ad4e-dc1d1219a9df", cp.stdout)

    def test_render_lines_exits_cleanly_when_pipe_closes(self) -> None:
        cache_file = self.root / "cache.json"
        cache_file.write_text(
            json.dumps(
                [
                    {
                        "ref": f"codex:session-{index}",
                        "agent": "codex",
                        "id": f"session-{index}",
                        "title": f"render row {index}",
                        "cwd": str(self.home / "workspace"),
                        "updated_epoch": 1712000000 + index,
                        "tab_name": "",
                    }
                    for index in range(200)
                ]
            ),
            encoding="utf-8",
        )
        runtime = Path(__file__).resolve().parents[1] / "bin" / "asm_runtime.py"
        cp = subprocess.run(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(runtime))} render-lines {shlex.quote(str(cache_file))} All 1712000200 | head -n 1 >/dev/null",
            shell=True,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(cp.returncode, 0)
        self.assertNotIn("BrokenPipeError", cp.stderr)

    def test_export_contains_recent_excerpt_and_reopen_command(self) -> None:
        cp = self.run_asm("export", "claude:11111111-1111-4111-8111-111111111111")
        export_path = Path(cp.stdout.strip())
        text = export_path.read_text(encoding="utf-8")
        self.assertIn("Recent Transcript Excerpt", text)
        self.assertIn("build a regression suite", text)
        self.assertIn("claude --dangerously-skip-permissions --resume 11111111-1111-4111-8111-111111111111", text)

    def test_inspect_falls_back_without_helper(self) -> None:
        standalone = self.root / "standalone"
        standalone.mkdir(parents=True, exist_ok=True)
        asm_copy = standalone / "asm"
        runtime_copy = standalone / "asm_runtime.py"
        store_copy = standalone / "asm_store.py"
        asm_copy.write_text(ASM_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        asm_copy.chmod(asm_copy.stat().st_mode | stat.S_IXUSR)
        runtime_copy.write_text(
            (Path(__file__).resolve().parents[1] / "bin" / "asm_runtime.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        store_copy.write_text(
            (Path(__file__).resolve().parents[1] / "bin" / "asm_store.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        write_executable(
            self.bin_dir / "less",
            textwrap.dedent(
                """\
                #!/bin/sh
                while [ "$#" -gt 0 ]; do
                  case "$1" in
                    -*) shift ;;
                    *) break ;;
                  esac
                done
                cat "$@"
                """
            ),
        )
        env = self.env.copy()
        env["PATH"] = f"{self.bin_dir}:{env.get('PATH', '')}"
        cp = subprocess.run(
            [str(asm_copy), "inspect", "claude:11111111-1111-4111-8111-111111111111"],
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
        text = cp.stdout
        self.assertIn("# Session Inspect", text)
        self.assertIn("## Commands", text)
        self.assertIn("claude --dangerously-skip-permissions --resume 11111111-1111-4111-8111-111111111111", text)
        self.assertIn("## Recent Transcript", text)
        self.assertIn("build a regression suite", text)

    def test_store_helper_initializes_schema_version_and_tables(self) -> None:
        store_path = Path(__file__).resolve().parents[1] / "bin" / "asm_store.py"
        db_path = self.home / ".local" / "share" / "asm" / "meta.sqlite"

        subprocess.run(
            [sys.executable, str(store_path), "ensure-schema", str(db_path)],
            text=True,
            capture_output=True,
            check=True,
        )

        db = sqlite3.connect(db_path)
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        }
        db.execute("INSERT INTO app_state (key, value) VALUES ('active_tab', 'review')")
        db.commit()
        db.close()

        self.assertEqual(version, 2)
        self.assertTrue(
            {
                "session_meta",
                "tabs",
                "tab_sessions",
                "session_tags",
                "app_state",
                "session_relations",
            }.issubset(tables)
        )

        subprocess.run(
            [sys.executable, str(store_path), "ensure-schema", str(db_path)],
            text=True,
            capture_output=True,
            check=True,
        )
        db = sqlite3.connect(db_path)
        version_after = db.execute("PRAGMA user_version").fetchone()[0]
        active_tab = db.execute("SELECT value FROM app_state WHERE key='active_tab'").fetchone()[0]
        db.close()

        self.assertEqual(version_after, 2)
        self.assertEqual(active_tab, "review")

    def test_store_helper_rejects_invalid_ref_without_traceback(self) -> None:
        store_path = Path(__file__).resolve().parents[1] / "bin" / "asm_store.py"
        db_path = self.home / ".local" / "share" / "asm" / "meta.sqlite"

        cp = subprocess.run(
            [sys.executable, str(store_path), "update-last-opened", str(db_path), "c"],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(cp.returncode, 2)
        self.assertIn("invalid ref: c", cp.stderr)
        self.assertNotIn("Traceback", cp.stderr)

    def test_index_status_reports_qmd_sync_details(self) -> None:
        self._seed_qmd_bin()
        qmd_docs = self.home / ".local" / "share" / "asm" / "qmd-docs"
        qmd_docs.mkdir(parents=True, exist_ok=True)
        (qmd_docs / "claude___11111111-1111-4111-8111-111111111111.md").write_text("claude\n", encoding="utf-8")
        (qmd_docs / "codex___019d470b-a125-7533-ad4e-dc1d1219a9df.md").write_text("codex\n", encoding="utf-8")
        stamp = self.home / ".local" / "share" / "asm" / "qmd-sync.sha256"

        collection_list = "asm_sessions_v1 (qmd://collection/asm_sessions_v1)"
        ls_count = "2"
        cp = self.run_asm(
            "index",
            "status",
            env_overrides={
                "ASM_TEST_QMD_COLLECTION_LIST": collection_list,
                "ASM_TEST_QMD_LS_COUNT": ls_count,
            },
        )
        self.assertIn("qmd available: true", cp.stdout)
        self.assertIn("qmd collection: asm_sessions_v1 (true)", cp.stdout)
        self.assertIn("qmd docs: ", cp.stdout)
        self.assertIn("qmd collection files: 2", cp.stdout)
        self.assertIn("qmd sync stamp: -", cp.stdout)
        self.assertIn("qmd stale: true", cp.stdout)
        signature = next(
            line.split(": ", 1)[1]
            for line in cp.stdout.splitlines()
            if line.startswith("qmd current signature: ")
        )
        self.assertRegex(signature, r"^[0-9a-f]{64}$")

        stamp.write_text("feedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedface", encoding="utf-8")
        cp = self.run_asm(
            "index",
            "status",
            env_overrides={
                "ASM_TEST_QMD_COLLECTION_LIST": collection_list,
                "ASM_TEST_QMD_LS_COUNT": ls_count,
            },
        )
        self.assertIn("qmd sync stamp: feedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedface", cp.stdout)
        self.assertIn(f"qmd current signature: {signature}", cp.stdout)
        self.assertIn("qmd stale: true", cp.stdout)

        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
        for row in rows:
            (qmd_docs / f"{row['ref'].replace(':', '___')}.md").write_text(f"{row['ref']}\n", encoding="utf-8")

        synced_count = str(len(rows))
        stamp.write_text(signature, encoding="utf-8")
        cp = self.run_asm(
            "index",
            "status",
            env_overrides={
                "ASM_TEST_QMD_COLLECTION_LIST": collection_list,
                "ASM_TEST_QMD_LS_COUNT": synced_count,
            },
        )
        self.assertIn(f"qmd expected docs: {synced_count}", cp.stdout)
        self.assertIn(f"qmd collection files: {synced_count}", cp.stdout)
        self.assertIn(f"qmd sync stamp: {signature}", cp.stdout)
        self.assertIn(f"qmd current signature: {signature}", cp.stdout)
        self.assertIn("qmd stale: false", cp.stdout)

    def test_index_update_does_not_remove_existing_qmd_collection(self) -> None:
        self._seed_qmd_bin()
        qmd_log = self.root / "qmd.log"
        cp = self.run_asm(
            "index",
            "update",
            env_overrides={
                "ASM_TEST_QMD_COLLECTION_LIST": "asm_sessions_v1 (qmd://collection/asm_sessions_v1)",
                "ASM_TEST_QMD_LOG": str(qmd_log),
            },
        )
        self.assertEqual(cp.returncode, 0)
        log = qmd_log.read_text(encoding="utf-8")
        self.assertIn("collection\nlist", log)
        self.assertIn("update", log)
        self.assertNotIn("collection\nremove", log)
        self.assertNotIn("collection\nadd", log)

    def test_runtime_import_iterm_tabs_ignores_codex_resume_flags(self) -> None:
        runtime_path = Path(__file__).resolve().parents[1] / "bin" / "asm_runtime.py"
        spec = importlib.util.spec_from_file_location("asm_runtime_test", runtime_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        fake_stdout = "<<<ASMROW>>>".join(
            [
                "1<<<ASMSEP>>>codex --dangerously-bypass-approvals-and-sandbox resume --last",
                "2<<<ASMSEP>>>codex --dangerously-bypass-approvals-and-sandbox resume 019d470b-a125-7533-ad4e-dc1d1219a9df",
            ]
        )

        class FakeCompleted:
            def __init__(self, stdout: str):
                self.stdout = stdout
                self.returncode = 0

        original_run = module.subprocess.run
        module.subprocess.run = lambda *args, **kwargs: FakeCompleted(fake_stdout)
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = module.cmd_import_iterm_tabs(0.1)
        finally:
            module.subprocess.run = original_run

        self.assertEqual(rc, 0)
        text = output.getvalue()
        self.assertNotIn("codex:--last", text)
        self.assertIn("iterm-2\tcodex:019d470b-a125-7533-ad4e-dc1d1219a9df", text)

    def test_runtime_read_git_info_detects_worktree(self) -> None:
        runtime_path = Path(__file__).resolve().parents[1] / "bin" / "asm_runtime.py"
        spec = importlib.util.spec_from_file_location("asm_runtime_test", runtime_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        main_repo = self.root / "main-repo"
        worktree = self.root / "worktree-repo"
        worktree_git_dir = main_repo / ".git" / "worktrees" / "worktree-repo"
        worktree_git_dir.mkdir(parents=True, exist_ok=True)
        (worktree_git_dir / "HEAD").write_text("ref: refs/heads/feature/worktree\n", encoding="utf-8")
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {worktree_git_dir}\n", encoding="utf-8")

        branch, is_worktree = module.read_git_info(str(worktree))
        self.assertEqual(branch, "feature/worktree")
        self.assertEqual(is_worktree, 1)

    def test_list_cache_enriches_git_and_active_fields(self) -> None:
        workspace = self.home / "workspace"
        self._seed_git_branch(workspace, "feature/live-preview")
        tty = "/dev/ttys001"
        self._seed_tty_process_bins(tty, workspace, "claude --resume 11111111-1111-4111-8111-111111111111")
        raw_scan = f"S<<<ASMSEP>>>1<<<ASMSEP>>>2<<<ASMSEP>>>1<<<ASMSEP>>>{tty}"

        cp = self.run_asm(
            "list",
            env_overrides={
                "ASM_TEST_UNAME_S": "Darwin",
                "ASM_TEST_UNAME_M": "arm64",
                "ASM_TEST_OPEN_ITERM": "1",
                "ASM_TEST_PGREP_MODE": "none",
                "ASM_TEST_OSASCRIPT_RUNNING_RESULT": "yes",
                "ASM_TEST_OSASCRIPT_WINDOW_COUNT_RESULT": "1",
                "ASM_TEST_OSASCRIPT_OK_RESULT": "ok",
                "ASM_TEST_OSASCRIPT_DEFAULT_RESULT": raw_scan,
                "NO_COLOR": "1",
            },
        )
        self.assertIn("1:2", cp.stdout)

        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
        rec = next(row for row in rows if row["ref"] == "claude:11111111-1111-4111-8111-111111111111")
        self.assertEqual(rec["git_branch"], "feature/live-preview")
        self.assertEqual(rec["git_is_worktree"], 0)
        self.assertEqual(rec["is_active"], 1)
        self.assertEqual(rec["active_window"], 1)
        self.assertEqual(rec["active_tab"], 2)

    def test_preview_prefers_live_output_and_shows_claude_usage(self) -> None:
        workspace = self.home / "workspace"
        self._seed_git_branch(workspace, "feature/live-preview")
        self._append_claude_usage(input_tokens=120, output_tokens=45, cache_read=20, cache_write=10)
        tty = "/dev/ttys001"
        self._seed_tty_process_bins(tty, workspace, "claude --resume 11111111-1111-4111-8111-111111111111")
        raw_scan = f"S<<<ASMSEP>>>1<<<ASMSEP>>>1<<<ASMSEP>>>1<<<ASMSEP>>>{tty}"

        self.run_asm(
            "list",
            env_overrides={
                "ASM_TEST_UNAME_S": "Darwin",
                "ASM_TEST_UNAME_M": "arm64",
                "ASM_TEST_OPEN_ITERM": "1",
                "ASM_TEST_PGREP_MODE": "none",
                "ASM_TEST_OSASCRIPT_RUNNING_RESULT": "yes",
                "ASM_TEST_OSASCRIPT_WINDOW_COUNT_RESULT": "1",
                "ASM_TEST_OSASCRIPT_OK_RESULT": "ok",
                "ASM_TEST_OSASCRIPT_DEFAULT_RESULT": raw_scan,
            },
        )
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"

        cp = self.run_asm(
            "__preview",
            "claude:11111111-1111-4111-8111-111111111111",
            env_overrides={
                "ASM_CACHE_FILE": str(cache_file),
                "ASM_TEST_OPEN_ITERM": "1",
                "ASM_TEST_PGREP_MODE": "none",
                "ASM_TEST_OSASCRIPT_RUNNING_RESULT": "yes",
                "ASM_TEST_OSASCRIPT_DEFAULT_RESULT": "live line 1\nlive line 2\n",
            },
        )
        self.assertIn("Live Output:", cp.stdout)
        self.assertIn("live line 1", cp.stdout)
        self.assertIn("Usage:", cp.stdout)
        self.assertIn("120 in / 45 out", cp.stdout)
        self.assertIn("Preview:      overview", cp.stdout)

    def test_status_supports_compact_and_json_output(self) -> None:
        workspace = self.home / "workspace"
        self._seed_tty_process_bins("/dev/ttys001", workspace, "claude --resume 11111111-1111-4111-8111-111111111111")
        raw_scan = "S<<<ASMSEP>>>1<<<ASMSEP>>>1<<<ASMSEP>>>1<<<ASMSEP>>>/dev/ttys001"
        overrides = {
            "ASM_TEST_UNAME_S": "Darwin",
            "ASM_TEST_UNAME_M": "arm64",
            "ASM_TEST_OPEN_ITERM": "1",
            "ASM_TEST_PGREP_MODE": "none",
            "ASM_TEST_OSASCRIPT_RUNNING_RESULT": "yes",
            "ASM_TEST_OSASCRIPT_WINDOW_COUNT_RESULT": "1",
            "ASM_TEST_OSASCRIPT_OK_RESULT": "ok",
            "ASM_TEST_OSASCRIPT_DEFAULT_RESULT": raw_scan,
        }

        compact = self.run_asm("status", "--compact", env_overrides=overrides)
        self.assertEqual(compact.stdout.strip(), "CLD@1:1")

        json_cp = self.run_asm("status", "--json", env_overrides=overrides)
        payload = json.loads(json_cp.stdout)
        self.assertEqual(payload[0]["ref"], "claude:11111111-1111-4111-8111-111111111111")
        self.assertEqual(payload[0]["window"], 1)
        self.assertEqual(payload[0]["tab"], 1)

    def test_iterm_save_preserves_codex_resume_flag_command_without_fake_session_id(self) -> None:
        tty = "/dev/ttys001"
        cwd = self.home / "workspace"
        cwd.mkdir(exist_ok=True)
        self._seed_tty_process_bins(tty, cwd, "codex resume --last")
        layouts_dir = self.home / ".local" / "share" / "asm" / "iterm-layouts"
        layouts_dir.mkdir(parents=True, exist_ok=True)
        (layouts_dir / ".autosave-stamp").write_text(str(int(time.time())), encoding="utf-8")

        raw_snapshot = "\n".join(
            [
                "W<<<ASMSEP>>>1<<<ASMSEP>>>10<<<ASMSEP>>>0,0,100,100<<<ASMSEP>>>1<<<ASMSEP>>>1",
                "T<<<ASMSEP>>>1<<<ASMSEP>>>1<<<ASMSEP>>>Current",
                f"S<<<ASMSEP>>>1<<<ASMSEP>>>1<<<ASMSEP>>>1<<<ASMSEP>>>{tty}<<<ASMSEP>>>Default<<<ASMSEP>>>Resume<<<ASMSEP>>>120<<<ASMSEP>>>40",
            ]
        )
        cp = self.run_asm(
            "iterm-save",
            "resume-last",
            env_overrides={
                "ASM_TEST_UNAME_S": "Darwin",
                "ASM_TEST_UNAME_M": "arm64",
                "ASM_TEST_OPEN_ITERM": "1",
                "ASM_TEST_PGREP_MODE": "none",
                "ASM_TEST_OSASCRIPT_RUNNING_RESULT": "yes",
                "ASM_TEST_OSASCRIPT_WINDOW_COUNT_RESULT": "1",
                "ASM_TEST_OSASCRIPT_OK_RESULT": "ok",
                "ASM_TEST_OSASCRIPT_DEFAULT_RESULT": raw_snapshot,
            },
        )
        snapshot_path = Path(cp.stdout.splitlines()[0].strip())
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        session = payload["windows"][0]["tabs"][0]["sessions"][0]
        self.assertEqual(session["agent"], "codex")
        self.assertEqual(session["session_id"], "")
        self.assertEqual(session["restore_kind"], "command")
        self.assertEqual(session["restore_command"], f"cd {cwd} && codex resume --last")

    def test_help_clears_stale_autosave_lock_and_writes_snapshot(self) -> None:
        layouts_dir = self.home / ".local" / "share" / "asm" / "iterm-layouts"
        layouts_dir.mkdir(parents=True, exist_ok=True)
        stale_lock = layouts_dir / ".autosave.lock"
        stale_lock.mkdir()
        old_time = time.time() - 120
        os.utime(stale_lock, (old_time, old_time))

        raw_snapshot = "W<<<ASMSEP>>>1<<<ASMSEP>>>10<<<ASMSEP>>>0,0,100,100<<<ASMSEP>>>1<<<ASMSEP>>>1"
        cp = self.run_asm(
            "help",
            env_overrides={
                "ASM_TEST_UNAME_S": "Darwin",
                "ASM_TEST_UNAME_M": "arm64",
                "ASM_TEST_OPEN_ITERM": "1",
                "ASM_TEST_PGREP_MODE": "none",
                "ASM_TEST_OSASCRIPT_RUNNING_RESULT": "yes",
                "ASM_TEST_OSASCRIPT_WINDOW_COUNT_RESULT": "1",
                "ASM_TEST_OSASCRIPT_OK_RESULT": "ok",
                "ASM_TEST_OSASCRIPT_DEFAULT_RESULT": raw_snapshot,
                "ASM_AUTOSAVE_LOCK_STALE_SECONDS": "1",
            },
        )
        self.assertIn("asm - AI session manager", cp.stdout)
        autosaves = sorted(layouts_dir.glob("autosave-*.json"))
        self.assertTrue(autosaves)
        self.assertFalse(stale_lock.exists())

    def test_help_skips_autosave_when_recent_stamp_exists(self) -> None:
        layouts_dir = self.home / ".local" / "share" / "asm" / "iterm-layouts"
        layouts_dir.mkdir(parents=True, exist_ok=True)
        (layouts_dir / ".autosave-stamp").write_text(str(int(time.time())), encoding="utf-8")

        cp = self.run_asm(
            "help",
            env_overrides={
                "ASM_TEST_UNAME_S": "Darwin",
                "ASM_TEST_UNAME_M": "arm64",
                "ASM_TEST_OPEN_ITERM": "1",
                "ASM_TEST_PGREP_MODE": "none",
                "ASM_TEST_OSASCRIPT_RUNNING_RESULT": "yes",
                "ASM_TEST_OSASCRIPT_WINDOW_COUNT_RESULT": "1",
                "ASM_TEST_OSASCRIPT_OK_RESULT": "ok",
                "ASM_TEST_OSASCRIPT_DEFAULT_RESULT": "W<<<ASMSEP>>>1<<<ASMSEP>>>10<<<ASMSEP>>>0,0,100,100<<<ASMSEP>>>1<<<ASMSEP>>>1",
            },
        )
        self.assertIn("asm - AI session manager", cp.stdout)
        self.assertEqual(sorted(layouts_dir.glob("autosave-*.json")), [])

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

    def test_inspect_bundle_view_prints_deep_bundle(self) -> None:
        cp = self.run_asm("inspect", "--bundle", "claude:11111111-1111-4111-8111-111111111111")
        self.assertIn("## Current State", cp.stdout)
        self.assertIn("## Decisions Already Made", cp.stdout)
        self.assertIn("## Suggested First Prompt", cp.stdout)
        self.assertNotIn("# Session Inspect", cp.stdout)

    def test_inspect_transcript_view_prints_transcript_sections(self) -> None:
        cp = self.run_asm("inspect", "--transcript", "claude:11111111-1111-4111-8111-111111111111")
        self.assertIn("# Session Transcript", cp.stdout)
        self.assertIn("Transcript path:", cp.stdout)
        self.assertIn("build a regression suite", cp.stdout)
        self.assertNotIn("## Commands", cp.stdout)

    def test_resume_marks_claude_workspace_trusted(self) -> None:
        cmd_file = self.root / "resume.cmd"
        self.run_asm(
            "resume",
            "claude:11111111-1111-4111-8111-111111111111",
            env_overrides={"ASM_CMD_FILE": str(cmd_file)},
        )
        config = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
        self.assertTrue(config["projects"][str(self.home / "workspace")]["hasTrustDialogAccepted"])

    def test_absorb_defaults_to_deep_handoff_command(self) -> None:
        cmd_file = self.root / "absorb.cmd"
        self.run_asm(
            "absorb",
            "--to",
            "codex",
            "claude:11111111-1111-4111-8111-111111111111",
            env_overrides={"ASM_CMD_FILE": str(cmd_file)},
        )
        command = cmd_file.read_text(encoding="utf-8").strip()
        self.assertIn("codex", command)
        self.assertIn("-to-codex-deep-", command)

    def test_handoff_to_claude_marks_target_workspace_trusted(self) -> None:
        cmd_file = self.root / "handoff.cmd"
        self.run_asm(
            "handoff",
            "--to",
            "claude",
            "codex:019d470b-a125-7533-ad4e-dc1d1219a9df",
            env_overrides={"ASM_CMD_FILE": str(cmd_file)},
        )
        config = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
        self.assertTrue(config["projects"][str(self.home / "codex-space")]["hasTrustDialogAccepted"])

    def test_handoff_prefers_project_hint_when_codex_cwd_is_home(self) -> None:
        session_id = "019d470b-a125-7533-ad4e-dc1d1219aaaa"
        repo_root = self.home / "project" / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        readme = repo_root / "README.md"
        readme.write_text("repo\n", encoding="utf-8")

        session_file = (
            self.home
            / ".codex"
            / "sessions"
            / "2026"
            / "04"
            / "09"
            / f"rollout-2026-04-09T08-00-00-{session_id}.jsonl"
        )
        session_file.parent.mkdir(parents=True, exist_ok=True)
        with session_file.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "session_meta", "payload": {"cwd": str(self.home)}}) + "\n")
            fh.write(
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": f"Review {readme} before continuing",
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )

        index_file = self.home / ".codex" / "session_index.jsonl"
        with index_file.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "id": session_id,
                        "thread_name": "handoff project hint",
                        "updated_at": "2026-04-09T08:02:03.123456Z",
                    }
                )
                + "\n"
            )

        output, code = self.run_asm_with_tty("handoff", "--to", "claude", f"codex:{session_id}")
        self.assertEqual(code, 0, output)
        log = (self.logs_dir / "claude.log").read_text(encoding="utf-8")
        self.assertIn(f"cwd={repo_root}", log)

    def test_active_tab_falls_back_to_all_when_missing(self) -> None:
        db_path = self.home / ".local" / "share" / "asm" / "meta.sqlite"
        self.run_asm("help")
        db = sqlite3.connect(db_path)
        db.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('active_tab', 'missing')")
        db.commit()
        db.close()
        cp = self.run_asm("__prompt_text")
        self.assertEqual(cp.stdout.strip(), "asm[child] →")

    def test_storage_bootstrap_is_idempotent(self) -> None:
        db_path = self.home / ".local" / "share" / "asm" / "meta.sqlite"
        self.run_asm("help")

        db = sqlite3.connect(db_path)
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        }
        self.assertTrue({"session_meta", "tabs", "tab_sessions", "session_tags", "app_state"}.issubset(tables))

        db.execute(
            "INSERT INTO app_state (key, value) VALUES ('active_tab', 'review')"
        )
        db.execute(
            "INSERT INTO tabs (name, sort_order, created_at) VALUES ('review', 1, '2026-04-02T00:00:00Z')"
        )
        db.commit()
        db.close()

        self.run_asm("help")
        db = sqlite3.connect(db_path)
        active_tab = db.execute("SELECT value FROM app_state WHERE key='active_tab'").fetchone()[0]
        review_count = db.execute("SELECT COUNT(*) FROM tabs WHERE name='review'").fetchone()[0]
        db.close()

        self.assertEqual(active_tab, "review")
        self.assertEqual(review_count, 1)

    def test_list_creates_cache_file(self) -> None:
        self.run_asm("list")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        self.assertTrue(cache_file.exists())

    def test_render_active_cache_ignores_missing_temp_file(self) -> None:
        missing = self.root / "missing-cache.json"
        cp = self.run_asm("__render_active_cache", str(missing))
        self.assertEqual(cp.stdout, "")

    def test_picker_prompt_uses_default_mode(self) -> None:
        cp = self.run_asm("__prompt_text")
        self.assertIn("asm", cp.stdout)

    def test_picker_prompt_uses_session_mode(self) -> None:
        state_file = self.root / "search-state.txt"
        state_file.write_text("session\n", encoding="utf-8")
        cp = self.run_asm("__picker_prompt", str(state_file))
        self.assertEqual(cp.stdout, "asm[child] → ")

    def test_picker_prompt_uses_search_mode(self) -> None:
        state_file = self.root / "search-state.txt"
        state_file.write_text("search\n", encoding="utf-8")
        cp = self.run_asm("__picker_prompt", str(state_file))
        self.assertEqual(cp.stdout, "search> ")

    def test_picker_slash_enters_search_mode(self) -> None:
        script = ASM_PATH.read_text(encoding="utf-8")
        self.assertIn("show-input+change-prompt(search> )+change-preview-window(hidden)", script)
        self.assertIn("hide-input+clear-query+change-prompt(", script)
        self.assertIn("change-preview-window(hidden)", script)
        self.assertIn("change-preview-window(${preview_window})", script)
        self.assertIn('change:reload($render_cmd)', script)
        self.assertIn("picker_char_action_bind()", script)
        self.assertIn("picker_char_accept_bind()", script)
        self.assertIn("picker_char_action_bind 'r'", script)
        self.assertIn("picker_char_action_bind '3'", script)
        self.assertIn("picker_char_accept_bind 't'", script)
        self.assertIn("preview_window='down:55%,border-top,wrap'", script)
        self.assertIn("preview_window='right:60%,border-left,wrap'", script)
        self.assertIn("__picker_set_preview_mode", script)
        self.assertIn("command asm resume", script)
        self.assertIn("bind_last_handoff", script)
        self.assertIn("__picker_enter_revive", script)

    def test_picker_p_toggles_pin_without_exiting_immediately(self) -> None:
        self.run_asm("list")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
        first_ref = rows[0]["ref"]
        agent, session_id = first_ref.split(":", 1)

        db_path = self.home / ".local" / "share" / "asm" / "meta.sqlite"
        db = sqlite3.connect(db_path)
        row = db.execute(
            "SELECT COALESCE(pinned, 0) FROM session_meta WHERE agent = ? AND session_id = ?",
            (agent, session_id),
        ).fetchone()
        db.close()
        initial_pinned = row[0] if row is not None else 0

        cp = self.run_asm("__toggle_pin_cache", str(cache_file), first_ref)
        self.assertEqual(cp.returncode, 0)

        db = sqlite3.connect(db_path)
        pinned = db.execute(
            "SELECT pinned FROM session_meta WHERE agent = ? AND session_id = ?",
            (agent, session_id),
        ).fetchone()[0]
        db.close()
        self.assertEqual(pinned, 0 if initial_pinned == 1 else 1)

    def test_picker_enter_emits_immediate_resume_command(self) -> None:
        self.run_asm("list")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        first_ref = json.loads(cache_file.read_text(encoding="utf-8"))[0]["ref"]
        cmd_file = self.root / "picker-enter.cmd"

        self._seed_fzf_script(
            textwrap.dedent(
                """\
                #!/bin/sh
                row="$(grep -v '^__header__' | grep -m1 .)"
                printf 'enter\n%s\n' "$row"
                """
            )
        )

        cp = self.run_asm(env_overrides={"ASM_CMD_FILE": str(cmd_file)})
        self.assertEqual(cp.returncode, 0)
        deadline = time.time() + 1.0
        while not cmd_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        command = cmd_file.read_text(encoding="utf-8").strip()
        self.assertEqual(command, f"command asm resume {first_ref}")

    def test_picker_v_can_open_handoff_flow(self) -> None:
        self.run_asm("list")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        first_ref = json.loads(cache_file.read_text(encoding="utf-8"))[0]["ref"]
        cmd_file = self.root / "picker-handoff.cmd"
        counter_file = self.root / "fzf-counter.txt"

        self._seed_fzf_script(
            textwrap.dedent(
                """\
                #!/bin/sh
                counter_file="${ASM_TEST_FZF_COUNTER_FILE:?}"
                count=0
                if [ -f "$counter_file" ]; then
                  count="$(cat "$counter_file")"
                fi
                count=$((count + 1))
                printf '%s' "$count" > "$counter_file"
                case "$count" in
                  1)
                    row="$(grep -v '^__header__' | grep -m1 .)"
                    printf 'v\n%s\n' "$row"
                    ;;
                  2)
                    row="$(grep '^__revive__:handoff\t' | head -n1)"
                    printf '%s\n' "$row"
                    ;;
                  3)
                    printf 'claude\n'
                    ;;
                esac
                """
            )
        )

        cp = self.run_asm(
            env_overrides={
                "ASM_CMD_FILE": str(cmd_file),
                "ASM_TEST_FZF_COUNTER_FILE": str(counter_file),
            }
        )
        self.assertEqual(cp.returncode, 0)
        command = cmd_file.read_text(encoding="utf-8").strip()
        self.assertIn("claude --dangerously-skip-permissions", command)
        self.assertIn(first_ref.replace(":", "_"), command)

    def test_picker_l_reuses_last_handoff_target(self) -> None:
        self.run_asm("list")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        first_ref = json.loads(cache_file.read_text(encoding="utf-8"))[0]["ref"]
        cmd_file = self.root / "picker-last-handoff.cmd"
        handoff_target_file = self.home / ".local" / "share" / "asm" / "last-handoff-target"
        handoff_target_file.parent.mkdir(parents=True, exist_ok=True)
        handoff_target_file.write_text("claude\n", encoding="utf-8")

        self._seed_fzf_script(
            textwrap.dedent(
                """\
                #!/bin/sh
                row="$(grep -v '^__header__' | grep -m1 .)"
                printf 'l\n%s\n' "$row"
                """
            )
        )

        cp = self.run_asm(env_overrides={"ASM_CMD_FILE": str(cmd_file)})
        self.assertEqual(cp.returncode, 0)
        command = cmd_file.read_text(encoding="utf-8").strip()
        self.assertIn("claude --dangerously-skip-permissions", command)
        self.assertIn(first_ref.replace(":", "_"), command)

    def test_picker_clone_ignores_trailing_key_lines(self) -> None:
        self.run_asm("list")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        cache_rows = json.loads(cache_file.read_text(encoding="utf-8"))
        clone_ref = next(
            entry["ref"] for entry in cache_rows if entry["ref"].startswith(("codex:", "claude:"))
        )
        cmd_file = self.root / "picker-clone.cmd"

        self._seed_fzf_script(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                target_ref="{clone_ref}"
                row="$(grep -v '^__header__' | awk -F '\t' -v ref="$target_ref" '$1 == ref {{ print; exit }}')"
                printf '%s\nc\n' "$row"
                """
            )
        )

        cp = self.run_asm(env_overrides={"ASM_CMD_FILE": str(cmd_file)})
        self.assertEqual(cp.returncode, 0)
        deadline = time.time() + 1.0
        while not cmd_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        command = cmd_file.read_text(encoding="utf-8").strip()
        self.assertEqual(command, f"command asm __clone {clone_ref}")

    def test_relate_records_clone_relationship_for_picker_and_inspect(self) -> None:
        child_ref = "codex:019d470b-a125-7533-ad4e-dc1d1219a9df"
        parent_ref = "claude:11111111-1111-4111-8111-111111111111"

        relate = self.run_asm("relate", child_ref, "--parent", parent_ref)
        self.assertIn(f"recorded relation: {child_ref} <- {parent_ref} (clone)", relate.stdout)

        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        self.run_asm("list", env_overrides={"ASM_FORCE_REFRESH": "1"})
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
        child = next(row for row in rows if row["ref"] == child_ref)
        parent = next(row for row in rows if row["ref"] == parent_ref)
        self.assertEqual(child["parent_ref"], parent_ref)
        self.assertEqual(child["relation_kind"], "clone")
        self.assertEqual(parent["child_count"], 1)
        self.assertIn(child_ref, parent["child_refs"])

        rendered = self.run_asm(
            "__render_picker_rows",
            str(cache_file),
            str(self.root / "picker-state.txt"),
            env_overrides={"NO_COLOR": "1"},
        )
        self.assertIn("fork<-claude:11111111", rendered.stdout)
        self.assertIn("forks:1", rendered.stdout)

        inspect = self.run_asm("inspect", "--print", child_ref)
        self.assertIn(f"- Forked from: {parent_ref}", inspect.stdout)

    def test_picker_slash_does_not_resume_when_key_line_trails_selection(self) -> None:
        self.run_asm("list")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        first_ref = json.loads(cache_file.read_text(encoding="utf-8"))[0]["ref"]
        cmd_file = self.root / "picker-slash.cmd"
        counter_file = self.root / "fzf-slash-counter.txt"

        self._seed_fzf_script(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                counter_file="${{ASM_TEST_FZF_COUNTER_FILE:?}}"
                count=0
                if [ -f "$counter_file" ]; then
                  count="$(cat "$counter_file")"
                fi
                count=$((count + 1))
                printf '%s' "$count" > "$counter_file"

                case "$count" in
                  1)
                    target_ref="{first_ref}"
                    row="$(grep -v '^__header__' | awk -F '\t' -v ref="$target_ref" '$1 == ref {{ print; exit }}')"
                    printf '%s\n/\n' "$row"
                    ;;
                  *)
                    exit 1
                    ;;
                esac
                """
            )
        )

        cp = self.run_asm(
            check=False,
            env_overrides={
                "ASM_CMD_FILE": str(cmd_file),
                "ASM_TEST_FZF_COUNTER_FILE": str(counter_file),
            },
        )
        self.assertFalse(cmd_file.exists())
        self.assertEqual(counter_file.read_text(encoding="utf-8"), "2")

    def test_picker_search_mode_unbinds_single_char_actions(self) -> None:
        cmd_file = self.root / "picker-search-unbind.cmd"
        out, rc = self.run_asm_tui("/c", "\u0003", env_overrides={"ASM_CMD_FILE": str(cmd_file)})
        self.assertEqual(rc, 0)
        self.assertFalse(cmd_file.exists())
        self.assertTrue(out)

    def test_pin_rejects_unknown_ref(self) -> None:
        cp = self.run_asm("pin", "claude:not-real", check=False)
        self.assertEqual(cp.returncode, 1)
        self.assertIn("session not found: claude:not-real", cp.stderr)

    def test_alias_rejects_unknown_ref(self) -> None:
        cp = self.run_asm("alias", "claude:not-real", "demo", check=False)
        self.assertEqual(cp.returncode, 1)
        self.assertIn("session not found: claude:not-real", cp.stderr)

    def test_list_output_uses_clock_timestamp(self) -> None:
        env = self.env.copy()
        env["NO_COLOR"] = "1"
        cp = subprocess.run(
            [str(ASM_PATH), "list"],
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
        first = next(line for line in cp.stdout.strip().splitlines() if line.strip() and not line.startswith("──"))
        self.assertRegex(first, r"\b\d{2}:\d{2}\b")

    def test_keys_print_contains_command_reference(self) -> None:
        cp = self.run_asm("keys", "--print")
        self.assertIn("# Keyboard Shortcuts", cp.stdout)
        self.assertIn("picker.shortcuts", cp.stdout)
        self.assertIn("session.resume.exact", cp.stdout)
        self.assertIn("| `v` | `session.revive.menu` |", cp.stdout)
        self.assertIn("session.revive.menu", cp.stdout)
        self.assertIn("session.handoff.last", cp.stdout)
        self.assertIn("session.tab.move", cp.stdout)

    def test_open_preset_prints_latest_session(self) -> None:
        project_root = self.home / "codex-space"
        config_file = project_root / "asm.json"
        config_file.write_text(
            json.dumps({"presets": {"review": ["codex:latest"]}}, ensure_ascii=False),
            encoding="utf-8",
        )

        sessions_dir = self.home / ".codex" / "sessions" / "2026" / "04" / "03"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        newer_wrong_id = "219d470b-a125-7533-ad4e-dc1d1219a9df"
        newer_wrong_file = sessions_dir / f"rollout-2026-04-03T08-00-00-{newer_wrong_id}.jsonl"
        newer_wrong_file.write_text(
            "\n".join(
                [
                    json.dumps({"type": "session_meta", "payload": {"cwd": str(self.home / "wrong-space")}}),
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": "wrong latest session"}],
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        fresh_time = time.time() + 120
        os.utime(newer_wrong_file, (fresh_time, fresh_time))

        cp = subprocess.run(
            [str(ASM_PATH), "open", "--print", "review"],
            text=True,
            capture_output=True,
            env={**self.env, "PWD": str(project_root)},
            cwd=project_root,
            check=True,
        )
        self.assertIn("# mode: resume", cp.stdout)
        self.assertIn(
            f"cd {self.home / 'wrong-space'} && codex --dangerously-bypass-approvals-and-sandbox resume {newer_wrong_id}",
            cp.stdout,
        )

    def test_open_preset_defaults_to_tab_for_multiple_sessions(self) -> None:
        project_root = self.home / "codex-space"
        config_file = project_root / "asm.json"
        config_file.write_text(
            json.dumps(
                {"presets": {"pair": ["codex:latest", "claude:11111111-1111-4111-8111-111111111111"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        cp = subprocess.run(
            [str(ASM_PATH), "open", "--print", "pair"],
            text=True,
            capture_output=True,
            env={**self.env, "PWD": str(project_root)},
            cwd=project_root,
            check=True,
        )
        self.assertIn("# mode: tab", cp.stdout)
        self.assertIn("command asm __iterm_tab", cp.stdout)
        self.assertIn("019d470b-a125-7533-ad4e-dc1d1219a9df", cp.stdout)
        self.assertIn("11111111-1111-4111-8111-111111111111", cp.stdout)

    def test_open_preset_resume_mode_requires_single_session(self) -> None:
        project_root = self.home / "codex-space"
        config_file = project_root / "asm.json"
        config_file.write_text(
            json.dumps(
                {
                    "defaultOpen": "resume",
                    "presets": {
                        "pair": ["codex:latest", "claude:11111111-1111-4111-8111-111111111111"]
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        cp = subprocess.run(
            [str(ASM_PATH), "open", "pair"],
            text=True,
            capture_output=True,
            env={**self.env, "PWD": str(project_root)},
            cwd=project_root,
            check=False,
        )
        self.assertEqual(cp.returncode, 1)
        self.assertIn("open mode 'resume' requires exactly one session", cp.stderr)

    def test_open_object_preset_uses_title_cwd_and_window_mode(self) -> None:
        project_root = self.home
        target_cwd = (self.home / "workspace").resolve()
        config_file = project_root / "asm.json"
        config_file.write_text(
            json.dumps(
                {
                    "presets": {
                        "focus": {
                            "title": "Focus Review",
                            "cwd": "workspace",
                            "open": "window",
                            "sessions": ["claude:11111111-1111-4111-8111-111111111111"],
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        cp = subprocess.run(
            [str(ASM_PATH), "open", "--print", "focus"],
            text=True,
            capture_output=True,
            env={**self.env, "PWD": str(project_root)},
            cwd=project_root,
            check=True,
        )
        self.assertIn("# preset: Focus Review", cp.stdout)
        self.assertIn(f"# cwd: {target_cwd}", cp.stdout)
        self.assertIn("# mode: window", cp.stdout)
        self.assertIn("command asm __iterm_window", cp.stdout)
        self.assertIn(f"cd\\ {target_cwd}", cp.stdout)

    def test_open_list_shows_preset_metadata(self) -> None:
        project_root = self.home
        config_file = project_root / "asm.json"
        config_file.write_text(
            json.dumps(
                {
                    "defaultOpen": "tab",
                    "presets": {
                        "focus": {
                            "title": "Focus Review",
                            "cwd": "workspace",
                            "open": "window",
                            "sessions": ["claude:11111111-1111-4111-8111-111111111111"],
                        },
                        "solo": ["codex:latest"],
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        cp = subprocess.run(
            [str(ASM_PATH), "open", "--list"],
            text=True,
            capture_output=True,
            env={**self.env, "PWD": str(project_root)},
            cwd=project_root,
            check=True,
        )
        self.assertIn("focus", cp.stdout)
        self.assertIn("title: Focus Review", cp.stdout)
        self.assertIn("mode: window", cp.stdout)
        self.assertIn("sessions: 1", cp.stdout)
        self.assertIn("solo", cp.stdout)

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
        lines = cp.stdout.strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("Tabs:", lines[0])
        self.assertIn("All:2", lines[0])
        self.assertIn("review:1", lines[0])
        self.assertIn("State:", lines[1])
        self.assertIn("tab=All", lines[1])
        self.assertIn("visible=2/2", lines[1])
        self.assertIn("preview=overview", lines[1])
        self.assertIn("Keys:", lines[2])
        self.assertIn("enter resume", lines[2])
        self.assertIn("l last", lines[2])
        self.assertIn("m move", lines[2])

    def test_render_picker_rows_filters_active_tab_and_emits_header_lines(self) -> None:
        self.run_asm("help")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                [
                    {
                        "ref": "claude:11111111-1111-4111-8111-111111111111",
                        "agent": "claude",
                        "id": "11111111-1111-4111-8111-111111111111",
                        "title": "review patch",
                        "cwd": str(self.home / "workspace"),
                        "updated_epoch": 1712000000,
                        "tab_name": "review",
                    },
                    {
                        "ref": "codex:019d470b-a125-7533-ad4e-dc1d1219a9df",
                        "agent": "codex",
                        "id": "019d470b-a125-7533-ad4e-dc1d1219a9df",
                        "title": "other work",
                        "cwd": str(self.home / "workspace"),
                        "updated_epoch": 1712000100,
                        "tab_name": "",
                    },
                ]
            ),
            encoding="utf-8",
        )
        db_path = self.home / ".local" / "share" / "asm" / "meta.sqlite"
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT OR IGNORE INTO tabs (name, sort_order, created_at) VALUES ('review', 1, '2026-04-02T00:00:00Z')"
        )
        db.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('active_tab', 'review')")
        db.commit()
        db.close()

        cp = self.run_asm(
            "__render_picker_rows",
            str(cache_file),
            str(self.root / "picker-state.txt"),
            env_overrides={"NO_COLOR": "1", "ASM_PICKER_QUERY_HINT": "hybrid rank + live filter"},
        )
        lines = cp.stdout.strip().splitlines()
        self.assertGreaterEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("__header__\t__header__\tTabs:"))
        self.assertTrue(lines[1].startswith("__header__\t__header__\tState:"))
        self.assertTrue(lines[2].startswith("__header__\t__header__\tKeys:"))
        self.assertIn("Tabs:", lines[0])
        self.assertIn("All:2", lines[0])
        self.assertIn("review:1", lines[0])
        self.assertIn("State:", lines[1])
        self.assertIn("tab=review", lines[1])
        self.assertIn("visible=1/2", lines[1])
        self.assertIn("preview=overview", lines[1])
        self.assertIn("search=hybrid rank + live filter", lines[1])
        body = "\n".join(lines[3:])
        self.assertIn("claude:11111111-1111-4111-8111-111111111111", body)
        self.assertNotIn("codex:019d470b-a125-7533-ad4e-dc1d1219a9df", body)

    def test_render_picker_rows_query_prefers_exact_chunk_over_scattered_letters(self) -> None:
        self.run_asm("help")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                [
                    {
                        "ref": "opencode:ses_old",
                        "agent": "opencode",
                        "id": "ses_old",
                        "title": "legacy session",
                        "alias": "asm dashboard cleanup",
                        "cwd": str(self.home / "workspace"),
                        "updated_epoch": 1712000000,
                        "tab_name": "",
                    },
                    {
                        "ref": "codex:019d470b-a125-7533-ad4e-dc1d1219a9df",
                        "agent": "codex",
                        "id": "019d470b-a125-7533-ad4e-dc1d1219a9df",
                        "title": "a slow migration",
                        "alias": "",
                        "cwd": str(self.home / "workspace"),
                        "updated_epoch": 1712000100,
                        "tab_name": "",
                    },
                ]
            ),
            encoding="utf-8",
        )

        cp = self.run_asm(
            "__render_picker_rows",
            str(cache_file),
            str(self.root / "picker-state.txt"),
            "asm",
            env_overrides={"NO_COLOR": "1"},
        )
        lines = [line for line in cp.stdout.strip().splitlines() if line and not line.startswith("__header__")]
        self.assertEqual(len(lines), 1)
        self.assertIn("opencode:ses_old", lines[0])
        self.assertNotIn("codex:019d470b-a125-7533-ad4e-dc1d1219a9df", cp.stdout)

    def test_render_lines_uses_compact_leading_cluster_and_flexible_time(self) -> None:
        cache_file = self.root / "compact-cache.json"
        cache_file.write_text(
            json.dumps(
                [
                    {
                        "ref": "claude:11111111-1111-4111-8111-111111111111",
                        "agent": "claude",
                        "id": "11111111-1111-4111-8111-111111111111",
                        "title": "tight picker row",
                        "cwd": str(self.home / "workspace"),
                        "updated_epoch": 1712000000,
                        "updated_display": "2026-04-02T08:00:00Z",
                        "tab_name": "",
                    }
                ]
            ),
            encoding="utf-8",
        )
        runtime = Path(__file__).resolve().parents[1] / "bin" / "asm_runtime.py"
        cp = subprocess.run(
            [sys.executable, str(runtime), "render-lines", str(cache_file), "All", "1712003600"],
            text=True,
            capture_output=True,
            check=True,
            env={**self.env, "NO_COLOR": "1", "ASM_RENDER_COLUMNS": "100"},
        )
        row = cp.stdout.strip().splitlines()[0].split("\t", 2)[2]
        self.assertTrue(row.startswith("C "))
        self.assertIn("1h", row)
        self.assertNotIn("CLD", row)

    def test_render_lines_highlights_query_hits_in_picker_output(self) -> None:
        cache_file = self.root / "highlight-cache.json"
        cache_file.write_text(
            json.dumps(
                [
                    {
                        "ref": "claude:11111111-1111-4111-8111-111111111111",
                        "agent": "claude",
                        "id": "11111111-1111-4111-8111-111111111111",
                        "title": "asm dashboard cleanup",
                        "cwd": str(self.home / "workspace"),
                        "updated_epoch": 1712000000,
                        "updated_display": "2026-04-02T08:00:00Z",
                        "tab_name": "",
                    }
                ]
            ),
            encoding="utf-8",
        )
        runtime = Path(__file__).resolve().parents[1] / "bin" / "asm_runtime.py"
        cp = subprocess.run(
            [sys.executable, str(runtime), "render-lines", str(cache_file), "All", "1712003600", "dashboard"],
            text=True,
            capture_output=True,
            check=True,
            env={**self.env, "ASM_FORCE_COLOR": "1", "ASM_RENDER_COLUMNS": "100"},
        )
        row = cp.stdout.strip().splitlines()[0].split("\t", 2)[2]
        self.assertIn("\x1b[38;5;117m", row)
        self.assertIn("\x1b[1;38;5;229m", row)
        self.assertIn("asm dashboard cleanup", row)

    def test_picker_preview_includes_summary_and_jira_commands(self) -> None:
        self.run_asm("list")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
        rows[0]["alias"] = "ASM-101 tighten picker ui"
        cache_file.write_text(json.dumps(rows), encoding="utf-8")
        state_file = self.root / "preview-state.txt"
        state_file.write_text("session\npreview:commands\n", encoding="utf-8")

        cp = self.run_asm(
            "__picker_preview",
            str(cache_file),
            str(state_file),
            rows[0]["ref"],
            env_overrides={"NO_COLOR": "1", "ASM_CACHE_FILE": str(cache_file)},
        )
        self.assertIn("Summary:", cp.stdout)
        self.assertIn("Jira:         ASM-101", cp.stdout)
        self.assertIn("asm jira view ASM-101", cp.stdout)
        self.assertIn("asm jira open ASM-101", cp.stdout)

    def test_picker_shift_active_tab_cycles_visible_filter(self) -> None:
        self.run_asm("help")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                [
                    {
                        "ref": "claude:11111111-1111-4111-8111-111111111111",
                        "agent": "claude",
                        "id": "11111111-1111-4111-8111-111111111111",
                        "title": "review patch",
                        "cwd": str(self.home / "workspace"),
                        "updated_epoch": 1712000000,
                        "tab_name": "review",
                    },
                    {
                        "ref": "codex:019d470b-a125-7533-ad4e-dc1d1219a9df",
                        "agent": "codex",
                        "id": "019d470b-a125-7533-ad4e-dc1d1219a9df",
                        "title": "other work",
                        "cwd": str(self.home / "workspace"),
                        "updated_epoch": 1712000100,
                        "tab_name": "",
                    },
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

        state_file = self.root / "picker-state.txt"
        state_file.write_text("session\n", encoding="utf-8")
        self.run_asm("__picker_shift_active_tab", str(state_file), "right")
        review_cp = self.run_asm(
            "__render_picker_rows",
            str(cache_file),
            str(state_file),
            env_overrides={"NO_COLOR": "1"},
        )
        self.assertIn("tab=review", review_cp.stdout)

        self.run_asm("__picker_shift_active_tab", str(state_file), "right")
        all_cp = self.run_asm(
            "__render_picker_rows",
            str(cache_file),
            str(state_file),
            env_overrides={"NO_COLOR": "1"},
        )
        self.assertIn("tab=All", all_cp.stdout)

    def test_picker_preview_uses_commands_mode_from_state(self) -> None:
        self.run_asm("list")
        cache_file = self.home / ".local" / "share" / "asm" / "session-cache.json"
        state_file = self.root / "picker-state.txt"
        state_file.write_text("session\npreview:commands\n", encoding="utf-8")

        cp = self.run_asm(
            "__picker_preview",
            str(cache_file),
            str(state_file),
            "claude:11111111-1111-4111-8111-111111111111",
            env_overrides={"ASM_CACHE_FILE": str(cache_file)},
        )
        self.assertIn("Preview:      commands", cp.stdout)
        self.assertIn("Resume:", cp.stdout)
        self.assertIn("Handoff:", cp.stdout)
        self.assertIn("asm export --to portable", cp.stdout)
        self.assertNotIn("Transcript:", cp.stdout)

    def test_standalone_script_reports_missing_store_helper(self) -> None:
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
        self.assertIn("store helper not found", cp.stderr)

    def test_standalone_script_reports_missing_runtime_helper(self) -> None:
        standalone = self.root / "standalone" / "asm"
        standalone.parent.mkdir(parents=True, exist_ok=True)
        standalone.write_text(ASM_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        standalone.chmod(standalone.stat().st_mode | stat.S_IXUSR)
        store_copy = self.root / "standalone" / "asm_store.py"
        store_copy.write_text(
            (Path(__file__).resolve().parents[1] / "bin" / "asm_store.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
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

    def test_iterm_restore_wraps_shell_sessions_and_preserves_layout_metadata(self) -> None:
        osascript_log = self.root / "osascript.log"
        self._seed_logging_osascript(osascript_log)

        layouts_dir = self.home / ".local" / "share" / "asm" / "iterm-layouts"
        layouts_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = layouts_dir / "latest.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "created_at": "2026-04-02T08:00:00Z",
                    "window_count": 1,
                    "session_count": 3,
                    "windows": [
                        {
                            "bounds": [0, 0, 100, 100],
                            "tabs": [
                                {
                                    "tab_title": "Review",
                                    "split_direction": "vertical",
                                    "sessions": [
                                        {
                                            "restore_command": "echo shell-window",
                                            "restore_kind": "shell",
                                            "profile": "Main",
                                        },
                                        {
                                            "restore_command": "echo vertical-split",
                                            "restore_kind": "command",
                                            "profile": "Split",
                                        },
                                    ],
                                },
                                {
                                    "tab_title": "Notes",
                                    "split_direction": "horizontal",
                                    "sessions": [
                                        {
                                            "restore_command": "echo second-tab",
                                            "restore_kind": "command",
                                            "profile": "TabProfile",
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        cp = self.run_asm(
            "iterm-restore",
            "latest",
            env_overrides={
                "ASM_TEST_OPEN_ITERM": "1",
            },
        )
        self.assertIn("restored 1 windows / 3 sessions", cp.stdout)

        log = osascript_log.read_text(encoding="utf-8")
        self.assertIn("kind=window", log)
        self.assertIn(r"cmd=/bin/zsh -ilc echo\ shell-window\;\ exec\ /bin/zsh\ -il", log)
        self.assertIn("kind=bounds", log)
        self.assertIn("bounds=0,0,100,100", log)
        self.assertIn("kind=split-vertical", log)
        self.assertIn("direction=vertical", log)
        self.assertIn("kind=tab", log)
        self.assertIn("profile=TabProfile", log)
        self.assertIn("kind=title", log)
        self.assertIn("title=Notes", log)

    def test_iterm_restore_fails_when_split_step_fails(self) -> None:
        osascript_log = self.root / "osascript.log"
        self._seed_logging_osascript(osascript_log)

        layouts_dir = self.home / ".local" / "share" / "asm" / "iterm-layouts"
        layouts_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = layouts_dir / "latest.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "window_count": 1,
                    "session_count": 2,
                    "windows": [
                        {
                            "tabs": [
                                {
                                    "split_direction": "vertical",
                                    "sessions": [
                                        {"restore_command": "echo one", "restore_kind": "command"},
                                        {"restore_command": "echo two", "restore_kind": "command"},
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        cp = self.run_asm(
            "iterm-restore",
            "latest",
            check=False,
            env_overrides={
                "ASM_TEST_OPEN_ITERM": "1",
                "ASM_TEST_OSASCRIPT_FAIL_KIND": "split-vertical",
            },
        )
        self.assertEqual(cp.returncode, 1)
        self.assertIn("failed to split iTerm2 session", cp.stderr)
        self.assertNotIn("restored 1 windows / 2 sessions", cp.stdout)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
