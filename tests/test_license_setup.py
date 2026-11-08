"""The FreeSurfer license wizard must work without a real user key."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "license_setup.py"
spec = importlib.util.spec_from_file_location("license_setup", SCRIPT)
license_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(license_module)

SYNTHETIC = b"researcher@example.org\n1234567890\nabcdef9876543210\n"


class LicenseSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.dest = self.home / "repo" / "config" / "license.txt"
        self.patch = mock.patch.object(license_module, "DESTINATION", self.dest)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_import_candidate_once_without_logging_contents(self):
        downloads = self.home / "Downloads"
        downloads.mkdir()
        original = downloads / "license.txt"
        original.write_bytes(SYNTHETIC)
        self.assertIn(original, license_module.candidates(home=self.home))
        target = license_module.install(source=original)
        self.assertEqual(target.read_bytes(), SYNTHETIC)
        self.assertEqual(original.read_bytes(), SYNTHETIC)
        self.assertTrue(license_module.existing_license())
        target2 = license_module.install(source=original)
        self.assertEqual(target, target2)

    def test_wizard_selects_file_and_skips_browser(self):
        downloads = self.home / "Downloads"
        downloads.mkdir()
        (downloads / "license.txt").write_bytes(SYNTHETIC)
        browser = mock.Mock()
        license_module.wizard(input_fn=lambda prompt: "1", open_fn=browser,
                              home=self.home)
        self.assertEqual(self.dest.read_bytes(), SYNTHETIC)
        browser.assert_not_called()

    def test_browser_registration_then_rescan(self):
        downloads = self.home / "Downloads"
        downloads.mkdir()
        choices = iter(["R", ""])
        def register(url):
            self.assertEqual(url, license_module.REGISTRATION)
            (downloads / "license.txt").write_bytes(SYNTHETIC)
        license_module.wizard(input_fn=lambda prompt: next(choices),
                              open_fn=register, home=self.home)
        self.assertTrue(self.dest.is_file())

    def test_email_text_can_be_pasted_locally(self):
        answers = iter(["E", "researcher@example.org", "1234567890",
                        "abcdef9876543210", "END"])
        license_module.wizard(input_fn=lambda prompt: next(answers),
                              open_fn=lambda url: None, home=self.home)
        self.assertEqual(self.dest.read_bytes(), SYNTHETIC)

    def test_reject_invalid_or_html_license(self):
        for data in (b"hello", b"no-email\nabc\nxyz\n",
                     b"test@example.org\n<html>\nmore\n",
                     b"researcher@example.org\none\ntwo\x00"):
            with self.subTest(data=data[:10]):
                with self.assertRaises(license_module.LicenseSetupError):
                    license_module.install(data=data)
                self.assertFalse(self.dest.exists())

    def test_does_not_replace_bad_existing_destination(self):
        self.dest.parent.mkdir(parents=True, exist_ok=True)
        self.dest.write_text("unrelated file")
        with self.assertRaises(license_module.LicenseSetupError):
            license_module.install(data=SYNTHETIC)
        self.assertEqual(self.dest.read_text(), "unrelated file")

    def test_no_unattended_registration(self):
        with self.assertRaises(license_module.LicenseSetupError):
            license_module.wizard(input_fn=lambda prompt: "Q",
                                  open_fn=mock.Mock(), home=self.home)
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()
