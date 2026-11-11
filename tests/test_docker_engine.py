"""Docker Desktop preflight regression tests (no MRI or Docker required)."""
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mri_pipeline.py"
spec = importlib.util.spec_from_file_location("pacni_engine_preflight", SCRIPT)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class DockerEnginePreflightTests(unittest.TestCase):
    def test_linux_engine_accepted(self):
        result = subprocess.CompletedProcess([], 0, stdout="linux|28.0.1\n", stderr="")
        with mock.patch.object(pipeline.shutil, "which", return_value="docker"), \
             mock.patch.object(pipeline.subprocess, "run", return_value=result) as runner:
            self.assertIsNone(pipeline.check_docker())
        self.assertEqual(runner.call_args.args[0],
                         ["docker", "info", "--format", "{{.OSType}}|{{.ServerVersion}}"])
        self.assertEqual(runner.call_args.kwargs["timeout"], 25)

    def test_installed_cli_but_engine_stopped(self):
        result = subprocess.CompletedProcess(
            [], 1, stdout="",
            stderr="open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.")
        with mock.patch.object(pipeline.shutil, "which", return_value="docker"), \
             mock.patch.object(pipeline.subprocess, "run", return_value=result):
            with self.assertRaises(pipeline.PipelineError) as raised:
                pipeline.check_docker()
        message = str(raised.exception)
        self.assertIn("Docker Engine is unreachable", message)
        self.assertIn("Docker Desktop", message)
        self.assertIn("Linux containers", message)
        self.assertIn("have NOT been modified", message)
        self.assertNotIn("CalledProcessError", message)

    def test_windows_engine_fails_before_free_surfer(self):
        result = subprocess.CompletedProcess([], 0, stdout="windows|28.0.1\n", stderr="")
        with mock.patch.object(pipeline.shutil, "which", return_value="docker"), \
             mock.patch.object(pipeline.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(pipeline.PipelineError, "Switch to Linux containers"):
                pipeline.check_docker()

    def test_timeout_is_actionable(self):
        with mock.patch.object(pipeline.shutil, "which", return_value="docker"), \
             mock.patch.object(pipeline.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=25)):
            with self.assertRaisesRegex(pipeline.PipelineError, "did not respond"):
                pipeline.check_docker()

    def test_cli_missing_no_probe(self):
        with mock.patch.object(pipeline.shutil, "which", return_value=None), \
             mock.patch.object(pipeline.subprocess, "run") as runner:
            with self.assertRaisesRegex(pipeline.PipelineError, "CLI was not found"):
                pipeline.check_docker()
            runner.assert_not_called()

    def test_garbled_status_is_not_treated_as_success(self):
        result = subprocess.CompletedProcess([], 0, stdout="garbage\n", stderr="")
        with mock.patch.object(pipeline.shutil, "which", return_value="docker"), \
             mock.patch.object(pipeline.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(pipeline.PipelineError, "valid OS/version"):
                pipeline.check_docker()


if __name__ == "__main__":
    unittest.main()
