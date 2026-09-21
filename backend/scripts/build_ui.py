#!/usr/bin/env python
"""Build the dashboard and place it where the API serves it from.

    python scripts/build_ui.py

Runs `npm install` (first time only) and `npm run build` in ../frontend, then
copies the static export into backend/webui/. After this one uvicorn process
serves the API and the UI together.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
FRONTEND = BACKEND.parent / "frontend"
TARGET = BACKEND / "webui"
NPM = "npm.cmd" if sys.platform == "win32" else "npm"


def run(*args: str) -> None:
    print(f"$ {' '.join(args)}")
    subprocess.run(args, cwd=FRONTEND, check=True, shell=(sys.platform == "win32"))


def main() -> None:
    if not FRONTEND.exists():
        sys.exit(f"frontend folder not found at {FRONTEND}")
    if not shutil.which(NPM) and sys.platform != "win32":
        sys.exit("npm not found — install Node.js 20 or newer")

    if not (FRONTEND / "node_modules").exists():
        run(NPM, "install")
    run(NPM, "run", "build")

    export = FRONTEND / "out"
    if not (export / "index.html").exists():
        sys.exit(f"export missing at {export} — check the build output above")

    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(export, TARGET)
    print(f"\nUI ready at {TARGET}")
    print("Start everything with:  uvicorn geoagent.main:app --port 8000")


if __name__ == "__main__":
    main()
