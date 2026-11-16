#!/usr/bin/env python3
"""Check rendered platform ownership and local attachment restrictions."""
import json
import subprocess
import sys

resources = {}
for path in sys.argv[2:]:
    stream = subprocess.check_output(['kubectl', 'patch', '--local', '-f', path,
                                      '--type=merge', '-p', '{}', '-o', 'json'], text=True).lstrip()
    while stream:
        obj, end = json.JSONDecoder().raw_decode(stream)
        stream = stream[end:].lstrip()
        resources[obj['kind'], obj['metadata']['name']] = obj
if sys.argv[1] == 'local':
    gateway = resources['Gateway', 'traefik-gateway']
    assert gateway['metadata']['namespace'] == 'traefik'
    assert gateway['spec'] == {'gatewayClassName': 'traefik', 'listeners': [{
        'name': 'web', 'port': 8000, 'protocol': 'HTTP', 'allowedRoutes': {'namespaces': {
            'from': 'Selector', 'selector': {'matchExpressions': [{
                'key': 'kubernetes.io/metadata.name', 'operator': 'In', 'values': ['demo-api', 'job-tracker']}]}}}}]}
    assert resources['GatewayClass', 'traefik']['spec']['controllerName'] == 'traefik.io/gateway-controller'
    service = resources['Service', 'traefik']
    assert service['spec']['type'] == 'NodePort'
    assert next(p for p in service['spec']['ports'] if p['name'] == 'web')['nodePort'] == 30080
    args = resources['Deployment', 'traefik']['spec']['template']['spec']['containers'][0]['args']
    assert '--providers.kubernetesgateway' in args
    assert not any(arg.startswith('--providers.kubernetesingress') for arg in args)
    assert ('IngressClass', 'traefik') not in resources, 'Local apps now use Gateway API'
    assert not any(arg.startswith('--providers.kubernetescrd') for arg in args)
    assert '--providers.kubernetesgateway.statusaddress.ip=127.0.0.1' in args
    assert not any('statusaddress.service' in arg for arg in args)
else:
    assert resources['GatewayClass', 'alb']['spec']['controllerName'] == 'gateway.k8s.aws/alb'
    for name in ('targetgroupbindings.elbv2.k8s.aws', 'loadbalancerconfigurations.gateway.k8s.aws',
                 'targetgroupconfigurations.gateway.k8s.aws', 'listenerruleconfigurations.gateway.k8s.aws'):
        assert ('CustomResourceDefinition', name) in resources
for (kind, name), obj in resources.items():
    if kind == 'CustomResourceDefinition':
        assert obj['metadata']['annotations']['helm.sh/resource-policy'] == 'keep'
        if obj['spec']['group'] == 'gateway.networking.k8s.io':
            assert obj['metadata']['annotations']['gateway.networking.k8s.io/bundle-version'] == 'v1.6.2'
print('Gateway platform ownership and attachment contract passed.')
