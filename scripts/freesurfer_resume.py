"""Conservative cross-platform recovery for interrupted FreeSurfer recon-all.

An IsRunning file is *not* proof that a process is alive. Removing it while a
reconstruction runs can corrupt the same subject. Only a previously imported
and provenance-verified subject is eligible, and only after an exclusive
OS-released launcher lock plus a successful inspection of running Docker
containers. Never use the unsafe recon-all -no-isrunning bypass.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


class ResumeError(RuntimeError):
    pass


IS_RUNNING_NAMES = ("IsRunning.lh", "IsRunning.rh", "IsRunning.lh+rh")
MIN_LOCK_AGE_SECONDS = 90


def _safe_subject(subject_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", subject_id):
        raise ResumeError("Refusing unexpected FreeSurfer subject identifier")
    return subject_id


@contextlib.contextmanager
def subject_launch_guard(subjects_root: Path, subject_id: str):
    """Per-subject OS-level nonblocking lock, released on crash or Ctrl+C.

    This lockfile is intentionally permanent: deleting an OS-lock pathname
    can create a second inode and destroy cross-process mutual exclusion.
    Unlike FreeSurfer IsRunning markers, OS locks are released at process exit.
    """
    subject_id = _safe_subject(subject_id)
    root = Path(subjects_root)
    if root.is_symlink() or not root.is_dir():
        raise ResumeError("Invalid or symlinked FreeSurfer output directory")
    lock_dir = root.parent / "locks"
    if lock_dir.is_symlink():
        raise ResumeError("Refusing symlinked process-lock directory")
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ("recon-" + subject_id + ".lock")
    if lock_path.is_symlink():
        raise ResumeError("Refusing symlinked process lock")
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        locked = False
        try:
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise ResumeError(
                        "This FreeSurfer subject is already being processed "
                        "by another DunedinPACNI process. No files were changed."
                    ) from None
            else:
                import fcntl
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise ResumeError(
                        "This FreeSurfer subject is already being processed "
                        "by another DunedinPACNI process. No files were changed."
                    ) from None
            locked = True
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _run_docker(args, run_fn=subprocess.run):
    try:
        result = run_fn(args, capture_output=True, text=True, errors="replace",
                        check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise ResumeError(
            "Cannot verify active Docker containers. Stale FreeSurfer lock "
            "was NOT removed. Check Docker Desktop and retry."
        ) from None
    if result.returncode != 0:
        raise ResumeError(
            "Docker container inventory failed. Stale FreeSurfer lock was "
            "NOT removed. Check 'docker ps' before retrying."
        )
    return result.stdout


def ensure_no_active_freesurfer(subjects_root: Path, run_fn=subprocess.run):
    """Refuse recovery if any currently running container may own recon-all.

    Overblocking unrelated FreeSurfer subjects is intentional for safety.
    Docker inspection is fail-closed, including on malformed responses.
    """
    ids = _run_docker(["docker", "ps", "-q", "--no-trunc"], run_fn).split()
    if len(ids) > 128 or any(not re.fullmatch(r"[a-f0-9]{12,64}", cid) for cid in ids):
        raise ResumeError("Unexpected Docker container inventory. Lock was NOT removed.")
    if not ids:
        return
    raw = _run_docker(["docker", "inspect", "--type", "container", *ids], run_fn)
    try:
        items = json.loads(raw)
        if not isinstance(items, list) or len(items) != len(ids):
            raise ValueError("Invalid inspect array")
        target = os.path.normcase(os.path.normpath(str(subjects_root.resolve())))
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Invalid inspected container")
            state = item.get("State")
            config = item.get("Config")
            mounts = item.get("Mounts")
            if not isinstance(state, dict) or not isinstance(config, dict) or not isinstance(mounts, list):
                raise ValueError("Missing Docker inspection fields")
            if not state.get("Running"):
                continue
            image = str(config.get("Image") or "").lower()
            exe = str(item.get("Path") or "").lower()
            args = item.get("Args") or []
            if not isinstance(args, list):
                raise ValueError("Malformed container arguments")
            command = " ".join([exe, *[str(a).lower() for a in args]])
            # Check all recon-all containers, including those launched outside
            # DunedinPACNI, because different containers can share SUBJECTS_DIR.
            if "freesurfer" in image or re.search(r"(?<![a-z])recon-all(?![a-z])", command):
                raise ResumeError(
                    "A FreeSurfer/recon-all Docker container is still running. "
                    "Do NOT clear IsRunning files or launch another reconstruction."
                )
            for mount in mounts:
                if not isinstance(mount, dict):
                    raise ValueError("Invalid Docker mount metadata")
                src = mount.get("Source")
                dest = str(mount.get("Destination") or "")
                # Refuse if any running container mounts the same subjects root.
                if src and dest in ("/subjects", "/subjects/"):
                    source = os.path.normcase(os.path.normpath(str(src)))
                    if source == target:
                        raise ResumeError(
                            "Another Docker container is using this FreeSurfer "
                            "subjects directory. Recovery blocked."
                        )
    except ResumeError:
        raise
    except (ValueError, TypeError, KeyError):
        raise ResumeError("Cannot validate active Docker containers; no lock removed.") from None


def clear_stale_isrunning(subject_dir: Path, subject_id: str, subjects_root: Path,
                          run_fn=subprocess.run, now_fn=time.time):
    """Remove only FreeSurfer's stale IsRunning markers for *this* subject.

    Returns True if any FreeSurfer markers were cleared. All other outputs
    (logs, MRI, surfaces, recon data, provenance) remain untouched.
    """
    subject_id = _safe_subject(subject_id)
    folder = Path(subject_dir)
    if folder.is_symlink() or not folder.is_dir():
        raise ResumeError("FreeSurfer subject directory is not a safe real directory")
    scripts = folder / "scripts"
    if scripts.is_symlink():
        raise ResumeError("Refusing symlinked FreeSurfer scripts directory")
    if not scripts.is_dir():
        return False
    found = [scripts / name for name in IS_RUNNING_NAMES
             if (scripts / name).exists() or (scripts / name).is_symlink()]
    if not found:
        return False
    if not (folder / "mri" / "orig" / "001.mgz").is_file():
        raise ResumeError(
            "Existing FreeSurfer subject lacks imported 001.mgz. "
            "Stale marker was NOT removed; inspect recon-all.log."
        )
    snapshots = []
    for marker in found:
        if marker.is_symlink() or not marker.is_file():
            raise ResumeError("Unexpected FreeSurfer IsRunning marker type; no files changed.")
        stat = marker.stat()
        if stat.st_size > 16384 or stat.st_size < 20:
            raise ResumeError("Unrecognized FreeSurfer marker format; no files changed.")
        if now_fn() - stat.st_mtime < MIN_LOCK_AGE_SECONDS:
            raise ResumeError(
                "FreeSurfer IsRunning marker is recent. Wait before retrying; "
                "a reconstruction may still be running. No files changed."
            )
        content = marker.read_text(encoding="utf-8", errors="replace")
        parsed = re.search(r"(?m)^SUBJECT\s+([A-Za-z0-9_]+)\s*$", content)
        if parsed is None or parsed.group(1) != subject_id:
            raise ResumeError("IsRunning belongs to an unknown subject; no files changed.")
        snapshots.append((marker, stat.st_size, stat.st_mtime_ns))
    # Check active Docker jobs only after verifying marker content/provenance.
    # This must be called inside subject_launch_guard by the owner.
    ensure_no_active_freesurfer(subjects_root, run_fn)
    for marker, size, mtime_ns in snapshots:
        stat = marker.stat()
        if stat.st_size != size or stat.st_mtime_ns != mtime_ns:
            raise ResumeError(
                "IsRunning changed during verification. No marker removed; retry later."
            )
    for marker, _, _ in snapshots:
        marker.unlink()
    print(
        "Recovered stale FreeSurfer IsRunning marker(s) after verifying no "
        "running Docker reconstruction. Existing MRI, logs and recon outputs preserved."
    )
    return True
