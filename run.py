"""Create an isolated environment if needed, then start the local web editor.

Usage: python run.py [--port 8000]
Only installs dependencies when exact pinned runtime versions are missing.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Start PDF Studio on localhost")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not (3, 10) <= sys.version_info[:2] <= (3, 13):
        print("Please use Python 3.10-3.13 (tested with Python 3.13.5).", file=sys.stderr)
        return 1
    env = ROOT / ".venv"
    executable = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    try:
        if not executable.exists():
            print("Creating .venv ...", flush=True)
            venv.EnvBuilder(with_pip=True).create(env)
        check = """
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import sys
try:
    for line in Path('requirements.txt').read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            name, expected = line.split('==', 1)
            if version(name) != expected:
                sys.exit(1)
except PackageNotFoundError:
    sys.exit(1)
"""
        result = subprocess.run([str(executable), "-c", check], cwd=ROOT, check=False)
        if result.returncode:
            print("Installing pinned dependencies (internet needed the first time) ...", flush=True)
            subprocess.run([str(executable), "-m", "pip", "install", "-r", "requirements.txt"], cwd=ROOT, check=True)
        print(f"\nPDF Studio is starting. Open http://127.0.0.1:{args.port}\nPress Ctrl+C to stop.\n", flush=True)
        return subprocess.call([
            str(executable), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", str(args.port), "--workers", "1", "--no-access-log",
        ], cwd=ROOT)
    except KeyboardInterrupt:
        return 0
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Startup failed: {exc}\nSee README.md for manual setup and troubleshooting.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
