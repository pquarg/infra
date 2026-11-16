"""Readiness fails closed on stale, wrong-parent and unresolved route status."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gateway", ROOT / "scripts/check-gateway.py")
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class RouteTests(unittest.TestCase):
    def run_route(self, *, namespace="demo-api", generation=2, resolved="True", parent=True):
        conditions = [{"type": name, "status": resolved if name == "ResolvedRefs" else "True",
                       "observedGeneration": generation} for name in ("Accepted", "ResolvedRefs", "Programmed")]
        route = {"kind": "HTTPRoute", "metadata": {"name": "demo-api", "generation": 2},
                 "status": {"parents": [{"parentRef": {"name": "demo-api", "namespace": namespace, "sectionName": "web"},
                                         "controllerName": "gateway.k8s.aws/alb", "conditions": conditions}] if parent else []}}
        gw = {"kind": "Gateway", "metadata": {"name": "demo-api", "generation": 2}, "status": {"conditions": conditions}}
        args = ["check", "--environment", "aws", "--context", "selected", "--kubeconfig", "/tmp/config", "--namespace", "demo-api", "--route", "demo-api"]
        with patch("sys.argv", args), patch.object(gateway.subprocess, "check_output", side_effect=[json.dumps(gw), json.dumps(route)]):
            gateway.main()

    def test_current_expected_parent_passes(self):
        self.run_route()

    def test_stale_unresolved_wrong_parent_and_absent_parent_fail(self):
        for changes in ({"generation": 1}, {"resolved": "False"}, {"namespace": "other"}, {"parent": False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_route(**changes)
