import subprocess
import sys

want = sys.argv[1]
out = subprocess.run(
    ["git", "tag", "-l", want], capture_output=True, text=True
).stdout.strip()
sys.exit(0 if out == want else 1)
