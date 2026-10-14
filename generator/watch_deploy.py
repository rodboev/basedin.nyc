import subprocess, sys, time

REPO = 'rodboev/basedin.nyc'

def gh(*args):
    return subprocess.run(['gh', *args, '--repo', REPO], capture_output=True, text=True).stdout.strip()

def fail(message):
    print(f'\a\n\033[41;97m DEPLOY FAILED \033[0m {message}\n', flush=True)
    if sys.stdin.isatty():
        input('Press Enter to exit...')
    sys.exit(1)

sha = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
run_id = ''
for _ in range(30):
    run_id = gh('run', 'list', '--workflow', 'deploy.yml', '--commit', sha, '--limit', '1', '--json', 'databaseId', '--jq', '.[0].databaseId')
    if run_id:
        break
    time.sleep(2)
if not run_id:
    fail(f'no deploy run started for {sha[:8]}')

print(f'Watching deploy run {run_id}...', flush=True)
if subprocess.run(['gh', 'run', 'watch', run_id, '--exit-status', '--compact', '--repo', REPO]).returncode:
    subprocess.run(['gh', 'run', 'view', run_id, '--log-failed', '--repo', REPO])
    fail(gh('run', 'view', run_id, '--json', 'url', '--jq', '.url'))
print('\033[42;97m DEPLOYED \033[0m https://basedin.nyc/', flush=True)
