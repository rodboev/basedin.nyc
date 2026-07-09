from __future__ import annotations

import html
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


DOCS_DIR = Path(__file__).resolve().parent
REPO_ROOT = DOCS_DIR.parent
OUTPUT_PATH = DOCS_DIR / "index.html"
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(value: str) -> str:
    return html.unescape(TAG_RE.sub("", value)).strip()


def humanize_stem(stem: str) -> str:
    return stem.replace("-", " ").replace("_", " ").strip().title()


def extract_label(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    for pattern in (TITLE_RE, H1_RE):
        match = pattern.search(text)
        if match:
            label = strip_tags(match.group(1))
            if label:
                return label
    return humanize_stem(path.stem)


def git_timestamp(args: list[str]) -> datetime | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    value = result.stdout.strip().splitlines()
    if not value:
        return None

    try:
        return datetime.fromisoformat(value[0].replace("Z", "+00:00"))
    except ValueError:
        return None


def file_timestamps(path: Path) -> tuple[datetime, datetime]:
    relative_name = path.relative_to(REPO_ROOT).as_posix()
    modified = git_timestamp(["log", "-1", "--format=%aI", "--", relative_name])
    created = git_timestamp(["log", "--follow", "--diff-filter=A", "--format=%aI", "--", relative_name])

    stat = path.stat()
    fallback_modified = datetime.fromtimestamp(stat.st_mtime, tz=ZoneInfo("America/New_York"))
    birthtime = getattr(stat, "st_birthtime", None)
    if birthtime is not None:
        fallback_created = datetime.fromtimestamp(birthtime, tz=ZoneInfo("America/New_York"))
    elif os.name == "nt":
        fallback_created = datetime.fromtimestamp(stat.st_ctime, tz=ZoneInfo("America/New_York"))
    else:
        fallback_created = fallback_modified

    return (
        created or fallback_created,
        max(modified, fallback_modified) if modified else fallback_modified,
    )


def format_display_datetime(value: datetime) -> str:
    eastern = value.astimezone(ZoneInfo("America/New_York"))
    try:
        return eastern.strftime("%-m/%-d/%Y %-I:%M %p")
    except ValueError:
        return eastern.strftime("%#m/%#d/%Y %#I:%M %p")


def collect_docs() -> list[dict[str, str]]:
    entries = []
    for path in sorted(DOCS_DIR.rglob("*.html")):
        if path.resolve() == OUTPUT_PATH.resolve():
            continue
        relative_name = path.relative_to(DOCS_DIR).as_posix()
        created_at, modified_at = file_timestamps(path)
        entries.append(
            {
                "href": relative_name,
                "label": extract_label(path),
                "created_display": format_display_datetime(created_at),
                "created_sort": created_at.astimezone(ZoneInfo("UTC")).isoformat(),
                "modified_display": format_display_datetime(modified_at),
                "modified_sort": modified_at.astimezone(ZoneInfo("UTC")).isoformat(),
            }
        )
    return sorted(entries, key=lambda item: item["modified_sort"], reverse=True)


def render(entries: list[dict[str, str]]) -> str:
    try:
        generated_at = datetime.now(ZoneInfo("America/New_York")).strftime("%B %-d, %Y")
    except ValueError:
        generated_at = datetime.now(ZoneInfo("America/New_York")).strftime("%B %#d, %Y")

    if entries:
        rows_html = "\n".join(
            (
                f'    <div class="docs-file-row"'
                f' data-sort-name="{html.escape(item["label"].lower())}"'
                f' data-sort-modified="{html.escape(item["modified_sort"])}"'
                f' data-sort-created="{html.escape(item["created_sort"])}">'
                f'<a class="docs-file-label" href="{html.escape(item["href"])}">{html.escape(item["label"])}</a>'
                f'<div class="docs-file-date">{html.escape(item["modified_display"])}</div>'
                f'<div class="docs-file-date">{html.escape(item["created_display"])}</div>'
                "</div>"
            )
            for item in entries
        )
        body_html = f'  <div class="docs-file-list-body" id="docs-list-body">\n{rows_html}\n  </div>'
    else:
        body_html = '  <p class="docs-file-empty">No docs found.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="darkreader-lock" />
<meta name="color-scheme" content="light dark" />
<title>docs</title>
<link rel="stylesheet" href="../style.css">
</head>
<body class="pr doc-index">
<div class="top-row">
  <h1><a class="back-link" href="../"><svg viewBox="0 0 16 16" width="1em" height="1em"><path d="M10 2L4 8l6 6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg></a>docs</h1>
  <nav class="nav-links">
    <a href="../resume/">Resume</a>
    <span class="nav-sep">/</span>
    <a href="../projects/">Projects</a>
    <span class="nav-sep">/</span>
    <a href="../pr-stats/">Stats</a>
    <span class="nav-sep">/</span>
    <span class="current">Docs</span>
    <span class="nav-sep">/</span>
    <span class="nav-links-icons">
      <a href="https://github.com/rodboev" class="nav-link--icon" aria-label="GitHub"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg></a>
      <a href="https://linkedin.com/in/rodboev" class="nav-link--icon" aria-label="LinkedIn"><svg viewBox="0 0 24 24" aria-hidden="true"><defs><mask id="linkedin-inverted"><rect width="24" height="24" fill="white"/><path fill="black" d="M6.94 8.98H3.67V19.5h3.27V8.98zM5.31 7.54c1.05 0 1.91-.86 1.91-1.91 0-1.06-.86-1.91-1.91-1.91S3.4 4.57 3.4 5.63c0 1.05.86 1.91 1.91 1.91zM20.6 19.5h-3.27v-5.12c0-1.22-.02-2.79-1.7-2.79-1.7 0-1.96 1.33-1.96 2.7v5.21H10.4V8.98h3.14v1.44h.04c.44-.83 1.5-1.7 3.1-1.7 3.31 0 3.92 2.18 3.92 5.02v5.76z"/></mask></defs><rect width="24" height="24" rx="2.75" fill="currentColor" mask="url(#linkedin-inverted)"/></svg></a>
    </span>
  </nav>
</div>
<section class="docs-file-list" id="docs-list">
  <div class="docs-file-list-head">
    <button type="button" data-sort-key="name" data-sort-type="text">Name</button>
    <button type="button" data-sort-key="modified" data-sort-type="date" data-sort-default="desc">Date Modified</button>
    <button type="button" data-sort-key="created" data-sort-type="date">Date Created</button>
  </div>
{body_html}
</section>

<p class="footer">Generated {html.escape(generated_at)}.</p>
<script>
var docsList = document.getElementById('docs-list');
var docsBody = document.getElementById('docs-list-body');
var sortButtons = docsList ? docsList.querySelectorAll('button[data-sort-key]') : [];
var activeSort = {{ key: 'modified', direction: 'desc', type: 'date' }};

function updateSortLabels() {{
  sortButtons.forEach(function(button) {{
    var key = button.getAttribute('data-sort-key');
    var label = button.textContent.replace(/ [▲▼]$/, '');
    if (key === activeSort.key) {{
      button.textContent = label + (activeSort.direction === 'asc' ? ' ▲' : ' ▼');
    }} else {{
      button.textContent = label;
    }}
  }});
}}

function sortDocsList(key, type, direction) {{
  if (!docsBody) return;
  var sortAttr = 'data-sort-' + key;
  var rows = Array.from(docsBody.querySelectorAll('.docs-file-row'));
  rows.sort(function(a, b) {{
    var aValue = a.getAttribute(sortAttr) || '';
    var bValue = b.getAttribute(sortAttr) || '';
    var result = 0;
    if (type === 'date') {{
      result = aValue.localeCompare(bValue);
    }} else {{
      result = aValue.localeCompare(bValue, undefined, {{ sensitivity: 'base' }});
    }}
    return direction === 'asc' ? result : -result;
  }});
  rows.forEach(function(row) {{
    docsBody.appendChild(row);
  }});
  activeSort = {{ key: key, direction: direction, type: type }};
  updateSortLabels();
}}

sortButtons.forEach(function(button) {{
  button.addEventListener('click', function() {{
    var key = button.getAttribute('data-sort-key');
    var type = button.getAttribute('data-sort-type') || 'text';
    var direction = 'asc';
    if (activeSort.key === key) {{
      direction = activeSort.direction === 'asc' ? 'desc' : 'asc';
    }} else if (button.getAttribute('data-sort-default') === 'desc') {{
      direction = 'desc';
    }}
    sortDocsList(key, type, direction);
  }});
}});

sortDocsList(activeSort.key, activeSort.type, activeSort.direction);
</script>
<script src="../assets/script.js?v=20260609u"></script>
</body>
</html>
"""


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render(collect_docs()), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
