#!/usr/bin/env python
"""Command-line access to the agent, e.g.

    python scripts/run_agent.py "Monitor minx=83.15 miny=17.65 maxx=83.35 maxy=17.80 for recent change"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geoagent.db.session import init_db  # noqa: E402
from geoagent.services import chat  # noqa: E402


def main() -> None:
    query = " ".join(sys.argv[1:]) or "List the most recent monitoring runs and summarise them."
    init_db()
    session = chat.get_or_create_session(None, channel="api")
    result = chat.ask(session.id, query)
    print(result["answer"])
    if result["run_ids"]:
        print("\nruns:", ", ".join(result["run_ids"]))


if __name__ == "__main__":
    main()
