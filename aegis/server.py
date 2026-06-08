from __future__ import annotations

import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def serve(directory: Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    directory = directory.resolve()
    if not directory.exists():
        raise SystemExit(f"目录不存在：{directory}")
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Serving {directory} at http://{host}:{port}/")
    httpd.serve_forever()
