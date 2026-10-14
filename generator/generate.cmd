@echo off
setlocal
pushd "%~dp0"
if not "%errorlevel%"=="0" exit /b %errorlevel%

python generate.py %*
if not "%errorlevel%"=="0" goto done

python -c "import msvcrt, sys; print('Press P to force-push or Enter to exit...', flush=True); sys.exit(10 if sys.stdin.isatty() and msvcrt.getwch().lower() == 'p' else 0)"
rem Only the explicit P exit code permits Git changes, including after Ctrl+C.
if not "%errorlevel%"=="10" goto done

git add -- ../index.html .pr-classification-cache.json
if not "%errorlevel%"=="0" goto done
git commit --amend --no-edit
if not "%errorlevel%"=="0" goto done
git push --force
if not "%errorlevel%"=="0" goto done
python watch_deploy.py

:done
set "result=%errorlevel%"
popd
exit /b %result%
