# PR Stats

Public contribution stats page for Rod Boev. The generated landing page is `index.html`.

## Usage

Generate the site:

```cmd
python generate.py
```

That fetches Rod's current PRs from the active repos in `repos.txt`, uses `.pr-classification-cache.json`, classifies newly closed PRs when needed, updates the cache, and writes `index.html`.

Rebuild cached closed-PR classifications:

```cmd
python generate.py --classify-cache
```

The rebuild checkpoints to `.pr-classification-cache.rebuild.json` and promotes it to `.pr-classification-cache.json` only after a complete run. Default concurrency is 4 workers.

Run the rebuild with explicit concurrency:

```cmd
python generate.py --classify-cache --workers 8
```

Save rebuild output to a log:

```cmd
pwsh -NoLogo -NoProfile -c "python generate.py --classify-cache --workers 8 2>&1 | Tee-Object -FilePath C:\Users\Rod\Desktop\py-output.txt"
```

Check divergence totals:

```cmd
pwsh -NoLogo -NoProfile -c "$data = Get-Content classification-divergences.json -Raw | ConvertFrom-Json; 'count ' + $data.Count; $data | Group-Object { $_.key.Split('#')[0] } | Select-Object Name, Count; $data | Group-Object { $_.expected.classification + ' => ' + $_.actual.classification } | Select-Object Name, Count"
```

Run tests:

```cmd
python -m pytest -q
C:\Apps\Python313\Scripts\mypy.exe --strict core generate.py
```

## Files

`generate.py` is the Python entry point.

`template.html` is the reusable HTML shell. It is not the published page.

`index.html` is generated output and the published landing page.

`.pr-classification-cache.json` is the live cache.

`repos.txt` is the active repo list, one `owner/repo` per line; `#` comments deactivate entries.

`classification-divergences.json` is written during cache rebuilds to record disagreements; it is a working artifact, not tracked state.
