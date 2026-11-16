#!/usr/bin/env python3
"""Extract pinned kubeconform schemas from vendored CRDs; requires kubectl."""
import argparse
import json
from pathlib import Path
import subprocess


def strict(schema):
    if isinstance(schema, dict):
        if "properties" in schema and not schema.get("x-kubernetes-preserve-unknown-fields"):
            schema.setdefault("additionalProperties", False)
        for value in schema.values():
            strict(value)
    elif isinstance(schema, list):
        for value in schema:
            strict(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    shared = Path(__file__).resolve().parents[1] / "shared"
    for path in sorted(shared.glob("*-crds/files/*.yaml")):
        stream = subprocess.check_output(["kubectl", "patch", "--local", "-f", str(path), "--type=merge", "-p", "{}", "-o", "json"], text=True).lstrip()
        while stream:
            obj, end = json.JSONDecoder().raw_decode(stream)
            stream = stream[end:].lstrip()
            if obj["kind"] != "CustomResourceDefinition":
                continue
            for version in obj["spec"]["versions"]:
                if not version["served"]:
                    continue
                schema = version["schema"]["openAPIV3Schema"]
                strict(schema)
                schema["properties"]["apiVersion"] = {"enum": [obj["spec"]["group"] + "/" + version["name"]]}
                schema["properties"]["kind"] = {"enum": [obj["spec"]["names"]["kind"]]}
                target = args.output / obj["spec"]["group"] / (obj["spec"]["names"]["kind"].lower() + "_" + version["name"] + ".json")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(schema, indent=2) + "\n")


if __name__ == "__main__":
    main()
