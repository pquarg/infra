#!/usr/bin/env python3
"""Remove this learning deployment, recovery data and logs; optionally its backend.

Only Python's standard library is used. Run --help before use. AWS deletion is
intentionally not part of the test suite; subprocesses are replaced by fakes.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[2]
BOOTSTRAP = REPOSITORY / "bootstrap/aws"
PROJECT = "245361883993"
PROFILE = "aws-project"
REGION = "eu-north-1"
CLUSTER = "deployment-project-dev"
DATABASE = "deployment-project-dev-postgres"
STATE_BUCKET = f"deployment-project-tfstate-{PROJECT}-{REGION}"
LOG_BUCKET = f"deployment-project-cf-logs-{PROJECT}-{REGION}"
STATE_KEYS = {
    "foundation": "dev/terraform.tfstate",
    "platform": "dev/platform/terraform.tfstate",
    "edge": "dev/edge/terraform.tfstate",
}
RECORD_VERSION = 3
PLATFORM_ADDRESSES = {"helm_release.argocd", "helm_release.load_balancer_controller"}
SETTINGS = {
    "aws_db_instance.postgres": {
        "deletion_protection", "skip_final_snapshot", "delete_automated_backups",
    },
    "aws_s3_bucket.cloudfront_logs": {"force_destroy"},
}


class Failure(RuntimeError):
    pass


def managed(state):
    return [r for r in state.get("resources", []) if r.get("mode") == "managed"]


def attributes(state, resource_type):
    return [i["attributes"] for r in managed(state) if r["type"] == resource_type
            for i in r.get("instances", []) if "attributes" in i]


def edge_address(address):
    # Deliberately exclude modules, the database and the bootstrap bucket.
    return address.startswith(("aws_cloudfront_", "aws_cloudwatch_log_delivery")) or (
        address.startswith("aws_s3_bucket") and address.endswith(".cloudfront_logs")
    )


def validate_stage_states(states):
    identities = set()
    for stage, state in states.items():
        for resource in managed(state):
            address = resource.get("module", "")
            address = (address + "." if address else "") + resource["type"] + "." + resource["name"]
            expected = ("platform" if address in PLATFORM_ADDRESSES else
                        "edge" if edge_address(address) else "foundation")
            if stage != expected or address == "aws_s3_bucket.terraform_state":
                raise Failure(f"Unexpected {address} in {stage}; inspect state ownership before teardown.")
            for instance in resource.get("instances", []):
                identifier = instance.get("attributes", {}).get("id")
                if identifier:
                    identity = (resource["type"], identifier)
                    if identity in identities:
                        raise Failure(f"Duplicate managed identity: {identity}; repair ownership first.")
                    identities.add(identity)


def read_only_fields(schema):
    def computed_block(block):
        return (all(a.get("computed") and not a.get("optional") and not a.get("required")
                    for a in block.get("attributes", {}).values())
                and all(computed_block(b["block"]) for b in block.get("block_types", {}).values()))
    block = schema.get("block", {})
    return {k for k, a in block.get("attributes", {}).items()
            if a.get("computed") and not a.get("optional") and not a.get("required")} | {
                k for k, b in block.get("block_types", {}).items() if computed_block(b["block"])
            }


def check_plan(plan, phase, schemas=None):
    """Reject creation, replacement and changes outside the phase's scope."""
    for resource in plan.get("resource_changes", []):
        actions = resource["change"]["actions"]
        if actions == ["no-op"] or resource.get("mode") == "data":
            continue
        address = resource["address"]
        readonly = read_only_fields((schemas or {}).get(resource.get("type", address.split(".")[0]), {}))
        if phase == "settings" and actions == ["update"] and address in SETTINGS:
            before = resource["change"]["before"] or {}
            after = resource["change"]["after"] or {}
            changed = {key for key in before.keys() | after.keys()
                       if before.get(key) != after.get(key)}
            if changed <= SETTINGS[address] | readonly:
                continue
        if phase == "edge" and actions == ["delete"] and edge_address(address):
            continue
        if phase == "platform" and actions == ["delete"] and address in PLATFORM_ADDRESSES:
            continue
        if (phase == "foundation" and actions == ["delete"] and not edge_address(address)
                and not address.startswith("helm_release.")
                and address != "aws_s3_bucket.terraform_state"):
            continue
        if (phase == "backend-destroy" and actions == ["delete"]
                and address.startswith("aws_s3_bucket") and address.endswith(".terraform_state")):
            continue
        if (phase == "backend-settings" and actions == ["update"]
                and address == "aws_s3_bucket.terraform_state"):
            before = resource["change"]["before"] or {}
            after = resource["change"]["after"] or {}
            if {k for k in before.keys() | after.keys()
                if before.get(k) != after.get(k)} <= {"force_destroy"} | readonly:
                continue
        raise Failure(f"Refusing {phase} plan: {address} proposes {actions}. "
                      "Inspect the saved plan; no apply was run for this phase.")


def replace_setting(path, name, old, new):
    text = path.read_text()
    pattern = rf"(?m)^(\s*{re.escape(name)}\s*=\s*)({old}|{new})(\s*(?:#.*)?)$"
    updated, count = re.subn(pattern, lambda m: m[1] + new + m[3], text)
    if count != 1:
        raise Failure(f"Expected one {name} setting in {path}; found {count}.")
    path.write_text(updated)


def copy_config(source, destination, backend=True):
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.is_file() and (path.suffix in {".tf", ".tfvars"}
                               or path.name.endswith((".tf.json", ".tfvars.json"))
                               or path.name == ".terraform.lock.hcl"):
            if backend or path.name != "backend.tf":
                shutil.copy2(path, destination / path.name)
    for name in ("functions", "policies"):
        if (source / name).is_dir():
            shutil.copytree(source / name, destination / name, dirs_exist_ok=True)


class Teardown:
    def __init__(self, directory, delete_backend=False):
        self.directory = directory
        self.stages = {name: directory / "environments/aws/dev" / name for name in STATE_KEYS}
        self.foundation = self.stages["foundation"]
        self.platform = self.stages["platform"]
        self.edge = self.stages["edge"]
        self.bootstrap = directory / "bootstrap-remote"
        self.local = directory / "bootstrap-local"
        self.record_path = directory / "progress.json"
        self.schemas = None
        self.record = json.loads(self.record_path.read_text()) if self.record_path.exists() else {
            "version": RECORD_VERSION, "project": PROJECT, "completed": [],
            "state_keys": STATE_KEYS, "delete_backend": delete_backend,
        }
        if self.record.get("version") != RECORD_VERSION or self.record.get("state_keys") != STATE_KEYS:
            raise Failure("Invalid teardown record: expected the current version and stage state keys. "
                          "Resume pre-consolidation records with their original checkout/tool; see docs/teardown.md.")
        if self.record.get("delete_backend") != delete_backend:
            raise Failure("Resume must use the original --delete-backend scope.")
        if self.record.get("project") != PROJECT:
            raise Failure("Recovery record targets a different AWS project.")
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("TF_CLI_ARGS", "TF_LOG"))
                    and k not in {"TF_DATA_DIR", "TF_WORKSPACE"}}
        self.env.update(TF_IN_AUTOMATION="1", TF_INPUT="0", TF_WORKSPACE="default",
                        AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off")

    def save(self):
        temporary = self.record_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.record, indent=2) + "\n")
        temporary.replace(self.record_path)

    def run(self, command, capture=False):
        result = subprocess.run(command, env=self.env, text=True,
                                stdout=subprocess.PIPE if capture else None,
                                stderr=subprocess.PIPE if capture else None)
        if result.returncode:
            raise Failure(f"Command failed ({result.returncode}): {' '.join(command)}\n"
                          + (result.stderr or ""))
        return result.stdout or ""

    def aws(self, *args, region=REGION, absent=()):
        command = ["aws", "--profile", PROFILE, "--region", region,
                   "--no-cli-pager", "--output", "json", *args]
        result = subprocess.run(command, env=self.env, text=True, capture_output=True)
        if result.returncode:
            # A 403, expired login or connection failure is NEVER treated as absent.
            match = re.search(r"An error occurred \(([^)]+)\)", result.stderr)
            if match and match[1] in absent:
                return None
            raise Failure(f"AWS command failed: {' '.join(command)}\n{result.stderr}")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def tf(self, directory, *args, capture=False):
        return self.run(["terraform", f"-chdir={directory}", *args], capture=capture)

    def kube(self, *args, capture=True):
        return self.run(["kubectl", "--kubeconfig", str(self.directory / "kubeconfig"),
                         "--context", "teardown", "--request-timeout=60s", *args], capture)

    def step(self, name, function):
        if name not in self.record["completed"]:
            print(f"\n=== {name} ===", flush=True)
            function()
            self.record["completed"].append(name)
            self.save()

    def initialize(self):
        for name in ("functions", "policies", "argocd", "load-balancer-controller"):
            shutil.copytree(ROOT / name, self.directory / "environments/aws/dev" / name, dirs_exist_ok=True)
        shutil.copytree(REPOSITORY / "shared", self.directory / "shared", dirs_exist_ok=True)
        (self.directory / "environments/aws/dev/config").mkdir(mode=0o700, parents=True, exist_ok=True)
        if not (ROOT / "config/operator.tfvars").is_file():
            raise Failure("Prepare config/operator.tfvars before teardown; see README.md.")
        shutil.copy2(ROOT / "config/operator.tfvars", self.directory / "environments/aws/dev/config/operator.tfvars")
        for name, directory in self.stages.items():
            source = ROOT / name
            copy_config(source, directory)
            self.init_tf(directory, source)
        copy_config(BOOTSTRAP, self.bootstrap)
        self.init_tf(self.bootstrap, BOOTSTRAP)

    def init_tf(self, directory, source):
        # Reuse installed packages when available, without copying backend
        # metadata or mutating the original Terraform working directories.
        modules = source / ".terraform/modules"
        if modules.is_dir() and not (directory / ".terraform/modules").exists():
            shutil.copytree(modules, directory / ".terraform/modules")
        # The source checkout's pinned module tree is already installed. Avoid
        # Terraform 1.17's module-install evaluation of provider-dependent
        # expressions before provider initialization in the new working copy.
        options = ["-get=false"] if (directory / ".terraform/modules/modules.json").is_file() else []
        providers = source / ".terraform/providers"
        if providers.is_dir():
            installed = directory / ".terraform/providers"
            if not installed.exists():
                # Terraform 1.17 evaluates provider functions during module
                # installation. Seed the installed package tree before init,
                # not just its later plugin search path. Link individual files
                # so installer metadata/directories remain private to this run.
                def link_file(original, destination):
                    Path(destination).symlink_to(Path(original).resolve())
                    return destination
                shutil.copytree(providers, installed, copy_function=link_file)
            options.append(f"-plugin-dir={providers}")
        self.tf(directory, "init", "-input=false", "-lockfile=readonly", *options)

    def state(self, directory, filename):
        stage = next((name for name, path in self.stages.items() if path == directory), None)
        if stage:
            metadata = self.aws("s3api", "head-object", "--bucket", STATE_BUCKET,
                                "--key", STATE_KEYS[stage], "--expected-bucket-owner", PROJECT,
                                absent=("404",))
            if metadata is None:
                state = {"resources": []}
                (self.directory / filename).write_text(json.dumps(state))
                return state
            self.record.setdefault("state_versions", {})[filename] = metadata.get("VersionId")
            self.save()
        text = self.tf(directory, "state", "pull", capture=True)
        state = json.loads(text)
        (self.directory / filename).write_text(text)
        return state

    def inventory(self):
        self.aws("s3api", "head-bucket", "--bucket", STATE_BUCKET,
                 "--expected-bucket-owner", PROJECT)
        states = {name: self.state(directory, f"{name}-before.tfstate")
                  for name, directory in self.stages.items()}
        validate_stage_states(states)
        state = {"resources": [r for snapshot in states.values() for r in snapshot.get("resources", [])]}
        bootstrap = self.state(self.bootstrap, "bootstrap-before.tfstate")
        buckets = attributes(bootstrap, "aws_s3_bucket")
        if [b["bucket"] for b in buckets] != [STATE_BUCKET] or any(
            not r["type"].startswith("aws_s3_bucket") or r["name"] != "terraform_state"
            for r in managed(bootstrap)
        ):
            raise Failure("Bootstrap state is not exclusively the expected state bucket.")
        vpcs = attributes(state, "aws_vpc")
        if len(vpcs) > 1:
            raise Failure("Expected at most one deployment VPC.")
        inventory = {"vpc": vpcs[0]["id"] if vpcs else None,
                     "bootstrap_serial": bootstrap["serial"],
                     "bootstrap_lineage": bootstrap["lineage"],
                     "clusters": [r["name"] for r in attributes(state, "aws_eks_cluster")],
                     "databases": [r["identifier"] for r in attributes(state, "aws_db_instance")],
                     "distributions": [r["id"] for r in attributes(state, "aws_cloudfront_distribution")],
                     "origins": [r["id"] for r in attributes(state, "aws_cloudfront_vpc_origin")],
                     "secrets": [s["secret_arn"] for r in attributes(state, "aws_db_instance")
                                 for s in r.get("master_user_secret", [])],
                     "volumes": [], "eips": []}
        if any(c != CLUSTER for c in inventory["clusters"]) or any(
            d != DATABASE for d in inventory["databases"]
        ):
            raise Failure("State contains an unexpected cluster or database.")
        if inventory["vpc"]:
            vpc = inventory["vpc"]
            reservations = self.aws("ec2", "describe-instances", "--filters",
                                    f"Name=vpc-id,Values={vpc}")["Reservations"]
            inventory["volumes"] = [b["Ebs"]["VolumeId"] for r in reservations
                                    for i in r["Instances"] for b in i["BlockDeviceMappings"]
                                    if "Ebs" in b]
            nats = self.aws("ec2", "describe-nat-gateways", "--filter",
                            f"Name=vpc-id,Values={vpc}")["NatGateways"]
            inventory["eips"] = [a["AllocationId"] for n in nats
                                  for a in n["NatGatewayAddresses"] if "AllocationId" in a]
        cluster = self.aws("eks", "describe-cluster", "--name", CLUSTER,
                           absent=("ResourceNotFoundException",))
        if cluster:
            if CLUSTER not in inventory["clusters"]:
                raise Failure("Live cluster is not in this Terraform state.")
            # This custom AWS CLI command prints plain text even with --output=json.
            self.run(["aws", "--profile", PROFILE, "--region", REGION, "eks", "update-kubeconfig",
                      "--name", CLUSTER, "--kubeconfig", str(self.directory / "kubeconfig"),
                      "--alias", "teardown"], capture=True)
            self.kube("get", "nodes", "-o", "json")
            self.kube("get", "ingresses,services", "-A", "-o", "json")
        elif inventory["clusters"]:
            raise Failure("EKS is absent but still in state; reconcile the earlier partial teardown first.")
        self.record["inventory"] = inventory
        self.save()
        print(json.dumps(inventory, indent=2), flush=True)

    def plan_apply(self, directory, name, phase, *options):
        plan = self.directory / f"{name}.tfplan"
        self.tf(directory, "validate")
        variables = [f"-var-file={self.directory / 'environments/aws/dev/config/operator.tfvars'}"] if directory in {
            self.foundation, self.edge} else []
        self.tf(directory, "plan", "-input=false", "-lock-timeout=5m",
                f"-out={plan}", *variables, *options)
        description = json.loads(self.tf(directory, "show", "-json", str(plan), capture=True))
        if self.schemas is None:
            providers = json.loads(self.tf(directory, "providers", "schema", "-json", capture=True))
            self.schemas = providers["provider_schemas"]["registry.terraform.io/hashicorp/aws"]["resource_schemas"]
        check_plan(description, phase, self.schemas)
        self.tf(directory, "apply", "-input=false", "-lock-timeout=5m", str(plan))

    def settings(self):
        for name, old, new in (("prevent_destroy", "true", "false"),
                               ("deletion_protection", "true", "false"),
                               ("skip_final_snapshot", "false", "true"),
                               ("delete_automated_backups", "false", "true")):
            replace_setting(self.foundation / "database.tf", name, old, new)
        replace_setting(self.edge / "cloudfront-logs.tf", "force_destroy", "false", "true")
        for stage in ("foundation", "edge"):
            directory = self.stages[stage]
            state = self.state(directory, f"{stage}-current.tfstate")
            targets = [f"-target={r['type']}.{r['name']}" for r in managed(state)
                       if f"{r['type']}.{r['name']}" in SETTINGS and not r.get("module")]
            if targets:
                self.plan_apply(directory, f"{stage}-deletion-settings", "settings", *targets)

    def destroy_stage(self, stage):
        directory = self.stages[stage]
        if managed(self.state(directory, f"{stage}-before-destroy.tfstate")):
            self.plan_apply(directory, f"destroy-{stage}", stage, "-destroy")
        if managed(self.state(directory, f"{stage}-after.tfstate")):
            raise Failure(f"Managed {stage} resources remain in Terraform state.")

    def remove_edge(self):
        self.destroy_stage("edge")
        self.verify_edge()

    def verify_edge(self):
        inventory = self.record["inventory"]
        for identifier in inventory["distributions"]:
            if self.aws("cloudfront", "get-distribution", "--id", identifier,
                        region="us-east-1", absent=("NoSuchDistribution",)) is not None:
                raise Failure(f"CloudFront distribution still exists: {identifier}")
        for identifier in inventory["origins"]:
            if self.aws("cloudfront", "get-vpc-origin", "--id", identifier,
                        region="us-east-1", absent=("EntityNotFound", "NoSuchResource")) is not None:
                raise Failure(f"CloudFront VPC origin still exists: {identifier}")

    def wait(self, description, condition, seconds=1800):
        deadline = time.monotonic() + seconds
        while not condition():
            if time.monotonic() >= deadline:
                raise Failure(f"Timed out waiting for {description}. Fix the blocker and resume.")
            print(f"Waiting for {description} ...", flush=True)
            time.sleep(15)

    def remove_ingresses(self):
        if CLUSTER not in self.record["inventory"]["clusters"]:
            return
        # Stop both reconcilers rather than publishing destructive Git changes.
        controllers = json.loads(self.kube("-n", "argocd", "get",
                                           "deployments,statefulsets", "-o", "json"))["items"]
        selected = [c for c in controllers if c["metadata"]["name"] in {
            "argocd-application-controller", "argocd-applicationset-controller"}]
        if not selected:
            # Foundation-only installs may never have had a platform. Proceed only
            # when neither Kubernetes requests nor AWS load balancers need cleanup.
            resources = json.loads(self.kube("get", "ingresses,services", "-A", "-o", "json"))["items"]
            requests = [r for r in resources if r["kind"] == "Ingress"
                        or r.get("spec", {}).get("type") == "LoadBalancer"]
            if requests or not self.no_load_balancers():
                raise Failure("No Argo CD controllers but load balancer resources remain; repair cleanup first.")
            self.remove_backend_groups()
            return
        self.record.setdefault("argocd_replicas", {
            f"{c['kind'].lower()}/{c['metadata']['name']}": c["spec"].get("replicas", 1)
            for c in selected
        })
        self.save()
        for c in selected:
            kind_name = f"{c['kind'].lower()}/{c['metadata']['name']}"
            self.kube("-n", "argocd", "scale", kind_name, "--replicas=0")
            selector = ",".join(f"{k}={v}" for k, v in c["spec"]["selector"]["matchLabels"].items())
            self.wait(f"{kind_name} pods to stop", lambda: not json.loads(
                self.kube("-n", "argocd", "get", "pods", "-l", selector, "-o", "json")
            )["items"], seconds=180)
        resources = json.loads(self.kube("get", "ingresses,services", "-A", "-o", "json"))["items"]
        if not any(r["kind"] == "Ingress" or r.get("spec", {}).get("type") == "LoadBalancer"
                   for r in resources) and self.no_load_balancers():
            self.remove_backend_groups()
            return
        self.kube("-n", "kube-system", "rollout", "status",
                  "deployment/aws-load-balancer-controller", "--timeout=300s")
        for r in resources:
            if r["kind"] == "Ingress" or r.get("spec", {}).get("type") == "LoadBalancer":
                self.kube("-n", r["metadata"]["namespace"], "delete", r["kind"],
                          r["metadata"]["name"], "--ignore-not-found=true", "--wait=true",
                          "--timeout=600s")
        self.wait("controller-owned load balancers and target groups to disappear", self.no_load_balancers)
        self.remove_backend_groups()

    def no_load_balancers(self):
        vpc = self.record["inventory"]["vpc"]
        if not vpc:
            return True
        lbs = self.aws("elbv2", "describe-load-balancers")["LoadBalancers"]
        targets = self.aws("elbv2", "describe-target-groups")["TargetGroups"]
        return not any(r.get("VpcId") == vpc for r in lbs + targets)

    def remove_backend_groups(self):
        """Clean up only a detached, explicitly controller-tagged backend SG."""
        vpc = self.record["inventory"]["vpc"]
        if not vpc or not self.no_load_balancers():
            return
        groups = self.aws("ec2", "describe-security-groups", "--filters",
                          f"Name=vpc-id,Values={vpc}")["SecurityGroups"]
        for group in groups:
            tags = {t["Key"]: t["Value"] for t in group.get("Tags", [])}
            if (tags.get("elbv2.k8s.aws/cluster") != CLUSTER
                    or tags.get("elbv2.k8s.aws/resource") != "backend-sg"):
                continue
            identifier = group["GroupId"]
            interfaces = self.aws("ec2", "describe-network-interfaces", "--filters",
                                  f"Name=group-id,Values={identifier}")["NetworkInterfaces"]
            if interfaces:
                raise Failure(f"Controller backend group {identifier} still has attached interfaces.")
            rules = [r for r in self.aws("ec2", "describe-security-group-rules")["SecurityGroupRules"]
                     if r.get("ReferencedGroupInfo", {}).get("GroupId") == identifier]
            for rule in rules:
                # Never revoke unrelated rules just to force SG/VPC deletion.
                if rule.get("Description") != "elbv2.k8s.aws/targetGroupBinding=shared" or rule["IsEgress"]:
                    raise Failure(f"Unexpected reference to {identifier}: {rule['SecurityGroupRuleId']}")
                if rule["GroupId"] not in {g["GroupId"] for g in groups}:
                    raise Failure(f"External VPC references controller group {identifier}.")
                self.aws("ec2", "revoke-security-group-ingress", "--group-id", rule["GroupId"],
                         "--security-group-rule-ids", rule["SecurityGroupRuleId"])
            self.aws("ec2", "delete-security-group", "--group-id", identifier)

    def recovery_data(self):
        db = self.aws("rds", "describe-db-instances", "--db-instance-identifier", DATABASE,
                      absent=("DBInstanceNotFound",))
        if db is not None:
            raise Failure("Database still exists; refusing recovery-data deletion.")
        snapshots = self.aws("rds", "describe-db-snapshots",
                             "--db-instance-identifier", DATABASE)["DBSnapshots"]
        for snapshot in snapshots:
            identifier = snapshot["DBSnapshotIdentifier"]
            if snapshot["SnapshotType"] != "manual":
                raise Failure(f"Unexpected surviving snapshot type: {identifier}")
            if snapshot["Status"] == "deleting":
                continue
            if snapshot["Status"] != "available":
                raise Failure(f"Snapshot {identifier} is {snapshot['Status']}; wait and resume.")
            self.aws("rds", "delete-db-snapshot", "--db-snapshot-identifier", identifier)
        backups = self.aws("rds", "describe-db-instance-automated-backups")["DBInstanceAutomatedBackups"]
        for backup in backups:
            if backup["DBInstanceIdentifier"] == DATABASE:
                if backup["Status"] != "retained":
                    raise Failure("Automated backup is not retained; wait and resume.")
                self.aws("rds", "delete-db-instance-automated-backup",
                         "--db-instance-automated-backups-arn", backup["DBInstanceAutomatedBackupsArn"])
        self.wait("database snapshots and retained backups to disappear", self.no_recovery_data)

    def no_recovery_data(self):
        snapshots = self.aws("rds", "describe-db-snapshots",
                             "--db-instance-identifier", DATABASE)["DBSnapshots"]
        backups = self.aws("rds", "describe-db-instance-automated-backups")["DBInstanceAutomatedBackups"]
        return not snapshots and not any(b["DBInstanceIdentifier"] == DATABASE for b in backups)

    def verify_runtime(self):
        self.verify_edge()
        for service, action, flag, identifier, missing in (
            ("eks", "describe-cluster", "--name", CLUSTER, "ResourceNotFoundException"),
            ("rds", "describe-db-instances", "--db-instance-identifier", DATABASE, "DBInstanceNotFound"),
            ("s3api", "head-bucket", "--bucket", LOG_BUCKET, "404"),
        ):
            if self.aws(service, action, flag, identifier, absent=(missing,)) is not None:
                raise Failure(f"Resource remains: {service} {identifier}")
        inventory = self.record["inventory"]
        vpc = inventory["vpc"]
        if vpc:
            for action, key in (("describe-network-interfaces", "NetworkInterfaces"),
                                ("describe-vpcs", "Vpcs")):
                if self.aws("ec2", action, "--filters", f"Name=vpc-id,Values={vpc}")[key]:
                    raise Failure(f"VPC resources remain: {action} {vpc}")
            nats = self.aws("ec2", "describe-nat-gateways", "--filter",
                            f"Name=vpc-id,Values={vpc}")["NatGateways"]
            if any(n["State"] != "deleted" for n in nats) or not self.no_load_balancers():
                raise Failure("NAT/load balancer resources remain.")
            instances = self.aws("ec2", "describe-instances", "--filters",
                                 f"Name=vpc-id,Values={vpc}")["Reservations"]
            if any(i["State"]["Name"] != "terminated" for r in instances for i in r["Instances"]):
                raise Failure("Worker instances remain.")
        for volume in inventory["volumes"]:
            if self.aws("ec2", "describe-volumes", "--volume-ids", volume,
                        absent=("InvalidVolume.NotFound",)) is not None:
                raise Failure(f"Worker volume remains: {volume}")
        for eip in inventory["eips"]:
            if self.aws("ec2", "describe-addresses", "--allocation-ids", eip,
                        absent=("InvalidAllocationID.NotFound",)) is not None:
                raise Failure(f"Elastic IP remains: {eip}")
        for secret in inventory["secrets"]:
            if self.aws("secretsmanager", "describe-secret", "--secret-id", secret,
                        absent=("ResourceNotFoundException",)) is not None:
                raise Failure(f"RDS administrator secret remains: {secret}")
        logs = self.aws("logs", "describe-log-groups", "--log-group-name-prefix", f"/aws/eks/{CLUSTER}/")
        if logs["logGroups"] or not self.no_recovery_data():
            raise Failure("Cluster logs or database recovery data remain.")

    def local_backend(self):
        # Take a fresh snapshot before switching to an isolated LOCAL backend.
        # Never delete the bucket while the Terraform performing deletion uses S3.
        for name, directory in self.stages.items():
            if managed(self.state(directory, f"{name}-before-backend-retirement.tfstate")):
                raise Failure(f"{name} resources reappeared; refusing state backend retirement.")
        state = self.state(self.bootstrap, "bootstrap-final-remote.tfstate")
        inventory = self.record["inventory"]
        if (state["serial"] != inventory["bootstrap_serial"]
                or state["lineage"] != inventory["bootstrap_lineage"]):
            raise Failure("Bootstrap state changed during teardown; inspect concurrent operations.")
        copy_config(self.bootstrap, self.local, backend=False)
        (self.local / "terraform.tfstate").write_text(json.dumps(state))
        replace_setting(self.local / "state.tf", "prevent_destroy", "true", "false")
        replace_setting(self.local / "state.tf", "force_destroy", "false", "true")
        self.init_tf(self.local, self.bootstrap)

    def destroy_backend(self):
        self.plan_apply(self.local, "destroy-backend", "backend-destroy", "-destroy")
        if managed(self.state(self.local, "bootstrap-after.tfstate")):
            raise Failure("Managed bootstrap resources remain in local state.")

    def verify_backend(self):
        if self.aws("s3api", "head-bucket", "--bucket", STATE_BUCKET, absent=("404",)) is not None:
            raise Failure("State bucket still exists.")

    def execute(self):
        self.step("deletion settings", self.settings)
        self.step("remove CloudFront and logs", self.remove_edge)
        self.step("pause GitOps and remove load balancers", self.remove_ingresses)
        self.step("destroy platform", lambda: self.destroy_stage("platform"))
        self.step("destroy foundation", lambda: self.destroy_stage("foundation"))
        self.step("delete database recovery data", self.recovery_data)
        self.step("verify runtime removal", self.verify_runtime)
        if not self.record["delete_backend"]:
            return
        self.step("switch bootstrap to local state", self.local_backend)
        self.step("permit state bucket emptying", lambda: self.plan_apply(
            self.local, "backend-settings", "backend-settings"))
        self.step("destroy state backend", self.destroy_backend)
        self.step("verify state backend removal", self.verify_backend)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete-backend", action="store_true",
                        help="also permanently delete the versioned state backend as the final step")
    parser.add_argument("--yes", action="store_true", help="skip the initial typed project confirmation")
    parser.add_argument("--check", action="store_true", help="read-only AWS preflight; do not plan/apply or delete")
    parser.add_argument("--resume", type=Path, metavar="DIRECTORY", help="resume an interrupted run from its private record")
    args = parser.parse_args()
    if args.resume and args.check:
        parser.error("--check starts a new preflight; do not combine it with --resume")
    os.umask(0o077)
    for tool in ("terraform", "aws", "kubectl"):
        if not shutil.which(tool):
            parser.error(f"Required command is missing: {tool}")
    private = ROOT / ".teardown"
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Prevent two commands in this checkout; Terraform also locks remote state.
    with (private / "command.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure("Another teardown is running in this checkout.") from None
        directory = args.resume.resolve() if args.resume else private / str(time.time_ns())
        if args.resume and not (directory / "progress.json").is_file():
            parser.error("Resume directory must contain progress.json")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        teardown = Teardown(directory, delete_backend=args.delete_backend)
        identity = teardown.aws("sts", "get-caller-identity")
        if identity["Account"] != PROJECT:
            raise Failure(f"Wrong AWS project: {identity['Account']}; expected {PROJECT}.")
        print(f"AWS project {PROJECT}; profile {PROFILE}; Region {REGION}.\n"
              "This deletes the deployment, ALL its database backups/snapshots, access logs,\n"
              + ("and the versioned state bucket. No AWS recovery copy is retained.\n"
                 if args.delete_backend else "The versioned state backend is retained.\n") +
              f"Private local recovery record: {directory}", flush=True)
        try:
            teardown.step("initialize private configuration", teardown.initialize)
            teardown.step("record inventory", teardown.inventory)
            if args.check:
                print("Preflight passed. No AWS resources changed; no destroy was executed.")
                return
            if "verify state backend removal" in teardown.record["completed"]:
                print("This run already completed. Its recorded resources were removed.")
                return
            if not args.yes:
                if not sys.stdin.isatty():
                    raise Failure("Interactive confirmation requires a terminal; use --yes to authorize deletion.")
                if input(f"Type {PROJECT} to permanently delete all of the above: ").strip() != PROJECT:
                    raise Failure("Confirmation did not match; no deletion started.")
            teardown.execute()
            print("\nTeardown complete: recorded deployment resources, recovery data and logs removed.\n"
                  + ("State backend removed.\n" if args.delete_backend else "State backend retained.\n") +
                  "AWS service-linked roles shared by services and resources outside this deployment are not deleted.\n"
                  f"Private local records remain in {directory}; protect or securely remove them when no longer needed.")
        except (Failure, KeyboardInterrupt):
            print(f"\nStopped. Source configuration is unchanged. Argo CD may remain paused.\n"
                  f"Fix the reported blocker, then resume:\n{ROOT / 'scripts/teardown-all.py'} "
                  f"{'--delete-backend ' if args.delete_backend else ''}--resume {directory}\n"
                  "Do not run a normal Terraform apply during partial teardown.", file=sys.stderr)
            raise


if __name__ == "__main__":
    try:
        main()
    except (Failure, KeyboardInterrupt) as error:
        print(f"\n{error or 'Interrupted.'}", file=sys.stderr)
        sys.exit(1)
