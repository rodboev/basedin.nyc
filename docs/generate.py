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
        modified or fallback_modified,
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
                "    <tr>"
                f'<td data-sort-value="{html.escape(item["label"].lower())}"><a href="{html.escape(item["href"])}">{html.escape(item["label"])}</a> <span class="dim"><code>{html.escape(item["href"])}</code></span></td>'
                f'<td data-sort-value="{html.escape(item["modified_sort"])}">{html.escape(item["modified_display"])}</td>'
                f'<td data-sort-value="{html.escape(item["created_sort"])}">{html.escape(item["created_display"])}</td>'
                "</tr>"
            )
            for item in entries
        )
    else:
        rows_html = '    <tr><td colspan="3">No docs found.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="darkreader-lock" />
<meta name="color-scheme" content="light dark" />
<title>docs</title>
<link rel="stylesheet" href="../style.css">
<style>
#docs-table th button {{
  appearance: none;
  background: none;
  border: 0;
  color: inherit;
  cursor: pointer;
  font: inherit;
  padding: 0;
}}

#docs-table td:nth-child(2),
#docs-table td:nth-child(3) {{
  white-space: nowrap;
}}
</style>
</head>
<body class="pr">
<main class="doc-page">
<div class="top-row">
  <h1><a class="back-link" href="../"><svg viewBox="0 0 16 16" width="1em" height="1em"><path d="M10 2L4 8l6 6" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg></a>docs</h1>
  <nav class="nav-links">
    <span class="current">Docs</span>
  </nav>
</div>
<table id="docs-table">
  <thead>
    <tr>
      <th><button type="button" data-sort-key="0" data-sort-type="text">Name</button></th>
      <th><button type="button" data-sort-key="1" data-sort-type="date" data-sort-default="desc">Date Modified</button></th>
      <th><button type="button" data-sort-key="2" data-sort-type="date">Date Created</button></th>
    </tr>
  </thead>
  <tbody>
{rows_html}
  </tbody>
</table>

<p class="footer">Generated {html.escape(generated_at)}.</p>
</main>
<script>
var docsTable = document.getElementById('docs-table');
var docsBody = docsTable ? docsTable.querySelector('tbody') : null;
var sortButtons = docsTable ? docsTable.querySelectorAll('button[data-sort-key]') : [];
var activeSort = {{ key: 1, direction: 'desc', type: 'date' }};

function updateSortLabels() {{
  sortButtons.forEach(function(button) {{
    var key = Number(button.getAttribute('data-sort-key'));
    var label = button.textContent.replace(/ [▲▼]$/, '');
    if (key === activeSort.key) {{
      button.textContent = label + (activeSort.direction === 'asc' ? ' ▲' : ' ▼');
    }} else {{
      button.textContent = label;
    }}
  }});
}}

function sortDocsTable(key, type, direction) {{
  if (!docsBody) return;
  var rows = Array.from(docsBody.querySelectorAll('tr'));
  rows.sort(function(a, b) {{
    var aValue = a.children[key].getAttribute('data-sort-value') || a.children[key].textContent.trim();
    var bValue = b.children[key].getAttribute('data-sort-value') || b.children[key].textContent.trim();
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
    var key = Number(button.getAttribute('data-sort-key'));
    var type = button.getAttribute('data-sort-type') || 'text';
    var direction = 'asc';
    if (activeSort.key === key) {{
      direction = activeSort.direction === 'asc' ? 'desc' : 'asc';
    }} else if (button.getAttribute('data-sort-default') === 'desc') {{
      direction = 'desc';
    }}
    sortDocsTable(key, type, direction);
  }});
}});

sortDocsTable(activeSort.key, activeSort.type, activeSort.direction);
</script>
</body>
</html>
"""


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render(collect_docs()), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
