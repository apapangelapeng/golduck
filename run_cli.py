"""Local CLI harness."""

from __future__ import annotations

import argparse
import json
from typing import Any

import runner_core


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wasm", help="path to the wasm artifact")
    parser.add_argument("--seed-hex", default="00" * 16)
    parser.add_argument("--team-id", type=int)
    parser.add_argument("--enabled-stages", type=int)
    args = parser.parse_args()
    with open(args.wasm, "rb") as f:
        wasm_bytes = f.read()
    request: dict[str, Any] = {}
    request["artifact_b64"] = wasm_bytes
    request["seed_hex"] = bytes.fromhex(args.seed_hex)
    request["team_id"] = args.team_id
    request["enabled_stages"] = args.enabled_stages
    result = runner_core.run_per_team(request)
    print(json.dumps(result, indent=2, sort_keys=True, default=_json_default))
    return 0


def _json_default(o: object) -> str:
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    return str(o)


if __name__ == "__main__":
    raise SystemExit(main())
