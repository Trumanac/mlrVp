"""One-shot migration script for the UI scale redesign.

Rewrites size calls in a target Python file:

    widget.setFixedHeight(32)    ->  bind_fixed_height(widget, 32)
    widget.setFixedWidth(80)     ->  bind_fixed_width(widget, 80)
    widget.setFixedSize(40, 20)  ->  bind_fixed_size(widget, 40, 20)
    widget.setMinimumWidth(200)  ->  bind_min_width(widget, 200)
    widget.setMinimumHeight(60)  ->  bind_min_height(widget, 60)
    widget.setMaximumWidth(48)   ->  bind_max_width(widget, 48)
    widget.setMaximumHeight(60)  ->  bind_max_height(widget, 60)
    widget.setMinimumSize(80, 40)->  bind_min_size(widget, 80, 40)

Only literal numeric arguments are rewritten.  Dynamic expressions are
left alone and printed for manual review.

Also injects the import line if missing:
    from mlrvp.gui.scale import (...)

Usage:
    python migrate_ui_scale.py <file1> [file2 ...]
"""
import re
import sys
from pathlib import Path

NUM = r"\d+(?:\.\d+)?"

# (regex, replacement, requires_import_name)
PATTERNS_SINGLE = [
    ("setFixedHeight",  "bind_fixed_height"),
    ("setFixedWidth",   "bind_fixed_width"),
    ("setMinimumWidth", "bind_min_width"),
    ("setMinimumHeight","bind_min_height"),
    ("setMaximumWidth", "bind_max_width"),
    ("setMaximumHeight","bind_max_height"),
]
PATTERNS_DOUBLE = [
    ("setFixedSize",   "bind_fixed_size"),
    ("setMinimumSize", "bind_min_size"),
]

# Match a chained receiver: identifiers, attribute access, simple subscripts
RECV = r"[A-Za-z_][\w\.\[\]]*"


def migrate(text: str) -> tuple[str, set[str], list[str]]:
    """Return (new_text, used_helpers, skipped_messages)."""
    used: set[str] = set()
    skipped: list[str] = []

    for method, helper in PATTERNS_SINGLE:
        # widget.setFixedHeight(NUM)
        pat = re.compile(rf"(?P<recv>{RECV})\.{method}\((?P<arg>{NUM})\)")
        def _sub(m, helper=helper):
            used.add(helper)
            return f"{helper}({m['recv']}, {m['arg']})"
        text = pat.sub(_sub, text)

        # warn on remaining non-literal calls
        leftover = re.compile(rf"\b{method}\((?!{NUM}\)).+?\)")
        for m in leftover.finditer(text):
            skipped.append(f"  SKIP non-literal: {m.group(0)}")

    for method, helper in PATTERNS_DOUBLE:
        pat = re.compile(
            rf"(?P<recv>{RECV})\.{method}\("
            rf"(?P<a>{NUM})\s*,\s*(?P<b>{NUM})\)")
        def _sub(m, helper=helper):
            used.add(helper)
            return f"{helper}({m['recv']}, {m['a']}, {m['b']})"
        text = pat.sub(_sub, text)
        leftover = re.compile(rf"\b{method}\((?!{NUM}\s*,\s*{NUM}\)).+?\)")
        for m in leftover.finditer(text):
            skipped.append(f"  SKIP non-literal: {m.group(0)}")

    return text, used, skipped


def inject_import(text: str, used: set[str]) -> str:
    if not used:
        return text
    if "from mlrvp.gui.scale import" in text:
        return text  # assume the user already imported; manual fix if needed
    helpers = sorted(used)
    import_line = (
        "from mlrvp.gui.scale import "
        + ", ".join(helpers)
        + "\n"
    )
    # Insert after the last 'from PySide6' or 'import PySide6' line
    lines = text.splitlines(keepends=True)
    insert_at = 0
    for i, ln in enumerate(lines):
        if ln.startswith(("from PySide6", "import PySide6", "from mlrvp", "import mlrvp")):
            insert_at = i + 1
    lines.insert(insert_at, import_line)
    return "".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for arg in sys.argv[1:]:
        path = Path(arg)
        text = path.read_text(encoding="utf-8")
        new_text, used, skipped = migrate(text)
        if used:
            new_text = inject_import(new_text, used)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            print(f"[OK] {path}: {len(used)} helpers used: {sorted(used)}")
        else:
            print(f"[--] {path}: no changes")
        for s in skipped:
            print(f"     {path}: {s}")


if __name__ == "__main__":
    main()
