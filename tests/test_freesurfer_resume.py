"""Safe FreeSurfer crash-resume regression; synthetic subjects, never patient MRI."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import freesurfer_resume as resume
import mri_pipeline as pipeline


class CrashResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.subjects = self.root / "work" / "freesurfer"
        self.subjects.mkdir(parents=True)
        self.subject_id = "pacni_TEST_synthsr_aabbccdd"
        self.folder = self.subjects / self.subject_id
        (self.folder / "scripts").mkdir(parents=True)
        (self.folder / "mri" / "orig").mkdir(parents=True)
        (self.folder / "mri" / "orig" / "001.mgz").write_bytes(b"synthetic placeholder")
        (self.folder / "scripts" / "recon-all.log").write_text("historical processing preserved")
        (self.folder / "surf").mkdir()
        (self.folder / "surf" / "lh.white").write_bytes(b"intermediate preserved")

    def tearDown(self):
        self.temp.cleanup()

    def marker(self, name="IsRunning.lh+rh", subid=None, age=7200):
        subid = subid or self.subject_id
        lock = self.folder / "scripts" / name
        lock.write_text(
            "------------------------------\n"
            "SUBJECT " + subid + "\nHEMI lh rh\n"
            "DATE Mon Nov 9 15:18:35 UTC 2026\nUSER root\n"
            "HOST\nPROCESSID 1\nPROCESSOR x86_64\nOS Linux\n",
            encoding="utf-8",
        )
        old = time.time() - age
        os.utime(lock, (old, old))
        return lock

    def no_containers(self, args, **kwargs):
        if args[:3] == ["docker", "ps", "-q"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError("Unexpected Docker operation")

    def test_stale_lock_recovered_preserving_data_and_logs(self):
        lock = self.marker()
        with resume.subject_launch_guard(self.subjects, self.subject_id):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                recovered = resume.clear_stale_isrunning(
                    self.folder, self.subject_id, self.subjects, run_fn=self.no_containers)
        self.assertTrue(recovered)
        self.assertFalse(lock.exists())
        self.assertEqual((self.folder/"scripts"/"recon-all.log").read_text(),
                         "historical processing preserved")
        self.assertEqual((self.folder/"surf"/"lh.white").read_bytes(),b"intermediate preserved")
        self.assertTrue((self.folder/"mri"/"orig"/"001.mgz").exists())
        self.assertIn("Recovered stale FreeSurfer", output.getvalue())

    def test_fresh_marker_refuses_even_when_docker_empty(self):
        lock = self.marker(age=10)
        with self.assertRaisesRegex(resume.ResumeError, "recent"):
            resume.clear_stale_isrunning(self.folder,self.subject_id,self.subjects,
                                         run_fn=self.no_containers)
        self.assertTrue(lock.exists())

    def test_unknown_subject_marker_never_removed(self):
        lock=self.marker(subid="pacni_OTHER_synthsr_aabbccdd")
        with self.assertRaisesRegex(resume.ResumeError,"unknown subject"):
            resume.clear_stale_isrunning(self.folder,self.subject_id,self.subjects,
                                         run_fn=self.no_containers)
        self.assertTrue(lock.exists())

    def test_refuse_if_running_freesurfer_container(self):
        lock=self.marker()
        cid="a"*64
        def fake(args,**kwargs):
            if args[:3]==["docker","ps","-q"]:
                return subprocess.CompletedProcess(args,0,stdout=cid+"\n",stderr="")
            if args[:3]==["docker","inspect","--type"]:
                data=[{"State":{"Running":True},"Config":{"Image":"freesurfer/freesurfer:7.4.1"},
                       "Path":"recon-all","Args":["-s",self.subject_id],
                       "Mounts":[{"Source":str(self.subjects),"Destination":"/subjects"}]}]
                return subprocess.CompletedProcess(args,0,stdout=json.dumps(data),stderr="")
            raise AssertionError("Unexpected Docker call")
        with self.assertRaisesRegex(resume.ResumeError,"still running"):
            resume.clear_stale_isrunning(self.folder,self.subject_id,self.subjects,
                                         run_fn=fake)
        self.assertTrue(lock.exists())

    def test_other_running_container_is_not_automatically_stopped(self):
        lock=self.marker()
        cid="b"*64
        def fake(args,**kwargs):
            if args[:3]==["docker","ps","-q"]:
                return subprocess.CompletedProcess(args,0,stdout=cid+"\n",stderr="")
            if args[:3]==["docker","inspect","--type"]:
                data=[{"State":{"Running":True},"Config":{"Image":"qwen/local"},
                       "Path":"qwen","Args":["--model","local"],
                       "Mounts":[{"Source":str(self.root/"qwen"),
                                  "Destination":"/models"}]}]
                return subprocess.CompletedProcess(args,0,stdout=json.dumps(data),stderr="")
            raise AssertionError("Unexpected Docker call")
        self.assertTrue(resume.clear_stale_isrunning(self.folder,self.subject_id,
                                                  self.subjects,run_fn=fake))
        self.assertFalse(lock.exists())

    def test_docker_failure_is_fail_closed(self):
        lock=self.marker()
        def fake(args,**kwargs):
            return subprocess.CompletedProcess(args,1,stdout="",
                                               stderr="Docker Engine unavailable")
        with self.assertRaisesRegex(resume.ResumeError,"Docker container inventory failed"):
            resume.clear_stale_isrunning(self.folder,self.subject_id,self.subjects,
                                         run_fn=fake)
        self.assertTrue(lock.exists())

    def test_markers_from_both_hemispheres(self):
        a=self.marker("IsRunning.lh")
        b=self.marker("IsRunning.rh")
        self.assertTrue(resume.clear_stale_isrunning(
            self.folder,self.subject_id,self.subjects,run_fn=self.no_containers))
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())

    def test_symlinked_marker_never_deleted(self):
        src=self.folder/"scripts"/"preserve"
        src.write_text("real file protected")
        marker=self.folder/"scripts"/"IsRunning.lh+rh"
        try:
            marker.symlink_to(src)
        except (OSError, NotImplementedError):
            self.skipTest("OS permissions prohibit symlink creation")
        with self.assertRaisesRegex(resume.ResumeError,"marker type"):
            resume.clear_stale_isrunning(self.folder,self.subject_id,self.subjects,
                                         run_fn=self.no_containers)
        self.assertTrue(marker.is_symlink())
        self.assertEqual(src.read_text(),"real file protected")

    def test_double_launch_is_blocked_by_os_lock(self):
        with resume.subject_launch_guard(self.subjects,self.subject_id):
            with self.assertRaisesRegex(resume.ResumeError,"already being processed"):
                with resume.subject_launch_guard(self.subjects,self.subject_id):
                    pass
        # The OS releases this lock automatically, even if a launch crashes.
        with resume.subject_launch_guard(self.subjects,self.subject_id):
            pass

    def test_reconstruct_resume_runs_make_all_and_never_reimports(self):
        study=self.root/"MRI"/"2018 SYNTHETIC"
        study.mkdir(parents=True)
        image=study/"T1.nii.gz"
        image.write_bytes(b"not real MRI")
        subject=self.subject_id
        expected="synthetic provenance"
        original_log=(self.folder/"scripts"/"recon-all.log").read_bytes()
        guard=resume.subject_launch_guard
        calls=[]
        def fake_sh(args):
            calls.append(args)
            # Simulate FreeSurfer successful completion for this placeholder.
            (self.folder/"scripts"/"recon-all.done").write_text("completed")
        with mock.patch.object(pipeline,"SUBJECTS",self.subjects), \
             mock.patch.object(pipeline,"PROVENANCE",self.root/"work"/"provenance"), \
             mock.patch.object(pipeline,"LICENSE",self.root/"config"/"license.txt"), \
             mock.patch.object(pipeline,"subject_id",return_value=subject), \
             mock.patch.object(pipeline,"active_input",return_value=image), \
             mock.patch.object(pipeline,"provenance",return_value={"signature":expected}), \
             mock.patch.object(pipeline,"record_provenance"), \
             mock.patch.object(pipeline,"check_license"), \
             mock.patch.object(pipeline,"check_docker"), \
             mock.patch.object(pipeline,"sh",side_effect=fake_sh), \
             mock.patch.object(pipeline,"recon_resumer",return_value=resume), \
             mock.patch.object(resume,"ensure_no_active_freesurfer"):
            self.marker()
            pipeline.reconstruct(study,"synthsr")
        self.assertEqual(len(calls),1)
        self.assertIn("-make",calls[0])
        self.assertIn("all",calls[0])
        self.assertNotIn("-i",calls[0])
        self.assertEqual((self.folder/"scripts"/"recon-all.log").read_bytes(),original_log)
        self.assertTrue((self.folder/"surf"/"lh.white").exists())
        self.assertTrue((self.folder/"scripts"/"recon-all.done").exists())

if __name__=="__main__":
    unittest.main()
