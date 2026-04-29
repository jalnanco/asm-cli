#!/usr/bin/env python3
import curses
import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path


HELP_TEXT = [
    "q quit",
    "j/k or arrows move",
    "PgUp/PgDn page",
    "g/G top/bottom",
    "/ search",
    "n/N next/prev match",
    "[ dump to native scrollback and exit",
    "o open link/file target",
    "? toggle help",
]


def resolve_open_target(candidate: str) -> str | None:
    candidate = candidate.strip()
    if not candidate:
        return None
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate

    trimmed = candidate.rstrip("),.;")
    if os.path.exists(trimmed):
        return trimmed

    probe = trimmed
    while ":" in probe:
        probe = probe.rsplit(":", 1)[0]
        if os.path.exists(probe):
            return probe
    return None


def parse_targets(content: str) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split()
        for part in parts:
            if part.startswith("http://") or part.startswith("https://"):
                target = resolve_open_target(part)
                if target and target not in seen:
                    seen.add(target)
                    targets.append(target)

        slash_index = line.find("/")
        if slash_index >= 0:
            target = resolve_open_target(line[slash_index:])
            if target and target not in seen:
                seen.add(target)
                targets.append(target)
    return targets


class InspectViewer:
    def __init__(self, stdscr: "curses._CursesWindow", content: str, source_path: str):
        self.stdscr = stdscr
        self.content = content
        self.source_path = source_path
        self.raw_lines = content.splitlines() or [""]
        self.targets = parse_targets(content)
        self.offset = 0
        self.status = "q quit | / search | [ dump | o open"
        self.search_query = ""
        self.search_hits: list[int] = []
        self.show_help = False

    def wrapped_lines(self, width: int) -> list[str]:
        width = max(width, 20)
        wrapped: list[str] = []
        for line in self.raw_lines:
            chunks = textwrap.wrap(
                line,
                width=width,
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            )
            wrapped.extend(chunks or [""])
        return wrapped

    def page_height(self) -> int:
        height, _ = self.stdscr.getmaxyx()
        return max(1, height - 2)

    def clamp_offset(self, line_count: int) -> None:
        self.offset = max(0, min(self.offset, max(0, line_count - self.page_height())))

    def render(self) -> None:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        body_height = self.page_height()
        wrapped = self.wrapped_lines(width - 1)
        self.clamp_offset(len(wrapped))

        for row in range(body_height):
            index = self.offset + row
            if index >= len(wrapped):
                break
            self.stdscr.addnstr(row, 0, wrapped[index], width - 1)

        footer = self.status
        self.stdscr.attron(curses.A_REVERSE)
        self.stdscr.addnstr(height - 2, 0, footer.ljust(width - 1), width - 1)
        progress = f"{self.offset + 1}/{max(len(wrapped), 1)}  {Path(self.source_path).name}"
        self.stdscr.addnstr(height - 1, 0, progress.ljust(width - 1), width - 1)
        self.stdscr.attroff(curses.A_REVERSE)

        if self.show_help:
            self.render_help()

        self.stdscr.refresh()

    def render_help(self) -> None:
        height, width = self.stdscr.getmaxyx()
        box_width = min(width - 4, max(len(line) for line in HELP_TEXT) + 4)
        box_height = len(HELP_TEXT) + 2
        top = max(1, (height - box_height) // 2)
        left = max(1, (width - box_width) // 2)
        win = curses.newwin(box_height, box_width, top, left)
        win.box()
        for idx, line in enumerate(HELP_TEXT, start=1):
            win.addnstr(idx, 2, line, box_width - 4)
        win.refresh()

    def prompt(self, label: str) -> str:
        height, width = self.stdscr.getmaxyx()
        curses.echo()
        curses.curs_set(1)
        self.stdscr.move(height - 1, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addnstr(height - 1, 0, label, width - 1)
        self.stdscr.refresh()
        value = self.stdscr.getstr(height - 1, len(label), width - len(label) - 1)
        curses.noecho()
        curses.curs_set(0)
        return value.decode("utf-8", errors="replace")

    def update_search_hits(self) -> None:
        self.search_hits = []
        if not self.search_query:
            return
        query = self.search_query.lower()
        for index, line in enumerate(self.wrapped_lines(self.stdscr.getmaxyx()[1] - 1)):
            if query in line.lower():
                self.search_hits.append(index)

    def jump_search(self, forward: bool) -> None:
        if not self.search_query:
            self.status = "search query is empty"
            return
        self.update_search_hits()
        if not self.search_hits:
            self.status = f"no match for: {self.search_query}"
            return

        current = self.offset
        candidates = self.search_hits if forward else list(reversed(self.search_hits))
        for hit in candidates:
            if (forward and hit > current) or (not forward and hit < current):
                self.offset = hit
                self.status = f"match: {self.search_query}"
                return
        self.offset = candidates[0]
        self.status = f"wrapped search: {self.search_query}"

    def dump_to_scrollback(self) -> int:
        curses.endwin()
        sys.stdout.write(self.content)
        if not self.content.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
        return 0

    def open_target(self) -> None:
        if not self.targets:
            self.status = "no link or file target found"
            return

        selection = self.select_target()
        if not selection:
            self.status = "open canceled"
            return

        opener = shlex.split(os.environ.get("ASM_INSPECT_OPEN_CMD", "open"))
        try:
            subprocess.run([*opener, selection], check=False)
        except Exception as exc:
            self.status = f"open failed: {exc}"
            return
        self.status = f"opened: {selection}"

    def select_target(self) -> str | None:
        height, width = self.stdscr.getmaxyx()
        box_height = min(height - 4, len(self.targets) + 4)
        box_width = min(width - 4, max(len(target) for target in self.targets) + 6)
        top = max(1, (height - box_height) // 2)
        left = max(1, (width - box_width) // 2)
        win = curses.newwin(box_height, box_width, top, left)
        win.keypad(True)
        index = 0

        while True:
            win.erase()
            win.box()
            win.addnstr(0, 2, " Open target ", box_width - 4)
            visible = self.targets[: box_height - 2]
            for row, target in enumerate(visible, start=1):
                marker = ">" if row - 1 == index else " "
                win.addnstr(row, 1, f"{marker} {target}", box_width - 3)
            win.refresh()

            key = win.getch()
            if key in (ord("q"), 27):
                return None
            if key in (curses.KEY_UP, ord("k")):
                index = max(0, index - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                index = min(len(visible) - 1, index + 1)
            elif key in (10, 13, curses.KEY_ENTER):
                return visible[index]

    def run(self) -> int:
        curses.curs_set(0)
        self.stdscr.keypad(True)

        while True:
            self.render()
            key = self.stdscr.getch()

            if key in (ord("q"), 27):
                return 0
            if key in (curses.KEY_DOWN, ord("j")):
                self.offset += 1
            elif key in (curses.KEY_UP, ord("k")):
                self.offset -= 1
            elif key == curses.KEY_NPAGE:
                self.offset += self.page_height()
            elif key == curses.KEY_PPAGE:
                self.offset -= self.page_height()
            elif key == ord("g"):
                self.offset = 0
            elif key == ord("G"):
                self.offset = 10**9
            elif key == ord("?"):
                self.show_help = not self.show_help
            elif key == ord("/"):
                self.search_query = self.prompt("search> ").strip()
                if self.search_query:
                    self.jump_search(True)
                else:
                    self.status = "search cleared"
            elif key == ord("n"):
                self.jump_search(True)
            elif key == ord("N"):
                self.jump_search(False)
            elif key == ord("["):
                return self.dump_to_scrollback()
            elif key == ord("o"):
                self.open_target()


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    source_path = sys.argv[1]
    content = Path(source_path).read_text(encoding="utf-8")
    return curses.wrapper(lambda stdscr: InspectViewer(stdscr, content, source_path).run())


if __name__ == "__main__":
    raise SystemExit(main())
