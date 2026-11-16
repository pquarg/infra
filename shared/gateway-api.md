# Gateway API platform lifecycle

The shared Standard bundle is Gateway API **v1.6.2**, used by both environments.
Traefik chart **42.0.0** runs **v3.7.14** and documents this patch; AWS controller
chart/controller **3.6.2** targets the compatible **v1.6.0** API minor. This is a
configuration compatibility choice, not a claim of AWS runtime conformance.
Controllers, providers and Terraform resource identities remain pinned.

## Sources and one owner per definition

- `gateway-api-crds/files/standard-install.yaml` is the unmodified
  [v1.6.2 Standard bundle](https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.2/standard-install.yaml),
  including its upstream safe-upgrade admission policy and binding.
- `aws-controller-crds/files/{crds,gateway-crds}.yaml` are the unmodified files
  bundled by [AWS controller chart 3.6.2](https://aws.github.io/eks-charts).
  That chart includes both traditional controller and Gateway CRDs. The dedicated
  release owns all of them, including TargetGroupBinding; the controller has
  `skip_crds = true`. Nothing depends on install-only `crds/` upgrades.
- Traefik's chart has Gateway definitions in its dependencies; `skip_crds = true`
  prevents a second owner. Its own CRD provider stays disabled.
- Each bundle includes its Apache 2.0 upstream LICENSE and source SHA256SUMS.
  Templates only add `helm.sh/resource-policy: keep` to CRDs. This retains CRDs
  and their resources when a platform release is uninstalled; it is not an
  automatic state-recovery mechanism.

| Environment | Terraform release address | Helm identity | Owns |
| --- | --- | --- | --- |
| Both | `helm_release.gateway_api_crds` | `gateway-system/gateway-api-crds` | Standard definitions and upgrade guard |
| AWS | `helm_release.aws_controller_crds` | `kube-system/aws-controller-crds` | All AWS chart CRDs |
| AWS | `helm_release.alb_gateway_class` | `kube-system/alb-gateway-class` | GatewayClass `alb` |
| Local | Existing `helm_release.traefik` | `traefik/traefik` | Controller, GatewayClass `traefik`, Gateway `traefik-gateway` |

CRDs are normal Helm templates, so reviewed Helm upgrades update their schemas.
They precede the controller releases through Terraform `depends_on`; AWS's class
release follows both CRD releases and the controller. No Terraform Kubernetes
provider or plan-time custom-resource lookup is introduced. Helm waits for CRDs
to become Established. Local bootstrap additionally waits for required CRDs,
controller rollouts, class acceptance and Gateway/listener readiness after apply.
AWS readiness is checked before registration and again before edge planning.

## Install or upgrade without changing the cluster

From `infra`, run the public planner, review its complete plan, and apply that
saved plan separately:

```sh
./scripts/plan.sh --environment local --stage platform --context kind-deployment
terraform -chdir=environments/local/platform apply platform.tfplan
./environments/local/scripts/check-ingress-kind.sh
```

The historical `check-ingress-kind.sh` entry point now verifies Gateway routing
and the unchanged NodePort/host mapping. It still supports existing callers.
The local state guard permits the new CRD address and refuses existing standard
CRDs or Helm records without a verified state binding. It does not take ownership.
Existing cluster, mounts, Secrets, databases, images and controller bindings stay
in place. An existing-state plan should add only the CRD release and update
Traefik in place; unexpected changes require review before apply.

A fresh cluster uses the same full platform apply. No targeted install is part
of this design. Fresh-install execution must be verified on a separately scoped
test cluster before claiming that acceptance criterion; rendering and a plan
against an existing cluster do not demonstrate it. Do not rebuild the working
cluster to perform that test.

Before changing a CRD pin, review upstream compatibility, stored versions,
conversion requirements and the safe-upgrade policy. Replace the vendored bundle,
update checksums and chart version, regenerate schemas, and validate both consumers.
Downgrading CRDs or disabling the upgrade guard needs its own reviewed procedure.
Never delete CRDs as a routing rollback: deletion removes every instance.

```sh
python3 scripts/update-gateway-schemas.py --output shared/gateway-schemas
python3 scripts/update-gateway-schemas.py --output ../gitops/schemas
./scripts/validate.sh --all
```

Schemas derive from served versions in these pinned definitions, with closed
object properties where the CRD declares properties and does not allow arbitrary
fields. GitOps carries its own schema copies and does not need an infra checkout
in Argo CD. Unknown kinds still fail validation. Upstream CRD definitions are
checksum-checked rather than silently fetching a different revision. Kubeconform
skips only the CRD definitions themselves because its registry lacks that schema;
other rendered resources, including the upgrade guard, are schema checked.

## Local transition and rollback

Ingress support was retained while app routes were published and verified.
Both local app cutovers have passed; the final configuration disables the unused
Ingress provider and class. Apply that change only after reviewing the full plan
and confirming the cluster has no Ingresses.
Follow the companion GitOps `docs/gateway-api.md`: smoke-test a distinct hostname,
then demo-api/Mailpit, then job-tracker. Local app sync can rerun migration hooks.
The completed cutover checked route conditions, original hosts, login/session
continuity, password reset, tracker data/parsing, path precedence and namespace
attachment denial. The tracker uses an Exact `/api` frontend rule to preserve the
old Ingress behavior; `/api/` and nested paths select its API.

For this or a future cutover, after all HTTPRoutes pass status and app journeys,
confirm `kubectl --context kind-deployment get ingress -A` is empty. Only then
set `providers.kubernetesIngress.enabled: false` and `ingressClass.enabled: false`,
validate, review another full local platform plan and apply separately.

Before that cleanup, restore an app's previous Ingress through a published GitOps
revert, prune the replaced HTTPRoute and verify the app. After cleanup, restore
Ingress support through Terraform first. Retain Standard CRDs throughout rollback.
Restore missing verified Helm bindings using the state recovery runbook; never
recreate a cluster to resolve an ownership error.

## AWS first deployment and deletion

AWS has no application/infrastructure deployment to migrate. The setup order is
backend → foundation → platform/CRDs → credentials and manual app sync → accepted
Gateway/routes and Active healthy ALB → edge → public checks. Replace the historic
frontend SG in GitOps with the new foundation output before sync. Keep the
internal IPv4 ALB name `dev-demo-api`, Pod IP targets and `/ready` HTTP 200 checks.
Gateway/HTTPRoute and both AWS configurations share wave 2 after healthy workload
wave 1; Argo CD v3.5.6 has built-in Gateway/HTTPRoute health handling, so no shared
health override is needed to force a Gateway healthy before its route exists.

Teardown removes edge first, pauses both GitOps reconcilers, and removes Gateways
(and legacy Ingress/LoadBalancer Service requests) while the AWS controller runs.
It waits for Kubernetes deletion/finalizers and AWS ALBs/target groups before
platform and foundation removal. An absent Gateway CRD is tolerated; forbidden
or unreachable API errors abort cleanup. Finalizers are never stripped.

The existing checkpoint `pause GitOps and remove load balancers` and record
version 3 remain compatible. The historical method name `remove_ingresses`
now includes Gateways. Existing runs must continue with their matching private
configuration/tool revision as required by the teardown runbook; do not swap
checkouts during a partial run. Private `.handoff/`, state and progress records
are preserved. Only mocked teardown tests belong to this implementation task.
