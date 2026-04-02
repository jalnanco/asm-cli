#!/usr/bin/env python3
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def clean_text(text: str) -> str:
    return " ".join((text or "").replace("\\n", " ").replace("\\t", " ").split())


def truncate_text(text: str, max_len: int) -> str:
    text = clean_text(text)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def color(code: str, text: str) -> str:
    if not color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def color_enabled() -> bool:
    if os.environ.get("ASM_FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CLICOLOR_FORCE", "0") not in ("", "0"):
        return True
    return sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"


def short_relative(now_epoch: int, epoch: int) -> str:
    diff = max(0, now_epoch - int(epoch))
    if diff < 60:
        return f"{diff}s"
    if diff < 3600:
        return f"{diff // 60}m"
    if diff < 86400:
        return f"{diff // 3600}h"
    if diff < 2592000:
        return f"{diff // 86400}d"
    return f"{diff // 2592000}mo"


def agent_label(agent: str) -> str:
    return {
        "codex": "CDX",
        "claude": "CLD",
        "opencode": "OPN",
        "cursor-agent": "CUR",
        "gemini": "GEM",
    }.get(agent, agent.upper())


def agent_badge(agent: str, label: str) -> str:
    return {
        "codex": color("38;5;75", label),
        "claude": color("38;5;179", label),
        "opencode": color("38;5;71", label),
        "cursor-agent": color("38;5;110", label),
        "gemini": color("38;5;141", label),
    }.get(agent, color("38;5;245", label))


def pin_badge(text: str) -> str:
    return color("1;38;5;203", text) if text.strip() == "PIN" else color("38;5;240", text)


def muted(text: str) -> str:
    return color("38;5;244", text)


def cmd_render_lines(cache_file: str, active_tab: str, now_epoch: int) -> int:
    path = Path(cache_file)
    if not path.exists():
        return 0
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        tab_name = row.get("tab_name", "")
        if active_tab != "All" and tab_name != active_tab:
            continue
        agent = row.get("agent", "")
        pinned = "PIN" if row.get("pinned", 0) == 1 else "   "
        age = short_relative(now_epoch, int(row.get("updated_epoch", 0) or 0))
        cwd = row.get("cwd", "")
        if not cwd or cwd == "/":
            project = "/"
        elif cwd == os.path.expanduser("~"):
            project = "~"
        else:
            project = Path(cwd).name
        title = row.get("alias") or row.get("title") or str(row.get("id", ""))[:12]
        tags = row.get("tags_text", "")
        if tags:
            title = f"{title}  {tags}"
        print(
            "\t".join(
                [
                    row.get("ref", ""),
                    f"{pin_badge(f'{pinned:<3}')}  {agent_badge(agent, f'{agent_label(agent):<3}')}  {muted(f'{age:<4}')}  {muted(f'{truncate_text(project, 18):<18}')}  {truncate_text(title, 100)}",
                ]
            )
        )
    return 0


def cmd_codex_file_path(home: str, session_id: str, cache_path: str) -> int:
    home_path = Path(home)
    cache = Path(cache_path)
    sessions_root = home_path / ".codex" / "sessions"
    ttl = 300

    def build_index():
        idx = {}
        if sessions_root.exists():
            for path in sessions_root.rglob("*.jsonl"):
                m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path.name)
                if m:
                    idx[m.group(1)] = str(path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"created_at": int(time.time()), "index": idx}), encoding="utf-8")
        return idx

    try:
        if cache.exists():
            payload = json.loads(cache.read_text(encoding="utf-8"))
            if int(time.time()) - int(payload.get("created_at", 0)) < ttl:
                index = payload.get("index", {})
            else:
                index = build_index()
        else:
            index = build_index()
    except Exception:
        index = build_index()

    path = index.get(session_id, "")
    if path:
        print(path)
    return 0


def cmd_list_claude(home: str, limit: int, cache_path: str) -> int:
    root = Path(home) / ".claude" / "projects"
    cache = Path(cache_path)
    files = sorted(
        [p for p in root.rglob("*.jsonl") if "/subagents/" not in str(p)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    try:
        payload = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    except Exception:
        payload = {}

    next_cache = {}
    count = 0
    for path in files:
        stat = path.stat()
        key = str(path)
        cached = payload.get(key)
        record = None
        if cached and cached.get("mtime") == stat.st_mtime and cached.get("size") == stat.st_size:
            record = cached.get("record")
        else:
            session_id = ""
            cwd = ""
            title = ""
            try:
                with path.open(encoding="utf-8") as fh:
                    for line in fh:
                        obj = json.loads(line)
                        if not session_id and obj.get("sessionId"):
                            session_id = obj.get("sessionId", "")
                        if not cwd and obj.get("cwd"):
                            cwd = obj.get("cwd", "")
                        if not title and obj.get("type") == "user" and not obj.get("isMeta", False):
                            msg = (obj.get("message") or {}).get("content")
                            if isinstance(msg, str):
                                title = msg
                            elif isinstance(msg, list):
                                title = " ".join(
                                    part.get("text", "")
                                    for part in msg
                                    if isinstance(part, dict) and part.get("type") == "text"
                                )
                        if session_id and cwd and title:
                            break
            except Exception:
                continue
            if not session_id:
                continue
            updated_epoch = int(stat.st_mtime)
            updated_display = datetime.fromtimestamp(updated_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            record = {
                "agent": "claude",
                "id": session_id,
                "title": title,
                "cwd": cwd,
                "updated_epoch": updated_epoch,
                "updated_display": updated_display,
            }
        next_cache[key] = {"mtime": stat.st_mtime, "size": stat.st_size, "record": record}
        print(json.dumps(record, ensure_ascii=False))
        count += 1
        if count >= limit:
            break

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(next_cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return 0


def cmd_import_iterm_tabs(timeout_s: float) -> int:
    script = r'''
tell application "iTerm2"
  if (count of windows) = 0 then return ""
  tell current window
    set rowList to {}
    set tabIndex to 1
    repeat with t in tabs
      try
        set bodyText to contents of current session of t
      on error
        set bodyText to ""
      end try
      if (length of bodyText) > 3000 then
        set bodyText to text -3000 thru -1 of bodyText
      end if
      copy ((tabIndex as string) & "<<<ASMSEP>>>" & bodyText) to end of rowList
      set tabIndex to tabIndex + 1
    end repeat
    set oldTids to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "<<<ASMROW>>>"
    set joinedText to rowList as text
    set AppleScript's text item delimiters to oldTids
    return joinedText
  end tell
end tell
'''
    patterns = [
        (re.compile(r'claude(?:\s+--[^\s]+)*\s+--resume\s+([0-9a-fA-F-]{36})'), 'claude:{}'),
        (re.compile(r'gemini --resume ([0-9a-fA-F-]{36})'), 'gemini:{}'),
        (re.compile(r'cursor-agent --resume ([0-9a-fA-F-]{36})'), 'cursor-agent:{}'),
        (re.compile(r'codex resume ([A-Za-z0-9._:-]+)'), 'codex:{}'),
        (re.compile(r'opencode -s ([A-Za-z0-9._:-]+)'), 'opencode:{}'),
    ]
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return 0
    if proc.returncode != 0 or not proc.stdout:
        return 0
    for row in proc.stdout.split("<<<ASMROW>>>"):
        if "<<<ASMSEP>>>" not in row:
            continue
        idx, body = row.split("<<<ASMSEP>>>", 1)
        found = None
        for pattern, template in patterns:
            matches = pattern.findall(body)
            if matches:
                found = template.format(matches[-1])
        if found:
            print(f"iterm-{idx}\t{found}")
    return 0


def cmd_extract_current_window(source_path: str, target_path: str, snapshot_name: str) -> int:
    source = json.loads(Path(source_path).read_text(encoding="utf-8"))
    windows = [w for w in source.get("windows", []) if w.get("is_current_window") is True]
    if not windows:
        raise SystemExit(1)
    window = windows[0]
    payload = {
        "version": source.get("version", 1),
        "created_at": source.get("created_at", ""),
        "snapshot_name": snapshot_name,
        "host": source.get("host", ""),
        "window_count": 1,
        "session_count": sum(len(tab.get("sessions", [])) for tab in window.get("tabs", [])),
        "windows": [{**window, "window_order": 1, "is_current_window": True}],
    }
    Path(target_path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0


def main() -> int:
    cmd = sys.argv[1]
    if cmd == "render-lines":
        return cmd_render_lines(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    if cmd == "codex-file-path":
        return cmd_codex_file_path(sys.argv[2], sys.argv[3], sys.argv[4])
    if cmd == "list-claude":
        return cmd_list_claude(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    if cmd == "import-iterm-tabs":
        return cmd_import_iterm_tabs(float(sys.argv[2]))
    if cmd == "extract-current-window":
        return cmd_extract_current_window(sys.argv[2], sys.argv[3], sys.argv[4])
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
