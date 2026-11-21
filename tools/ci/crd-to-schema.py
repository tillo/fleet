#!/usr/bin/env python3
"""Turn a live CRD into the strict JSON schema kubeconform wants, under tools/ci/schemas/.

    kubectl get crd <name> -o json | tools/ci/crd-to-schema.py

Used when the datree catalog lags the CRD we actually run (VolSync 0.17's
restic.exclude, 2026-11-11). It mirrors the catalog's own generator: every object
with `properties` gets `additionalProperties: false` unless it declares its own,
and `x-kubernetes-int-or-string` becomes string|integer. Writes one file per served
version, named the way kubeconform's {{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}
template expects, and prints the paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).with_name("schemas")


def strict(node):
    """Recursively close every object that lists properties; leave free-form maps open."""
    if isinstance(node, list):
        return [strict(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: strict(v) for k, v in node.items()}
    if out.pop("x-kubernetes-int-or-string", False):
        out.pop("type", None)
        out["oneOf"] = [{"type": "string"}, {"type": "integer"}]
    if "properties" in out and "additionalProperties" not in out:
        out["additionalProperties"] = False
    return out


def schemas(crd: dict) -> dict[str, dict]:
    """{relative path: schema} for every served version of the CRD."""
    spec = crd["spec"]
    group, kind = spec["group"], spec["names"]["kind"].lower()
    return {
        f"{group}/{kind}_{ver['name']}.json": strict(ver["schema"]["openAPIV3Schema"])
        for ver in spec["versions"] if ver.get("served")
    }


def main() -> int:
    for rel, schema in schemas(json.load(sys.stdin)).items():
        path = OUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(schema, indent=1, sort_keys=True) + "\n")
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
