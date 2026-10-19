"""Set the console version in its three files (the first `version` field of each, nothing else)."""

import re
import sys
from pathlib import Path

version = sys.argv[1]
for path, pattern, repl in [
    ("bus-app/package.json", r'"version": "[^"]+"', f'"version": "{version}"'),
    ("bus-app/src-tauri/tauri.conf.json", r'"version": "[^"]+"', f'"version": "{version}"'),
    ("bus-app/src-tauri/Cargo.toml", r'(?m)^version = "[^"]+"', f'version = "{version}"'),
]:
    p = Path(path)
    p.write_text(re.sub(pattern, repl, p.read_text(), count=1))
