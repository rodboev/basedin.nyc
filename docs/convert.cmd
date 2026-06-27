@echo off
setlocal

if "%~1"=="" (
  echo Usage: convert input.md [output.html]
  echo   Converts a Markdown file to HTML using the site stylesheet.
  exit /b 1
)

set "INPUT=%~1"
if "%~2"=="" (
  set "OUTPUT=%~dpn1.html"
) else (
  set "OUTPUT=%~2"
)

set "HEADER=%TEMP%\pandoc-header.html"
set "BEFORE=%TEMP%\pandoc-before.html"
set "AFTER=%TEMP%\pandoc-after.html"

> "%HEADER%" echo ^<meta name="darkreader-lock" /^>^<meta name="color-scheme" content="light dark" /^>
> "%BEFORE%" echo ^<main class="doc-page"^>
> "%AFTER%" (
  echo ^</main^>
  echo ^<script src="doc-nav.js"^>^</script^>
)

pandoc -s "%INPUT%" -o "%OUTPUT%" ^
  -c "../style.css?v=20260607b" ^
  -c "pandoc.css" ^
  --include-in-header="%HEADER%" ^
  --include-before-body="%BEFORE%" ^
  --include-after-body="%AFTER%" ^
  -V "classoption=pr"

if errorlevel 1 (
  echo pandoc failed.
  exit /b 1
)

REM Inject body class="pr" — pandoc doesn't support this natively
powershell -c "(Get-Content '%OUTPUT%' -Raw) -replace '<body>', '<body class=\"pr\">' | Set-Content '%OUTPUT%' -NoNewline"

del "%HEADER%" "%BEFORE%" "%AFTER%" 2>nul

echo Created %OUTPUT%
