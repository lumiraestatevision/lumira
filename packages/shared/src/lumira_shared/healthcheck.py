"""Container-Healthcheck ohne curl: ``python -m lumira_shared.healthcheck``.

Fragt ``http://127.0.0.1:$PORT/health/ready`` ab und beendet sich mit 0 (gesund) oder 1.
Die schlanken Service-Images enthalten kein curl – Python ist ohnehin vorhanden.
"""

from __future__ import annotations

import os
import sys
import urllib.request


def main() -> int:
    port = os.environ.get("PORT", "8000")
    path = os.environ.get("HEALTHCHECK_PATH", "/health/ready")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=4) as response:
            return 0 if response.status == 200 else 1
    except Exception as exc:
        print(f"healthcheck failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
