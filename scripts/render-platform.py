#!/usr/bin/env python3
"""Render the pinned Argo CD installation for Terraform, without a cluster."""

import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def decode_resources(source):
    """kubectl --local emits a stream of JSON objects, sometimes a List."""
    decoder = json.JSONDecoder()
    remaining = source.lstrip()
    resources = []
    while remaining:
        resource, offset = decoder.raw_decode(remaining)
        resources.extend(resource["items"] if resource.get("kind") == "List" else [resource])
        remaining = remaining[offset:].lstrip()
    return resources


def resource_key(resource):
    metadata = resource["metadata"]
    return "/".join((resource["apiVersion"], resource["kind"],
                     metadata.get("namespace", "_cluster"), metadata["name"]))


def installation(resources):
    result = {}
    namespaces = []
    for resource in resources:
        if resource["kind"] == "Namespace":
            namespaces.append(resource["metadata"]["name"])
            continue
        namespace = resource["metadata"].get("namespace")
        if namespace is not None and namespace != "argocd":
            raise ValueError(f"Unexpected installation namespace: {namespace}")
        key = resource_key(resource)
        if key in result:
            raise ValueError(f"Duplicate installation resource: {key}")
        result[key] = resource
    if namespaces != ["argocd"] or not result:
        raise ValueError("Expected the argocd namespace and a nonempty installation")
    return result


def render():
    # The empty local patch converts Kustomize output to JSON, with no API calls.
    output = subprocess.check_output(
        ["kubectl", "patch", "--local", "-k", str(ROOT / "argocd/install"),
         "--type=merge", "-p", "{}", "-o", "json"], text=True,
    )
    resources = installation(decode_resources(output))
    destination = ROOT / ".generated/argocd-install.json"
    destination.parent.mkdir(exist_ok=True)
    # Never leave a partial manifest map that could change Terraform's for_each.
    with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(resources, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.close()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"Rendered {len(resources)} Argo CD resources into {destination.relative_to(ROOT)}")
    return resources


if __name__ == "__main__":
    render()
