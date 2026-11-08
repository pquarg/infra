# AWS Load Balancer Controller policy

`aws-load-balancer-controller-v3.6.2.json` is the official policy for controller v3.6.2:

https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v3.6.2/docs/install/iam_policy.json

SHA-256: `16f232c9d9f79366fe949c4550ad517a202380058a9e48d45a4e215044a20a6a`

Keep this public policy in Git alongside the Terraform code. When upgrading the
controller, review the matching release policy and update the versioned file,
Terraform reference, chart version and checksum together.

The role's trust policy restricts use to this EKS cluster and the
`aws-load-balancer-controller` service account in `kube-system`. EKS Pod Identity
provides temporary credentials; no access keys are stored in this repository.
