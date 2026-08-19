#!/usr/bin/env python3
"""Load a mode-0600 JSON secret file and exec one pipeline stage.

The wrapper never prints values and deletes the in-process Python objects before
replacing itself.  It exists only to avoid shell quoting and command-line secret
arguments; it does not orchestrate workflow stages.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


ALLOWED = {
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_DEPOSIT_WALLET",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_API_PASSPHRASE",
}


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[2] == "":
        print("usage: run_with_polymarket_secrets.py SECRET.json COMMAND [ARG ...]", file=sys.stderr)
        return 2
    path = Path(argv[1])
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        print("secret file must not be readable or writable by group/other", file=sys.stderr)
        return 2
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != ALLOWED:
        print("secret file has an unexpected key set", file=sys.stderr)
        return 2
    environment = os.environ.copy()
    for key in sorted(ALLOWED):
        value = raw.get(key)
        if not isinstance(value, str) or not value:
            print(f"secret file value {key} is missing", file=sys.stderr)
            return 2
        environment[key] = value
    del raw
    os.execvpe(argv[2], argv[2:], environment)
    return 127


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

