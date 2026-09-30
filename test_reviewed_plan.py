import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import main


class ReviewedPlanTests(unittest.TestCase):
    def setUp(self):
        self.repo = main.RepoInfo("owner", "sample", "owner/sample", "https://github.com/owner/sample", "https://github.com/owner/sample.git")
        self.data = {"repo": "owner/sample", "revision": "a" * 40,
                     "plan": {"action": "DEPLOY", "reason": "reviewed native route",
                              "steps": [{"type": "exec", "cmd": "python demo.py", "timeout": 30}]}}

    def test_revision_must_match_before_execution(self):
        with patch.object(main.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="b" * 40)):
            with self.assertRaisesRegex(main.RepoWayfinderError, "does not match"):
                main.reviewed_execution_plan(self.data, self.repo, Path("."))

    def test_wrong_repository_is_rejected_without_git(self):
        self.data["repo"] = "other/project"
        with patch.object(main.subprocess, "run") as git:
            with self.assertRaises(main.RepoWayfinderError):
                main.reviewed_execution_plan(self.data, self.repo, Path("."))
            git.assert_not_called()

    def test_security_validation_still_applies(self):
        self.data["plan"]["steps"][0]["cmd"] = "python demo.py && curl example.com"
        with patch.object(main.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="a" * 40)):
            with self.assertRaisesRegex(main.RepoWayfinderError, "rejected"):
                main.reviewed_execution_plan(self.data, self.repo, Path("."))

    def test_valid_plan_is_explicitly_labelled(self):
        with patch.object(main.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="a" * 40)):
            plan = main.reviewed_execution_plan(self.data, self.repo, Path("."))
        self.assertEqual(plan.source, "reviewed_local_plan")
        self.assertEqual(plan.steps[0].cmd, "python demo.py")

    def test_generated_output_does_not_invalidate_source_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "demo.py").write_text("print('sample')", encoding="utf-8")
            plan = main.ExecutionPlan("DEPLOY", [main.CommandStep("exec", "python demo.py -o output.txt", "test", 30)], "test", "reviewed")
            before = main.capture_plan_evidence(root, plan)
            (root / "output.txt").write_text("generated result", encoding="utf-8")
            self.assertEqual(before, main.capture_plan_evidence(root, plan))
            (root / "demo.py").write_text("print('changed')", encoding="utf-8")
            self.assertNotEqual(before, main.capture_plan_evidence(root, plan))



if __name__ == "__main__":
    unittest.main()
