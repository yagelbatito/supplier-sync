"""Quick per-supplier summary of a sync log."""
import re
import sys
from pathlib import Path

log_path = Path(sys.argv[1])
log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

start_re = re.compile(r"Starting supplier: (.+?) \[(.+?)\]")
sections = []
current = None
for line in log_lines:
    m = start_re.search(line)
    if m:
        if current:
            sections.append(current)
        current = {"name": m.group(1), "key": m.group(2), "lines": []}
    if current:
        current["lines"].append(line)
if current:
    sections.append(current)

last_per_key = {}
for s in sections:
    last_per_key[s["key"]] = s

for key, s in last_per_key.items():
    body = "\n".join(s["lines"])
    print(f"=== {s['name']} [{key}] - {len(s['lines'])} log lines ===")
    print(f"  Updated:        {len(re.findall(r'Updated:', body))}")
    print(f"  Created:        {len(re.findall(r'Created:', body))}")
    print(f"  Disappeared->Draft: {len(re.findall(r'Product disappeared', body))}")
    print(f"  Failed/Error:   {len(re.findall(r'FAILED|Failed:', body))}")
    warnings = [l for l in s["lines"]
                if re.search(r"(?i)preload|page \d+ failed|forbidden|500 error|503|maxretry|connectionerror|timeout", l)]
    if warnings:
        print(f"  Scrape warnings ({len(warnings)}):")
        for w in warnings[:8]:
            print(f"    {w[:200]}")
    print()
