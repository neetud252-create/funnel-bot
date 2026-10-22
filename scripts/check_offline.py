"""Run isolated, offline tests; never invoke live AI or database integration tests."""
from pathlib import Path
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

root = Path(__file__).resolve().parents[1]
logs = root / 'test-results'
logs.mkdir(exist_ok=True)
env = dict(os.environ, DATABASE_URL='postgresql://unused', PYTHONIOENCODING='utf-8')
excluded = {'test_chart_live.py', 'test_chart_trial_db.py', 'test_chart_daily_db.py'}
files = [p for p in sorted(root.glob('test_*.py')) if p.name not in excluded]
if len(sys.argv) > 1:
    files = [root / name for name in sys.argv[1:]]

def check(path):
    try:
        result = subprocess.run([sys.executable, '-u', str(path)], cwd=root, env=env,
                                capture_output=True, timeout=90)
        output = result.stdout + result.stderr
        code = result.returncode
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or b'') + (error.stderr or b'') + b'\nTIMEOUT\n'
        code = 124
    (logs / (path.stem + '.log')).write_bytes(output)
    print(('PASS' if code == 0 else 'FAIL') + ' ' + path.name, flush=True)
    return code

with ThreadPoolExecutor(max_workers=3) as executor:
    results = list(executor.map(check, files))
print(f'{len(results)-sum(bool(code) for code in results)}/{len(results)} offline suites passed', flush=True)
sys.exit(any(results))
