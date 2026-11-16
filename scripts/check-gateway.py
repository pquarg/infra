#!/usr/bin/env python3
"""Read-only, generation-aware Gateway API handoff and route checks."""
import argparse
import json
import subprocess


def condition(obj, name, conditions=None):
    conditions = obj.get("status", {}).get("conditions", []) if conditions is None else conditions
    generation = obj["metadata"]["generation"]
    if not any(c["type"] == name and c["status"] == "True"
               and c.get("observedGeneration") == generation for c in conditions):
        raise ValueError(f"{obj['kind']}/{obj['metadata']['name']}: {name} is not True for generation {generation}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=("local", "aws"), required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--namespace")
    parser.add_argument("--route")
    args = parser.parse_args()
    if (args.environment == "local") != args.context.startswith("kind-") or ":" in args.kubeconfig:
        parser.error("environment/context disagree or multiple kubeconfig files selected")
    if bool(args.route) != bool(args.namespace):
        parser.error("--route and --namespace must be supplied together")
    kube = ["kubectl", "--context=" + args.context, "--kubeconfig=" + args.kubeconfig]

    def get(kind, name, namespace=None):
        command = kube + (["-n", namespace] if namespace else []) + ["get", kind, name, "-o", "json"]
        return json.loads(subprocess.check_output(command, text=True))

    if args.route:
        if args.environment == "aws":
            gateway = get("gateway", "demo-api", args.namespace)
            condition(gateway, "Accepted")
            condition(gateway, "Programmed")
        obj = get("httproute", args.route, args.namespace)
        desired = {"name": "traefik-gateway", "namespace": "traefik", "sectionName": "web"} if args.environment == "local" else {"name": "demo-api", "sectionName": "web"}
        controller = "traefik.io/gateway-controller" if args.environment == "local" else "gateway.k8s.aws/alb"
        parents = [p for p in obj.get("status", {}).get("parents", [])
                   if p.get("controllerName") == controller
                   and all(p.get("parentRef", {}).get(k) == v for k, v in desired.items())
                   and p.get("parentRef", {}).get("namespace", args.namespace) == desired.get("namespace", args.namespace)
                   and p.get("parentRef", {}).get("group", "gateway.networking.k8s.io") == "gateway.networking.k8s.io"
                   and p.get("parentRef", {}).get("kind", "Gateway") == "Gateway"]
        if not parents:
            raise ValueError("Expected route parent status is missing")
        for parent in parents:
            condition(obj, "Accepted", parent.get("conditions", []))
            condition(obj, "ResolvedRefs", parent.get("conditions", []))
        return
    crds = ["gatewayclasses.gateway.networking.k8s.io", "gateways.gateway.networking.k8s.io", "httproutes.gateway.networking.k8s.io", "grpcroutes.gateway.networking.k8s.io", "referencegrants.gateway.networking.k8s.io", "backendtlspolicies.gateway.networking.k8s.io", "listenersets.gateway.networking.k8s.io", "tcproutes.gateway.networking.k8s.io", "tlsroutes.gateway.networking.k8s.io", "udproutes.gateway.networking.k8s.io"]
    if args.environment == "aws":
        crds += [name + ".gateway.k8s.aws" for name in ("loadbalancerconfigurations", "targetgroupconfigurations", "listenerruleconfigurations")]
    subprocess.run(kube + ["wait", "--for=condition=Established", "--timeout=120s"] + ["crd/" + c for c in crds], check=True)
    name = "traefik" if args.environment == "local" else "alb"
    subprocess.run(kube + ["wait", "--for=condition=Accepted", "gatewayclass/" + name, "--timeout=120s"], check=True)
    obj = get("gatewayclass", name)
    expected = "traefik.io/gateway-controller" if args.environment == "local" else "gateway.k8s.aws/alb"
    if obj["spec"]["controllerName"] != expected:
        raise ValueError("Unexpected GatewayClass controller; provision through infra/environments/" + ("local" if args.environment == "local" else "aws/dev") + " first")
    condition(obj, "Accepted")
    if args.environment == "local":
        subprocess.run(kube + ["-n", "traefik", "wait", "--for=condition=Programmed", "gateway/traefik-gateway", "--timeout=120s"], check=True)
        obj = get("gateway", "traefik-gateway", "traefik")
        for name in ("Accepted", "Programmed"):
            condition(obj, name)
        listeners = [v for v in obj.get("status", {}).get("listeners", []) if v["name"] == "web"]
        if not listeners:
            raise ValueError("Gateway web listener has no status")
        for listener in listeners:
            for name in ("Accepted", "ResolvedRefs", "Programmed"):
                condition(obj, name, listener.get("conditions", []))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
