#!/usr/bin/env bash
# Read-only prerequisites; always select and verify the Kubernetes target.
set -euo pipefail
stage="${1:-}"
case "$stage" in foundation|platform|edge) shift ;; *) printf 'Usage: %s foundation|platform|edge [--context CONTEXT] [--kubeconfig PATH]\n' "${0##*/}" >&2; exit 1 ;; esac
context=""
kubeconfig="${KUBECONFIG:-$HOME/.kube/config}"
while (( $# )); do
  case "$1" in
    --context|--kubeconfig)
      if (( $# < 2 )) || [[ -z "$2" || "$2" == --* ]]; then exit 1; fi
      case "$1" in --context) context="$2" ;; --kubeconfig) kubeconfig="$2" ;; esac
      shift 2 ;;
    *) printf 'Error: unknown argument %s\n' "$1" >&2; exit 1 ;;
  esac
done
aws_args=(--profile aws-project --region eu-north-1 --no-cli-pager)
account="$(aws "${aws_args[@]}" sts get-caller-identity --query Account --output text)"
[[ "$account" == 245361883993 ]] || { printf 'Error: unexpected AWS account.\n' >&2; exit 1; }
if [[ "$stage" == foundation ]]; then exit 0; fi
[[ -n "$context" && "$kubeconfig" != *:* ]] || { printf 'Error: select --context CONTEXT and one kubeconfig file.\n' >&2; exit 1; }
kube=(kubectl "--context=$context" "--kubeconfig=$kubeconfig")
endpoint="$(aws "${aws_args[@]}" eks describe-cluster --name deployment-project-dev --query cluster.endpoint --output text)"
selected="$("${kube[@]}" config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
[[ "$selected" == "$endpoint" ]] || { printf 'Error: Kubernetes context does not select the foundation EKS endpoint.\n' >&2; exit 1; }
"${kube[@]}" get nodes > /dev/null
"${kube[@]}" wait --for=condition=Ready nodes --all --timeout=300s
"${kube[@]}" -n kube-system rollout status daemonset/eks-pod-identity-agent --timeout=300s
association="$(aws "${aws_args[@]}" eks list-pod-identity-associations --cluster-name deployment-project-dev --namespace kube-system --service-account aws-load-balancer-controller --query 'associations[].associationId' --output text)"
[[ -n "$association" && "$association" != None ]] || { printf 'Error: foundation controller Pod Identity association is missing.\n' >&2; exit 1; }
if [[ "$stage" == platform ]]; then exit 0; fi
for controller in argocd-server argocd-repo-server argocd-applicationset-controller; do
  "${kube[@]}" -n argocd rollout status "deployment/$controller" --timeout=300s
done
"${kube[@]}" -n kube-system rollout status deployment/aws-load-balancer-controller --timeout=300s
lb_arn="$(aws "${aws_args[@]}" elbv2 describe-load-balancers --names deployment-project-dev-app --query 'LoadBalancers[?State.Code==`active`].LoadBalancerArn' --output text)"
[[ -n "$lb_arn" && "$lb_arn" != None ]] || { printf 'Error: app ALB must be Active before planning edge.\n' >&2; exit 1; }
targets="$(aws "${aws_args[@]}" elbv2 describe-target-groups --load-balancer-arn "$lb_arn" --query 'TargetGroups[].TargetGroupArn' --output text)"
[[ -n "$targets" && "$targets" != None ]] || { printf 'Error: app ALB has no target groups.\n' >&2; exit 1; }
for target in $targets; do
  healthy="$(aws "${aws_args[@]}" elbv2 describe-target-health --target-group-arn "$target" --query 'length(TargetHealthDescriptions[?TargetHealth.State==`healthy`])' --output text)"
  [[ "$healthy" =~ ^[1-9][0-9]*$ ]] || { printf 'Error: ALB target group has no healthy targets.\n' >&2; exit 1; }
done
