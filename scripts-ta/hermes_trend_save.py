"""Shared merge-on-write for the trend jobs' local report files.

The bug this fixes (found 2026-07-30):

`build_report()` filters every fetched item through `SeenUrls` and persists
`seen.save()`. So a SECOND run on the same day sees almost nothing new. But
`save_local()` wrote to a filename keyed on today's date with mode "w", so the last
run of the day won — and thanks to dedup the last run is always the emptiest.
`market-trends` ran 3x on 2026-07-26 and ended with a 0-item report; the same happened
to `market-intelligence` on 07-28.

Overwriting is wrong, but so is "skip if thinner": each run's items are the ones that
were new *to that run*, so run 1 and run 2 hold DIFFERENT items. Measured across the 8
damaged days, the later run contributed up to 14 items the earlier one lacked. The day's
true report is the UNION.

So: merge by section, preserving first-seen order, de-duplicating by item line. Idempotent
— re-running merges to the same content.
"""
from __future__ import annotations

import os
import re

ITEM_RE = re.compile(r"^\s*[-*]\s+\[", re.M)   # re.M: these are line matches
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _parse(body: str):
    """-> (preamble_lines, [(heading, [lines])]) keeping order."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in body.splitlines():
        if HEAD_RE.match(line) and line.lstrip().startswith("##"):
            current = (line.rstrip(), [])
            sections.append(current)
            continue
        (current[1] if current else preamble).append(line.rstrip())
    return preamble, sections


def merge_bodies(old_body: str, new_body: str) -> str:
    """Union of two same-day reports. Sections and items keep first-seen order."""
    old_pre, old_secs = _parse(old_body)
    _, new_secs = _parse(new_body)

    merged: list[tuple[str, list[str]]] = [(h, list(ls)) for h, ls in old_secs]
    index = {h: i for i, (h, _) in enumerate(merged)}

    for head, lines in new_secs:
        if head in index:
            target = merged[index[head]][1]
            # Drop trailing blanks so appended items sit flush under the existing
            # list rather than after a gap.
            while target and not target[-1].strip():
                target.pop()
            seen = {l.strip() for l in target if l.strip()}
            for l in lines:
                if l.strip() and l.strip() not in seen:
                    target.append(l)
                    seen.add(l.strip())
        else:
            index[head] = len(merged)
            merged.append((head, list(lines)))

    out: list[str] = [l for l in old_pre]
    for head, lines in merged:
        if out and out[-1].strip():
            out.append("")
        out.append(head)
        # drop leading blanks, collapse runs of blanks
        cleaned: list[str] = []
        for l in lines:
            if not l.strip() and (not cleaned or not cleaned[-1].strip()):
                continue
            cleaned.append(l)
        out.extend(cleaned)
    return "\n".join(out).rstrip() + "\n"


def count_items(text: str) -> int:
    return len(ITEM_RE.findall(text)) if text else 0


def save_report(path, frontmatter: str, report: str) -> str:
    """Write the report, merging with an existing same-day file rather than clobbering.

    Returns a human-readable status string, as the previous save_local() did.
    """
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new_items = count_items(report)

    if os.path.exists(path):
        try:
            existing = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            existing = ""
        old_body = re.sub(r"\A---\n.*?\n---\n", "", existing, flags=re.S)
        old_items = count_items(old_body)
        merged = merge_bodies(old_body.strip(), report.strip())
        total = count_items(merged)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(frontmatter + merged)
        if total == old_items and new_items <= old_items:
            return f"Saved locally (no new items; kept {old_items}): {path}"
        return (f"Saved locally (merged same-day run: {old_items} + {new_items} new "
                f"-> {total} unique): {path}")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(frontmatter + report)
    return f"Saved locally: {path}"
