#!/usr/bin/env python3
"""No entry rules. This process does not watch and does not send.

Orders, when something else calls them, go through
apps/tradovate/place_struct40.py: 5 MNQZ6, stop 20, target 40, demo only.
"""
from __future__ import annotations

import json
import time

SYMBOL = "MNQZ6"


def main() -> None:
    print(json.dumps({
        "ts": int(time.time() * 1000),
        "event": "seven_start",
        "fire": False,
        "note": "blank",
        "symbol": SYMBOL,
        "book": {"qty": 5, "stop": 20.0, "tp": 40.0, "symbol": SYMBOL},
        "fire_mode": "off",
    }), flush=True)


if __name__ == "__main__":
    main()
