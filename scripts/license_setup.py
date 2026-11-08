"""One-time local FreeSurfer license setup. Never uploads or prints the license."""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "config" / "license.txt"
REGISTRATION = "https://surfer.nmr.mgh.harvard.edu/registration.html"


class LicenseSetupError(RuntimeError):
    pass


def read_license(source: Path) -> bytes:
    if not source.is_file() or source.is_symlink():
        raise LicenseSetupError("License file not found or is a symbolic link")
    if source.stat().st_size > 64 * 1024:
        raise LicenseSetupError("License file is unexpectedly large")
    data = source.read_bytes()
    return validate_license(data)


def validate_license(data: bytes) -> bytes:
    # This is a plausibility check, not cryptographic verification.
    if len(data) < 24 or len(data) > 64 * 1024 or b"\x00" in data:
        raise LicenseSetupError("File is empty, binary or unexpectedly sized")
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LicenseSetupError("License must be plain text") from exc
    lines = [x.strip() for x in content.splitlines() if x.strip()]
    if len(lines) < 3 or any("<html" in x.lower() for x in lines):
        raise LicenseSetupError(
            "Expected the multi-line text FreeSurfer license, not a webpage"
        )
    if not re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", lines[0]):
        raise LicenseSetupError(
            "The first nonempty line should be the email used for FreeSurfer registration"
        )
    return data


def existing_license():
    try:
        read_license(DESTINATION)
        return True
    except LicenseSetupError:
        return False


def candidates(home: Path | None = None):
    """Search a few predictable local paths; never crawl emails or drives."""
    home = home or Path.home()
    roots = [home / "Downloads", home / "Desktop", home / "Загрузки",
             home / ".freesurfer", home]
    env = os.environ.get("FS_LICENSE")
    paths = [Path(env).expanduser()] if env else []
    for directory in roots:
        if not directory.is_dir():
            continue
        for pattern in ("license.txt", "license*.txt", "freesurfer*.txt"):
            paths.extend(directory.glob(pattern))
    distinct = {}
    for p in paths:
        if p.is_file() and not p.is_symlink() and p.resolve() != DESTINATION.resolve():
            distinct[str(p.resolve()).casefold()] = p
    return sorted(distinct.values(), key=lambda p: str(p).casefold())


def install(source: Path | None = None, data: bytes | None = None):
    if existing_license():
        print("FreeSurfer license is already installed: config/license.txt")
        return DESTINATION
    if DESTINATION.exists():
        raise LicenseSetupError(
            "config/license.txt exists but does not look like a FreeSurfer license. "
            "Review or move it before installing another."
        )
    if (source is None) == (data is None):
        raise LicenseSetupError("Provide exactly one of a file path or license text")
    payload = read_license(source) if source is not None else validate_license(data)
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".license-", suffix=".tmp",
            dir=DESTINATION.parent, delete=False
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
        os.replace(temp_path, DESTINATION)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    print("FreeSurfer license installed in config/license.txt (Git-ignored).")
    print("File content was not printed, logged or uploaded.")
    return DESTINATION


def wizard(input_fn=input, open_fn=webbrowser.open, home=None):
    if existing_license():
        print("FreeSurfer license: installed (config/license.txt).")
        return
    print("FreeSurfer requires one-time FREE registration.")
    print("Official form:", REGISTRATION)
    print("The license.txt will be emailed to the registered address.")
    found = candidates(home)
    if found:
        print("\nPossible local license files:")
        for i, item in enumerate(found, 1):
            print(f"{i}. {item}")
        print("These are candidates only; contents are checked before installation.")
    else:
        print("No license.txt found in Downloads, Desktop or home.")
    print("\nOptions: [number] install found file; [P] paste file path;")
    print("         [E] paste license email text; [R] open registration page;")
    print("         [Enter] rescan after saving attachment; [Q] cancel")
    for _ in range(4):
        choice = input_fn("Choice: ").strip()
        if choice.upper() == "Q":
            raise LicenseSetupError("Setup cancelled. License not installed")
        if choice.isdecimal() and 1 <= int(choice) <= len(found):
            install(source=found[int(choice) - 1])
            return
        if choice.upper() == "P":
            raw = input_fn("Full path to license.txt: ").strip().strip('"')
            install(source=Path(raw).expanduser())
            return
        if choice.upper() == "E":
            print("Paste ONLY the FreeSurfer license lines (not the whole email).")
            print("Enter END on its own line to finish. Input remains local.")
            chunks = []
            for _line in range(32):
                line = input_fn("")
                if line.strip() == "END":
                    break
                chunks.append(line)
            else:
                raise LicenseSetupError("Too many pasted lines")
            install(data=("\n".join(chunks) + "\n").encode("utf-8"))
            return
        if choice.upper() == "R":
            open_fn(REGISTRATION)
            print("Register in the browser. Save the emailed license.txt.")
            print("After saving, press Enter at the next prompt.")
            continue
        if choice == "":
            found = candidates(home)
            if len(found) == 1:
                print("Found:", found[0])
                install(source=found[0])
                return
            for i, item in enumerate(found, 1):
                print(f"{i}. {item}")
            if not found:
                print("Still no file. Try [P] with its exact path or [E] to paste text.")
            continue
        print("Invalid choice.")
    raise LicenseSetupError("License not installed; rerun setup after registration")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", nargs="?", choices=("setup", "status"), default="setup")
    args = parser.parse_args()
    if args.action == "status":
        if existing_license():
            print("FreeSurfer license: found (config/license.txt)")
        else:
            print("FreeSurfer license: missing. Run setup from the menu.")
            sys.exit(1)
    else:
        wizard()


if __name__ == "__main__":
    try:
        main()
    except (LicenseSetupError, OSError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
