"""Sample a subprocess tree from its supervisor, without third-party dependencies."""

import os
import subprocess
import sys
from pathlib import Path


def tree_rss_bytes(root: int) -> int:
    rows = []
    if sys.platform == "linux":
        for path in Path("/proc").iterdir():
            if not path.name.isdecimal():
                continue
            try:
                parent = int((path / "stat").read_text().rsplit(")", 1)[1].split()[1])
                rss = int((path / "statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
                rows.append((int(path.name), parent, rss))
            except (OSError, ValueError, IndexError):
                continue
    elif sys.platform == "darwin":
        output = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True, check=True
        ).stdout
        rows = [
            (int(pid), int(parent), int(rss) * 1024)
            for pid, parent, rss in (line.split() for line in output.splitlines())
        ]
    else:
        raise RuntimeError("resource_monitor_unavailable")
    descendants = {root}
    while True:
        found = {pid for pid, parent, _ in rows if parent in descendants}
        if found <= descendants:
            break
        descendants |= found
    return sum(rss for pid, _, rss in rows if pid in descendants)
