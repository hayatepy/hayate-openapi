"""CLI: python -m hayate_openapi app:app --title X --version 1.0 [-o out.json]"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from .generate import OpenApi


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hayate_openapi",
        description="Emit the OpenAPI 3.1 document for a hayate app.",
    )
    parser.add_argument("app", help="import target, e.g. 'main:app'")
    parser.add_argument("--title", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--description")
    parser.add_argument("-o", "--output", help="write here instead of stdout")
    args = parser.parse_args(argv)

    module_name, _, attribute = args.app.partition(":")
    if not attribute:
        parser.error("app must be 'module:attribute', e.g. 'main:app'")
    sys.path.insert(0, str(Path.cwd()))
    application = getattr(importlib.import_module(module_name), attribute)

    document = OpenApi(
        application, title=args.title, version=args.version, description=args.description
    ).generate()
    text = json.dumps(document, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
