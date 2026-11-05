import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = load_script("render-platform")
importer = load_script("import-platform")
NAMESPACE = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "argocd"}}
SERVICE = {"apiVersion": "v1", "kind": "Service", "metadata": {
    "namespace": "argocd", "name": "argocd-server",
}}


class RenderingTests(unittest.TestCase):
    def test_kubectl_json_stream_and_lists(self):
        source = json.dumps(NAMESPACE) + "\n" + json.dumps({
            "kind": "List", "items": [SERVICE],
        })
        self.assertEqual(renderer.decode_resources(source), [NAMESPACE, SERVICE])

    def test_reordering_does_not_change_resource_addresses(self):
        cluster_role = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
                        "metadata": {"name": "argocd-server"}}
        first = renderer.installation([NAMESPACE, SERVICE, cluster_role])
        self.assertEqual(first, renderer.installation([cluster_role, NAMESPACE, SERVICE]))
        self.assertNotIn("v1/Namespace/_cluster/argocd", first)
        self.assertIn("v1/Service/argocd/argocd-server", first)

    def test_duplicate_objects_and_wrong_namespaces_fail_closed(self):
        invalid_namespace = {**SERVICE, "metadata": {**SERVICE["metadata"], "namespace": "default"}}
        for resources in ([NAMESPACE, SERVICE, SERVICE], [NAMESPACE, invalid_namespace],
                          [SERVICE], [NAMESPACE]):
            with self.subTest(resources=resources), self.assertRaises(ValueError):
                renderer.installation(resources)


class ImportTests(unittest.TestCase):
    def test_cluster_scoped_import_omits_namespace(self):
        self.assertEqual(importer.import_id(NAMESPACE), "apiVersion=v1,kind=Namespace,name=argocd")
        self.assertEqual(importer.import_id(SERVICE),
                         "apiVersion=v1,kind=Service,namespace=argocd,name=argocd-server")

    def test_adoption_only_imports_and_can_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "terraform/argocd"
            state_root.mkdir(parents=True)
            (state_root / "terraform.tfstate").write_text("{}")
            tracked_address = "kubernetes_manifest.namespace"
            address = importer.installation_address(renderer.resource_key(SERVICE), SERVICE)
            with (patch.object(importer, "ROOT", root),
                  patch.object(importer.subprocess, "run") as run,
                  patch.object(importer.subprocess, "check_output",
                               side_effect=[tracked_address + "\n", "service/argocd-server\n"]) as output):
                importer.adopt("argocd", [(tracked_address, NAMESPACE), (address, SERVICE)],
                               "kind-example", "example", "/tmp/kubeconfig")
            calls = [call.args[0] for call in run.call_args_list]
            self.assertEqual(len(calls), 2)
            self.assertIn("init", calls[0])
            self.assertIn("import", calls[1])
            self.assertIn("-var=cluster_name=example", calls[1])
            self.assertEqual(calls[1][-2:], [address, importer.import_id(SERVICE)])
            read = output.call_args_list[1].args[0]
            self.assertEqual(read[:5], ["kubectl", "--kubeconfig", "/tmp/kubeconfig",
                                       "--context", "kind-example"])
            self.assertIn("get", read)

    def test_new_state_imports_secrets_using_sensitive_typed_resource(self):
        secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {
            "name": "argocd-secret", "namespace": "argocd",
        }}
        address = importer.installation_address(renderer.resource_key(secret), secret)
        self.assertTrue(address.startswith("kubernetes_secret_v1.installation["))
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(importer, "ROOT", Path(directory)),
                  patch.object(importer.subprocess, "run") as run,
                  patch.object(importer.subprocess, "check_output", return_value="secret/argocd-secret")):
                importer.adopt("argocd", [(address, secret)], "kind-example", "example", "/tmp/config")
            self.assertEqual(run.call_args_list[-1].args[0][-1], "argocd/argocd-secret")

    def test_wrong_cluster_state_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "terraform/argocd"
            state_root.mkdir(parents=True)
            (state_root / "terraform.tfstate").write_text(json.dumps({
                "outputs": {"context": {"value": "kind-other"}},
            }))
            with (patch.object(importer, "ROOT", root),
                  patch.object(importer.subprocess, "run") as run,
                  patch.object(importer.subprocess, "check_output", return_value=""),
                  self.assertRaisesRegex(ValueError, "kind-other")):
                importer.adopt("argocd", [("kubernetes_manifest.namespace", NAMESPACE)],
                               "kind-example", "example", "/tmp/config")
            self.assertEqual(len(run.call_args_list), 1)  # init only

    def test_failed_import_stops_without_trying_later_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(importer, "ROOT", Path(directory)),
                  patch.object(importer.subprocess, "run", side_effect=[None, RuntimeError("import failed")]),
                  patch.object(importer.subprocess, "check_output", return_value="namespace/argocd") as output,
                  self.assertRaisesRegex(RuntimeError, "import failed")):
                importer.adopt("argocd", [("kubernetes_manifest.namespace", NAMESPACE),
                                          ("unused", SERVICE)], "kind-example", "example", "/tmp/config")
            self.assertEqual(output.call_count, 1)


class BootstrapTests(unittest.TestCase):
    def run_bootstrap(self, *, fail_apply=False):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            log = tools / "commands.log"
            fake = '''#!/bin/bash
printf '%s %s\\n' "${0##*/}" "$*" >> "$COMMAND_LOG"
if [[ "${0##*/}" == terraform && "$*" == *" apply "* && "$FAIL_APPLY" == 1 ]]; then
  exit 42
fi
if [[ "${0##*/}" == kind ]]; then printf 'local-control-plane\\n'; fi
if [[ "${0##*/}" == docker ]]; then printf '127.0.0.1:80\\n'; fi
'''
            for name in ("terraform", "kubectl", "kind", "docker", "python3"):
                executable = tools / name
                executable.write_text(fake)
                executable.chmod(0o700)
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/bootstrap-kind.sh"), "example"],
                env={**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                     "COMMAND_LOG": str(log), "FAIL_APPLY": "1" if fail_apply else "0",
                     "INFRA_LOCAL_KUBECONFIG": "/tmp/explicit-local-kubeconfig",
                     "KUBECONFIG": "/tmp/ambient-config-must-not-be-used"},
                capture_output=True, text=True,
            )
            return result, log.read_text().splitlines()

    def test_registration_waits_for_crds_and_all_commands_select_local_context(self):
        result, commands = self.run_bootstrap()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        applies = [i for i, command in enumerate(commands) if command.startswith("terraform ") and " apply " in command]
        self.assertEqual(len(applies), 2)
        crd_wait = next(i for i, command in enumerate(commands) if "condition=Established" in command)
        self.assertLess(applies[0], crd_wait)
        self.assertLess(crd_wait, applies[1])
        for command in commands:
            if command.startswith("kubectl "):
                self.assertIn("--context=kind-example", command)
                self.assertIn("--kubeconfig=/tmp/explicit-local-kubeconfig", command)
                self.assertNotIn(" apply ", command)
        for i in applies:
            self.assertIn("-var=cluster_name=example", commands[i])
            self.assertNotIn("-auto-approve", commands[i])

    def test_failed_installation_never_applies_registration(self):
        result, commands = self.run_bootstrap(fail_apply=True)
        self.assertEqual(result.returncode, 42)
        self.assertEqual(sum(" apply " in command for command in commands), 1)
        self.assertFalse(any("condition=Established" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
