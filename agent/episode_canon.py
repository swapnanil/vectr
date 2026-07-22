"""Pure stdout/stderr digest canonicalization: cap length, keep head+tail,
collapse repeated lines, hash elided middles. Runs on TOOL OUTPUT only
(R5-sanctioned, the same category as exit-code/marker classification) — no
prompt content is ever touched here.

No config dependency: caps are passed in by the caller (`app/service.py`,
which reads them from `agent/config.py`) so this stays a plain, testable
function of its own arguments.
"""
from __future__ import annotations

import hashlib


def _collapse_repeated_lines(lines: list[str]) -> list[str]:
    """Collapse consecutive duplicate lines into one line + a `[x<N>]`
    repeat-count suffix — a build/test loop that reprints the identical
    warning hundreds of times shouldn't spend the digest budget on
    duplicates."""
    collapsed: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        j = i + 1
        while j < len(lines) and lines[j] == line:
            j += 1
        run_len = j - i
        collapsed.append(line if run_len == 1 else f"{line} [x{run_len}]")
        i = j
    return collapsed


def canonicalize_digest(
    text: str,
    max_chars: int,
    head_lines: int,
    tail_lines: int,
) -> str:
    """Canonicalize one stdout/stderr blob to at most `max_chars` characters.

    Steps: collapse consecutive duplicate lines, then — only if still over
    `max_chars` — keep the first `head_lines` and last `tail_lines` of the
    collapsed set with a hash-tagged elision marker for the dropped middle.
    A final hard char-level truncation (same head/tail shape, applied to the
    joined string) guarantees the cap even for a single pathologically long
    line that head+tail line-selection alone can't bound.

    Never raises; `""` in, `""` out.
    """
    if not text:
        return ""

    lines = text.splitlines()
    collapsed = _collapse_repeated_lines(lines)
    joined = "\n".join(collapsed)
    if len(joined) <= max_chars:
        return joined

    if len(collapsed) > head_lines + tail_lines:
        head = collapsed[:head_lines]
        tail = collapsed[len(collapsed) - tail_lines:] if tail_lines else []
        elided = collapsed[head_lines:len(collapsed) - tail_lines] if tail_lines else collapsed[head_lines:]
        elided_text = "\n".join(elided)
        digest = hashlib.sha256(elided_text.encode("utf-8", errors="replace")).hexdigest()[:12]
        marker = f"... [elided {len(elided)} lines, sha256:{digest}] ..."
        joined = "\n".join([*head, marker, *tail])

    if len(joined) <= max_chars:
        return joined

    # Still over budget (e.g. one giant unbroken line) — hard char-level
    # head+tail truncation as the final backstop.
    half = max_chars // 2
    return joined[:half] + "\n...[truncated]...\n" + joined[-half:]
