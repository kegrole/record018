"""Run the mock portal standalone: python -m mock_portal [port]"""

import json
import sys
from pathlib import Path

from .server import PortalServer


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    config = json.loads((Path(__file__).parent / "portal.json").read_text(encoding="utf-8"))
    server = PortalServer(config)
    server.httpd.server_address = ("127.0.0.1", port)
    print(f"mock portal listening on http://127.0.0.1:{port}")
    try:
        server.httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

