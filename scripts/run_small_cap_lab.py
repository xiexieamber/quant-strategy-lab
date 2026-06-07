#!/usr/bin/env python3
"""启动本地小市值实验室（Streamlit）。"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "apps" / "small_cap_lab" / "app.py"

if __name__ == "__main__":
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(APP), *sys.argv[1:]],
        check=True,
    )
