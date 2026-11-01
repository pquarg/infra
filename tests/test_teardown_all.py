"""Offline regression checks: no test invokes AWS, Kubernetes or Terraform."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "teardown", Path(__file__).resolve().parents[1] / "scripts/teardown-all.py")
teardown = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(teardown)


def change(address, actions, before=None, after=None, mode="managed"):
    return {"address": address, "mode": mode, "change": {
        "actions": actions, "before": before, "after": after,
    }}


def state_resource(kind, name, attrs):
    return {"mode": "managed", "type": kind, "name": name,
            "instances": [{"attributes": attrs}]}


class PlanTests(unittest.TestCase):
    def test_creation_or_replacement_never_passes_any_phase(self):
        for phase in ("settings", "edge", "destroy", "backend-settings"):
            for actions in (["create"], ["delete", "create"], ["create", "delete"]):
                with self.subTest(phase=phase, actions=actions), self.assertRaises(teardown.Failure):
                    teardown.check_plan({"resource_changes": [change(
                        "aws_cloudfront_function.ip_allowlist", actions)]}, phase)

    def test_settings_allow_only_the_three_database_flags_and_log_force_destroy(self):
        for address, keys in teardown.SETTINGS.items():
            for key in keys:
                teardown.check_plan({"resource_changes": [change(
                    address, ["update"], {key: False}, {key: True})]}, "settings")
        with self.assertRaises(teardown.Failure):
            teardown.check_plan({"resource_changes": [change(
                "aws_db_instance.postgres", ["update"],
                {"engine_version": "17.10"}, {"engine_version": "17.11"})]}, "settings")

    def test_edge_phase_cannot_delete_platform_or_backend(self):
        for address in ("module.eks.aws_eks_cluster.this[0]", "aws_db_instance.postgres",
                        "aws_s3_bucket.terraform_state", "module.vpc.aws_vpc.this[0]"):
            with self.subTest(address=address), self.assertRaises(teardown.Failure):
                teardown.check_plan({"resource_changes": [change(address, ["delete"])]}, "edge")

    def test_edge_phase_includes_both_functions_logging_and_origin(self):
        for address in ("aws_cloudfront_function.ip_allowlist", "aws_cloudfront_function.https_redirects",
                        "aws_cloudfront_vpc_origin.myfastapiapp", "aws_cloudfront_distribution.myfastapiapp",
                        "aws_cloudwatch_log_delivery.cloudfront", "aws_s3_bucket_policy.cloudfront_logs"):
            teardown.check_plan({"resource_changes": [change(address, ["delete"])]}, "edge")

    def test_destroy_rejects_updates(self):
        with self.assertRaises(teardown.Failure):
            teardown.check_plan({"resource_changes": [change(
                "aws_db_instance.postgres", ["update"])]}, "destroy")

    def test_computed_database_outputs_can_change_but_configurable_fields_cannot(self):
        schema = {"aws_db_instance": {"block": {"attributes": {
            "latest_restorable_time": {"computed": True},
            "engine_version": {"computed": True, "optional": True},
        }}}}
        teardown.check_plan({"resource_changes": [change("aws_db_instance.postgres", ["update"],
            {"deletion_protection": True, "latest_restorable_time": "old"},
            {"deletion_protection": False, "latest_restorable_time": None})]}, "settings", schema)
        with self.assertRaises(teardown.Failure):
            teardown.check_plan({"resource_changes": [change("aws_db_instance.postgres", ["update"],
                {"engine_version": "17.10"}, {"engine_version": None})]}, "settings", schema)

    def test_backend_settings_cannot_modify_other_bucket_properties(self):
        teardown.check_plan({"resource_changes": [change("aws_s3_bucket.terraform_state", ["update"],
                            {"force_destroy": False}, {"force_destroy": True})]}, "backend-settings")
        with self.assertRaises(teardown.Failure):
            teardown.check_plan({"resource_changes": [change("aws_s3_bucket.terraform_state", ["update"],
                                {"bucket": "original"}, {"bucket": "other"})]}, "backend-settings")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.job = teardown.Teardown(self.directory)

    def test_aws_access_errors_are_not_absence(self):
        for code in ("AccessDenied", "ExpiredToken", "RequestExpired", "403"):
            result = subprocess.CompletedProcess([], 255, "", f"An error occurred ({code}) when calling HeadBucket")
            with patch.object(teardown.subprocess, "run", return_value=result), self.assertRaises(teardown.Failure):
                self.job.aws("s3api", "head-bucket", "--bucket", teardown.LOG_BUCKET, absent=("404",))

    def test_only_explicit_service_not_found_is_absence(self):
        result = subprocess.CompletedProcess([], 254, "", "An error occurred (404) when calling HeadBucket")
        with patch.object(teardown.subprocess, "run", return_value=result) as run:
            self.assertIsNone(self.job.aws("s3api", "head-bucket", absent=("404",)))
        self.assertIn(teardown.PROFILE, run.call_args.args[0])
        self.assertIn(teardown.REGION, run.call_args.args[0])

    def test_environment_cannot_inject_terraform_arguments_or_a_different_workspace(self):
        with patch.dict(teardown.os.environ, {
            "TF_CLI_ARGS_plan": "-target=wrong", "TF_WORKSPACE": "prod", "TF_DATA_DIR": "/other",
        }):
            job = teardown.Teardown(self.directory)
        self.assertNotIn("TF_CLI_ARGS_plan", job.env)
        self.assertNotIn("TF_DATA_DIR", job.env)
        self.assertEqual(job.env["TF_WORKSPACE"], "default")

    def test_rejected_saved_plan_is_never_applied(self):
        description = {"resource_changes": [change("module.eks.aws_eks_cluster.this[0]", ["create"])]}
        self.job.schemas = {}
        self.job.tf = Mock(side_effect=["", "", json.dumps(description)])
        with self.assertRaises(teardown.Failure):
            self.job.plan_apply(self.directory, "edge", "edge")
        self.assertFalse(any("apply" in call.args for call in self.job.tf.call_args_list))

    def test_edge_config_removed_before_plan_and_cluster_kept_alive(self):
        self.job.runtime.mkdir()
        for filename in teardown.EDGE_FILES:
            (self.job.runtime / filename).write_text("edge")
        (self.job.runtime / "eks.tf").write_text("cluster")
        self.job.state = Mock(return_value={"resources": [state_resource(
            "aws_cloudfront_function", "ip_allowlist", {})]})
        def inspect(*args):
            self.assertFalse(any((self.job.runtime / f).exists() for f in teardown.EDGE_FILES))
            self.assertTrue((self.job.runtime / "eks.tf").exists())
        self.job.plan_apply = Mock(side_effect=inspect)
        self.job.verify_edge = Mock()
        self.job.remove_edge()
        self.job.plan_apply.assert_called_once()

    def test_empty_runtime_does_not_generate_normal_creation_plan(self):
        self.job.runtime.mkdir()
        self.job.state = Mock(return_value={"resources": []})
        self.job.plan_apply = Mock()
        self.job.verify_edge = Mock()
        self.job.remove_edge()
        self.job.plan_apply.assert_not_called()

    def test_complete_workflow_and_resume_order(self):
        methods = ("settings", "remove_edge", "remove_ingresses", "destroy_runtime", "recovery_data",
                   "verify_runtime", "local_backend", "plan_apply", "destroy_backend", "verify_backend")
        calls = []
        for method in methods:
            setattr(self.job, method, Mock(side_effect=lambda *args, m=method: calls.append(m)))
        self.job.execute()
        self.assertEqual(calls, list(methods))
        calls.clear()
        self.job.execute()
        self.assertEqual(calls, [])

    def test_failure_stops_before_backend_and_resume_retries_only_failed_step(self):
        for method in ("settings", "remove_edge", "remove_ingresses", "destroy_runtime",
                       "recovery_data", "local_backend", "plan_apply", "destroy_backend", "verify_backend"):
            setattr(self.job, method, Mock())
        self.job.verify_runtime = Mock(side_effect=teardown.Failure("EBS disk remains"))
        with self.assertRaises(teardown.Failure):
            self.job.execute()
        self.job.local_backend.assert_not_called()
        recovered = teardown.Teardown(self.directory)
        self.assertNotIn("verify runtime removal", recovered.record["completed"])
        self.assertIn("destroy runtime", recovered.record["completed"])
        self.job.verify_runtime.side_effect = None
        self.job.execute()
        self.job.destroy_runtime.assert_called_once()
        self.job.local_backend.assert_called_once()

    def test_backend_handoff_copies_fresh_state_and_uses_no_s3_backend(self):
        self.job.bootstrap.mkdir()
        (self.job.bootstrap / "backend.tf").write_text('terraform { backend "s3" {} }')
        (self.job.bootstrap / "state.tf").write_text(
            'resource "aws_s3_bucket" "terraform_state" {\n'
            '  force_destroy = false\n  lifecycle {\n    prevent_destroy = true\n  }\n}\n')
        state = {"serial": 7, "lineage": "test", "resources": []}
        self.job.record["inventory"] = {"bootstrap_serial": 7, "bootstrap_lineage": "test"}
        self.job.state = Mock(side_effect=[{"resources": []}, state])
        self.job.tf = Mock()
        self.job.local_backend()
        self.assertFalse((self.job.local / "backend.tf").exists())
        self.assertEqual(json.loads((self.job.local / "terraform.tfstate").read_text()), state)
        self.assertIn("force_destroy = true", (self.job.local / "state.tf").read_text())
        self.assertIn("prevent_destroy = true", (self.job.bootstrap / "state.tf").read_text())
        self.job.tf.assert_called_once_with(self.job.local, "init", "-input=false", "-lockfile=readonly")

    def test_init_seeds_module_and_provider_packages_without_remote_backend_metadata(self):
        source = self.directory / "source"
        package = source / ".terraform/providers/registry.terraform.io/hashicorp/aws/1.0/test"
        package.mkdir(parents=True)
        executable = package / "terraform-provider-aws"
        executable.write_text("provider")
        (source / ".terraform/terraform.tfstate").write_text("original remote backend metadata")
        modules = source / ".terraform/modules"
        modules.mkdir()
        (modules / "modules.json").write_text('{"Modules": []}')
        self.job.runtime.mkdir()
        def inspect(*args):
            installed = self.job.runtime / executable.relative_to(source)
            self.assertTrue(installed.is_symlink())
            self.assertEqual(installed.resolve(), executable.resolve())
            self.assertTrue((self.job.runtime / ".terraform/modules/modules.json").is_file())
            self.assertFalse((self.job.runtime / ".terraform/terraform.tfstate").exists())
        self.job.tf = Mock(side_effect=inspect)
        self.job.init_tf(self.job.runtime, source)
        self.assertEqual((source / ".terraform/terraform.tfstate").read_text(), "original remote backend metadata")

    def test_backend_retirement_refuses_reappearing_resources_or_concurrent_state_changes(self):
        self.job.record["inventory"] = {"bootstrap_serial": 7, "bootstrap_lineage": "test"}
        for states in ([{"resources": [state_resource("aws_vpc", "this", {})]}],
                       [{"resources": []}, {"serial": 8, "lineage": "test"}]):
            self.job.state = Mock(side_effect=states)
            with self.assertRaises(teardown.Failure):
                self.job.local_backend()

    def test_database_recovery_is_never_deleted_while_instance_exists(self):
        self.job.aws = Mock(return_value={"DBInstances": [{}]})
        with self.assertRaises(teardown.Failure):
            self.job.recovery_data()
        self.job.aws.assert_called_once()

    def test_deletes_only_this_databases_manual_snapshots_and_retained_backups(self):
        self.job.aws = Mock(side_effect=[None, {"DBSnapshots": [{
            "DBSnapshotIdentifier": "old-final", "SnapshotType": "manual", "Status": "available",
        }]}, {}, {"DBInstanceAutomatedBackups": [
            {"DBInstanceIdentifier": teardown.DATABASE, "Status": "retained",
             "DBInstanceAutomatedBackupsArn": "arn:lab-backup"},
            {"DBInstanceIdentifier": "other-db", "Status": "retained",
             "DBInstanceAutomatedBackupsArn": "arn:other-backup"},
        ]}, {}])
        self.job.wait = Mock()
        self.job.recovery_data()
        self.assertTrue(any("delete-db-snapshot" in c.args and "old-final" in c.args
                            for c in self.job.aws.call_args_list))
        self.assertTrue(any("delete-db-instance-automated-backup" in c.args and "arn:lab-backup" in c.args
                            for c in self.job.aws.call_args_list))
        self.assertFalse(any("arn:other-backup" in c.args for c in self.job.aws.call_args_list))

    def test_gitops_pods_stop_before_ingress_deletion(self):
        self.job.record["inventory"] = {"clusters": [teardown.CLUSTER]}
        controllers = {"items": [{
            "kind": kind, "metadata": {"name": name},
            "spec": {"replicas": 1, "selector": {"matchLabels": {"name": name}}},
        } for kind, name in (("StatefulSet", "argocd-application-controller"),
                              ("Deployment", "argocd-applicationset-controller"))]}
        ingress = {"items": [{"kind": "Ingress", "metadata": {"namespace": "app", "name": "app"}}]}
        calls = []
        def kube(*args, **kwargs):
            calls.append(args)
            if "deployments,statefulsets" in args:
                return json.dumps(controllers)
            if "pods" in args:
                return '{"items": []}'
            if "ingresses,services" in args:
                return json.dumps(ingress)
            return ""
        self.job.kube = kube
        self.job.no_load_balancers = Mock(return_value=True)
        self.job.remove_backend_groups = Mock()
        self.job.remove_ingresses()
        deletion = next(i for i, args in enumerate(calls) if "delete" in args)
        self.assertEqual(sum("scale" in args for args in calls[:deletion]), 2)
        self.assertEqual(sum("pods" in args for args in calls[:deletion]), 2)
        self.assertEqual(self.job.record["argocd_replicas"]["statefulset/argocd-application-controller"], 1)

    def test_existing_snapshot_deletion_can_be_resumed(self):
        self.job.aws = Mock(side_effect=[None, {"DBSnapshots": [{
            "DBSnapshotIdentifier": "old-final", "SnapshotType": "manual", "Status": "deleting",
        }]}, {"DBInstanceAutomatedBackups": []}])
        self.job.wait = Mock()
        self.job.recovery_data()
        self.assertFalse(any("delete-db-snapshot" in c.args for c in self.job.aws.call_args_list))
        self.job.wait.assert_called_once()

    def test_orphan_backend_group_requires_exact_tags_and_known_rule_description(self):
        self.job.record["inventory"] = {"vpc": "vpc-lab"}
        self.job.no_load_balancers = Mock(return_value=True)
        group = {"GroupId": "sg-backend", "Tags": [
            {"Key": "elbv2.k8s.aws/cluster", "Value": teardown.CLUSTER},
            {"Key": "elbv2.k8s.aws/resource", "Value": "backend-sg"},
        ]}
        rule = {"ReferencedGroupInfo": {"GroupId": "sg-backend"}, "GroupId": "sg-backend",
                "IsEgress": False, "Description": "unrelated rule", "SecurityGroupRuleId": "sgr-other"}
        self.job.aws = Mock(side_effect=[{"SecurityGroups": [group]}, {"NetworkInterfaces": []},
                                        {"SecurityGroupRules": [rule]}])
        with self.assertRaises(teardown.Failure):
            self.job.remove_backend_groups()
        self.assertFalse(any("delete-security-group" in c.args for c in self.job.aws.call_args_list))


class ConfigTests(unittest.TestCase):
    def test_original_source_is_unchanged_and_private_copy_has_deletion_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory)
            original = (teardown.ROOT / "database.tf").read_text()
            teardown.copy_config(teardown.ROOT, copy)
            for setting, before, after in (
                ("prevent_destroy", "true", "false"), ("deletion_protection", "true", "false"),
                ("skip_final_snapshot", "false", "true"), ("delete_automated_backups", "false", "true"),
            ):
                teardown.replace_setting(copy / "database.tf", setting, before, after)
                # Retry is idempotent.
                teardown.replace_setting(copy / "database.tf", setting, before, after)
            self.assertEqual((teardown.ROOT / "database.tf").read_text(), original)
            self.assertNotIn("prevent_destroy = true", (copy / "database.tf").read_text())
            self.assertTrue((copy / "functions/ip-allowlist.js.tftpl").exists())
            self.assertFalse((copy / ".terraform").exists())


class CommandTests(unittest.TestCase):
    def test_check_never_runs_deletion_or_prompts(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(teardown, "ROOT", Path(directory)), \
                patch.object(teardown.shutil, "which", return_value="installed"), \
                patch.object(teardown.Teardown, "aws", return_value={"Account": teardown.PROJECT}), \
                patch.object(teardown.Teardown, "initialize") as initialize, \
                patch.object(teardown.Teardown, "inventory") as inventory, \
                patch.object(teardown.Teardown, "execute") as execute, \
                patch("builtins.input") as prompt, patch.object(teardown.sys, "argv", ["teardown", "--check"]):
            teardown.main()
            initialize.assert_called_once()
            inventory.assert_called_once()
            execute.assert_not_called()
            prompt.assert_not_called()

    def test_wrong_project_aborts_before_initializing_or_deleting(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(teardown, "ROOT", Path(directory)), \
                patch.object(teardown.shutil, "which", return_value="installed"), \
                patch.object(teardown.Teardown, "aws", return_value={"Account": "other"}), \
                patch.object(teardown.Teardown, "initialize") as initialize, \
                patch.object(teardown.Teardown, "execute") as execute, \
                patch.object(teardown.sys, "argv", ["teardown", "--yes"]):
            with self.assertRaises(teardown.Failure):
                teardown.main()
            initialize.assert_not_called()
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
