#!/usr/bin/env python3
"""Gateway status responses for lifecycle command fakes."""
import json
import os
import sys
args = ' '.join(sys.argv[1:])
case = os.environ.get('CASE')
conditions = [{'type': k, 'status': 'True', 'observedGeneration': 2}
              for k in ('Accepted', 'ResolvedRefs', 'Programmed')]
if case == 'unaccepted':
    conditions[0]['status'] = 'False'
if case == 'unresolved':
    conditions[1]['status'] = 'False'
if case == 'stale':
    for c in conditions:
        c['observedGeneration'] = 1
local = '--context=kind-' in args
if 'get gatewayclass' in args:
    obj = {'kind': 'GatewayClass', 'metadata': {'name': 'traefik' if local else 'alb', 'generation': 2},
           'spec': {'controllerName': 'wrong' if case == 'class' else ('traefik.io/gateway-controller' if local else 'gateway.k8s.aws/alb')},
           'status': {'conditions': conditions}}
elif 'get httproute' in args:
    obj = {'kind': 'HTTPRoute', 'metadata': {'name': 'demo-api', 'generation': 2}, 'status': {'parents': [{
        'parentRef': {'name': 'demo-api', 'sectionName': 'web'},
        'controllerName': 'gateway.k8s.aws/alb', 'conditions': conditions}]}}
else:
    obj = {'kind': 'Gateway', 'metadata': {'name': 'traefik-gateway', 'generation': 2},
           'status': {'conditions': conditions, 'listeners': [{'name': 'web', 'conditions': conditions}]}}
print(json.dumps(obj))
