#!/usr/bin/env python3
"""Import an existing local platform into local Terraform state; never apply it.

Run before bootstrap on a previously installed platform. Imports read Kubernetes
objects and change local Terraform state only. Partial imports can be resumed.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from importlib.util import module_from_spec, spec_from_file_location


ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location("render_platform", ROOT / "scripts/render-platform.py")
renderer = module_from_spec(spec)
spec.loader.exec_module(renderer)


def import_id(resource):
    metadata = resource["metadata"]
    fields = [f'apiVersion={resource["apiVersion"]}', f'kind={resource["kind"]}']
    if "namespace" in metadata:
        fields.append(f'namespace={metadata["namespace"]}')
    fields.append(f'name={metadata["name"]}')
    return ",".join(fields)


def installation_address(key, resource):
    kind = "kubectl_manifest" if resource["kind"] == "Secret" else "kubernetes_manifest"
    return f"{kind}.installation[{json.dumps(key)}]"


def adopt(root, resources, context, cluster, kubeconfig):
    terraform = ["terraform", f"-chdir={ROOT / 'terraform' / root}"]
    subprocess.run([*terraform, "init", "-input=false"], check=True)
    state = ROOT / "terraform" / root / "terraform.tfstate"
    tracked = set(subprocess.check_output([*terraform, "state", "list"], text=True).splitlines()) if state.exists() else set()
    if state.exists():
        state_data = json.loads(state.read_text())
        previous_context = state_data.get("outputs", {}).get("context", {}).get("value")
        if previous_context and previous_context != context:
            raise ValueError(f"{root} state belongs to {previous_context}; use separate state/checkouts")
    if any(address.startswith("kubernetes_secret_v1.installation[") for address in tracked):
        raise ValueError("Secret state migration required; see docs/terraform.md#secret-state-migration")
    for address, resource in resources:
        if address in tracked:
            print(f"Already imported: {address}", flush=True)
            continue
        present = subprocess.check_output(
            ["kubectl", "--kubeconfig", kubeconfig, "--context", context,
             "get", "--ignore-not-found", "-f", "-", "-o", "name"],
            input=json.dumps(resource), text=True,
        ).strip()
        if not present:
            print(f"Not installed; a later plan may create: {address}", flush=True)
            continue
        identifier = import_id(resource)
        if resource["kind"] == "Secret":
            identifier = "//".join((
                resource["apiVersion"], "Secret", resource["metadata"]["name"],
                resource["metadata"]["namespace"],
            ))
        subprocess.run(
            [*terraform, "import", "-input=false", f"-var=cluster_name={cluster}",
             f"-var=kubeconfig_path={kubeconfig}", address, identifier], check=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cluster", nargs="?", default="argocd-postgres")
    parser.add_argument("--kubeconfig", default="~/.kube/config")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.cluster):
        parser.error("Invalid kind cluster name")
    os.umask(0o077)
    context = "kind-" + args.cluster
    kubeconfig = str(Path(args.kubeconfig).expanduser())
    subprocess.run(["kind", "get", "nodes", "--name", args.cluster], check=True)
    subprocess.run(["kubectl", "--kubeconfig", kubeconfig, "--context", context,
                    "get", "nodes"], check=True)
    installation = renderer.render()
    namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "argocd"}}
    resources = [("kubernetes_manifest.namespace", namespace)]
    resources.extend((installation_address(key, value), value)
                     for key, value in sorted(installation.items()))
    adopt("argocd", resources, context, args.cluster, kubeconfig)
    # Convert the checked-in Application only; never import Traefik workloads.
    output = subprocess.check_output(
        ["kubectl", "patch", "--local", "-k", str(ROOT / "argocd"),
         "--type=merge", "-p", "{}", "-o", "json"], text=True,
    )
    application, = renderer.decode_resources(output)
    adopt("platform", [("kubernetes_manifest.traefik", application)],
          context, args.cluster, kubeconfig)
    print("Import complete. Review Terraform plans before applying; no cluster resources were changed.")


if __name__ == "__main__":
    main()
