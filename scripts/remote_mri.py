"""Selective public-cloud DICOM intake, client-side and opt-in.

Supported cloud listings: public Yandex Disk links (no API key), public Google
Drive folder (PACNI_GOOGLE_API_KEY), Dropbox shared folders (PACNI_DROPBOX_TOKEN).
Only fixed provider endpoints and provider-controlled HTTPS media hosts are used.
DICOM metadata is inspected with HTTP Range; failures DO NOT fetch full MRI.
No share URL, tokens, or patient metadata are committed or printed to logs.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import ssl
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import patient_registry as patients

MAX_LISTED = 5000
MAX_PREVIEW = 262144
MAX_SERIES_BYTES = 4 * 1024**3
MAX_SERIES_FILES = 2000
MAX_SINGLE_FILE = 512 * 1024**2
T1 = re.compile(r"(?i)(?:^|[^a-z])t1(?:$|[^a-z])|mprage|mp.rage|bravo|(?:f)?spgr|mdeft")
EXCLUDE = re.compile(r"(?i)t2|flair|tof|angio|dwi|diffusion|adc|swi|scout|locali[sz]er|survey|3.pl|fieldmap")
SAFE_SUFFIX = (".dcm", ".ima", "")
ALLOWED_HOSTS = {
    "cloud-api.yandex.net", "www.googleapis.com",
    "api.dropboxapi.com", "content.dropboxapi.com",
}
CDN_SUFFIXES = (".disk.yandex.ru", ".disk.yandex.net", ".storage.yandex.net",
                ".googleusercontent.com", ".dropboxusercontent.com")
EXPLICIT_LONG_VR = {"OB", "OW", "OF", "SQ", "UT", "UN", "OD", "OL", "UC", "UR"}

class CloudError(RuntimeError):
    pass


def validate_url(value):
    p = urllib.parse.urlsplit(value)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port:
        raise CloudError("Only standard HTTPS URLs without embedded credentials are accepted")
    host = p.hostname.lower()
    if host not in ALLOWED_HOSTS and not any(host.endswith(s) for s in CDN_SUFFIXES):
        raise CloudError("External download URL is outside allowlisted cloud providers")
    return value


def validate_share(value):
    p = urllib.parse.urlsplit(value)
    if p.scheme != "https" or p.username or p.password or p.port or not p.hostname:
        raise CloudError("Cloud share must be a read-only HTTPS link")
    host = p.hostname.lower()
    if host in {"disk.yandex.ru", "yadi.sk", "disk.yandex.com"}:
        return "yandex"
    if host in {"drive.google.com", "docs.google.com"}:
        return "google"
    if host in {"www.dropbox.com", "dropbox.com", "db.tt"}:
        return "dropbox"
    raise CloudError("Only public Yandex Disk, Google Drive or Dropbox links are supported")


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        # Do not forward provider credentials to a different origin.
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            for h in ("Authorization", "Dropbox-API-Arg"):
                if new.has_header(h):
                    new.remove_header(h)
        return new


_OPENER = urllib.request.build_opener(CheckedRedirect)


def http_request(url, headers=None, post=None, max_bytes=1024 * 1024):
    validate_url(url)
    req = urllib.request.Request(url, data=post, headers=headers or {},
                                 method="POST" if post is not None else "GET")
    try:
        with _OPENER.open(req, timeout=35) as response:
            if response.status not in (200, 206):
                raise CloudError("Cloud server returned unexpected HTTP status")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise CloudError("Response exceeded safe size bound")
            return response.status, response.headers, body
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        # Signed URLs / bearer tokens can be embedded in exception messages.
        raise CloudError("Cloud request failed; check link permissions and expiry") from None


def as_json(url, headers=None, post=None):
    _, _, data = http_request(url, headers=headers, post=post, max_bytes=3*1024*1024)
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise CloudError("Invalid cloud provider metadata response") from None
    if not isinstance(obj, dict):
        raise CloudError("Malformed cloud response")
    return obj


def url_q(base, **query):
    return base + "?" + urllib.parse.urlencode(query)


def listing_yandex(share):
    files = []
    folders = [""]
    while folders:
        rel = folders.pop(0)
        offset = 0
        while True:
            params = {"public_key": share, "limit": 500, "offset": offset}
            if rel:
                params["path"] = rel
            obj = as_json(url_q("https://cloud-api.yandex.net/v1/disk/public/resources", **params))
            if obj.get("type") == "file":
                files.append({"name": str(obj.get("name", "")), "path": rel,
                              "parent": str(Path(rel).parent).replace("\\", "/"),
                              "size": int(obj.get("size", 0)), "provider": "yandex"})
                break
            if obj.get("type") != "dir":
                raise CloudError("Shared link is not a public folder")
            embedded = obj.get("_embedded", {})
            items = embedded.get("items", [])
            if not isinstance(items, list):
                raise CloudError("Invalid folder listing")
            for row in items:
                name = row.get("name", "")
                if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in {".", ".."}:
                    raise CloudError("Invalid remote filename")
                child = (rel.rstrip("/") + "/" + name)
                if row.get("type") == "dir":
                    folders.append(child)
                elif row.get("type") == "file":
                    files.append({"name": name, "path": child, "parent": rel or "/",
                                  "size": int(row.get("size", 0)), "provider": "yandex"})
                if len(files) + len(folders) > MAX_LISTED:
                    raise CloudError("Cloud folder listing exceeds safe bounds")
            if len(items) < 500:
                break
            offset += len(items)
    return files


def google_folder_id(share):
    p = urllib.parse.urlsplit(share)
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", p.path)
    if m:
        return m.group(1)
    q = urllib.parse.parse_qs(p.query)
    if p.path.endswith("/folders") and q.get("id"):
        return q["id"][0]
    raise CloudError("Google Drive requires a public folder link, not a file/ZIP preview")


def listing_google(share):
    api_key = os.getenv("PACNI_GOOGLE_API_KEY", "")
    if not api_key:
        raise CloudError("Google public folder listing requires PACNI_GOOGLE_API_KEY")
    files = []
    folders = [google_folder_id(share)]
    while folders:
        folder = folders.pop(0)
        token = ""
        while True:
            query = {"q": f"'{folder}' in parents and trashed = false",
                     "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,size)",
                     "pageSize": 1000, "key": api_key}
            if token:
                query["pageToken"] = token
            obj = as_json(url_q("https://www.googleapis.com/drive/v3/files", **query))
            if obj.get("incompleteSearch"):
                raise CloudError("Provider reported an incomplete folder search")
            for row in obj.get("files", []):
                name = row.get("name", "")
                if not isinstance(name, str) or not name or "/" in name or "\\" in name:
                    raise CloudError("Invalid remote filename")
                if row.get("mimeType") == "application/vnd.google-apps.folder":
                    folders.append(row["id"])
                elif row.get("mimeType") != "application/vnd.google-apps.shortcut":
                    files.append({"name": name, "path": str(row["id"]), "parent": folder,
                                  "size": int(row.get("size") or 0), "provider": "google"})
                if len(files) + len(folders) > MAX_LISTED:
                    raise CloudError("Cloud folder listing exceeds safe bounds")
            token = obj.get("nextPageToken", "")
            if not token:
                break
    return files


def dropbox_rpc(route, payload, token):
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    return as_json("https://api.dropboxapi.com/2/" + route,
                   headers, post=json.dumps(payload).encode("utf-8"))


def listing_dropbox(share):
    token = os.getenv("PACNI_DROPBOX_TOKEN", "")
    if not token:
        raise CloudError("Dropbox public link listing requires PACNI_DROPBOX_TOKEN (read-only API app)")
    files = []
    folders = [""]
    while folders:
        rel = folders.pop(0)
        obj = dropbox_rpc("files/list_folder",
                          {"path": rel, "shared_link": {"url": share}, "recursive": False}, token)
        while True:
            for row in obj.get("entries", []):
                name = row.get("name", "")
                if not isinstance(name, str) or not name or "/" in name or "\\" in name:
                    raise CloudError("Invalid remote filename")
                path = rel.rstrip("/") + "/" + name
                if row.get(".tag") == "folder":
                    folders.append(path)
                elif row.get(".tag") == "file":
                    files.append({"name": name, "path": path, "parent": rel or "/",
                                  "size": int(row.get("size", 0)), "provider": "dropbox"})
                if len(files) + len(folders) > MAX_LISTED:
                    raise CloudError("Cloud folder listing exceeds safe bounds")
            if not obj.get("has_more"):
                break
            obj = dropbox_rpc("files/list_folder/continue", {"cursor": obj["cursor"]}, token)
    return files


def list_cloud(share):
    source = validate_share(share)
    data = {"yandex": listing_yandex, "google": listing_google,
            "dropbox": listing_dropbox}[source](share)
    # We never treat a ZIP, report, image thumbnail or non-DICOM document as
    # MRI. DICOM extensionless files are common, so allow safe leaf names.
    return [x for x in data if x["size"] > 132 and x["size"] <= MAX_SINGLE_FILE
            and (Path(x["name"]).suffix.lower() in (".dcm", ".ima", "")
                 or x["name"].upper().startswith(("IM", "MR")))]


def yandex_media(share, file):
    obj = as_json(url_q("https://cloud-api.yandex.net/v1/disk/public/resources/download",
                        public_key=share, path=file["path"]))
    href = obj.get("href", "")
    validate_url(href)
    return href


def media_request(share, file, preview=False):
    provider = file["provider"]
    headers = {}
    post = None
    if provider == "yandex":
        url = yandex_media(share, file)
    elif provider == "google":
        key = os.getenv("PACNI_GOOGLE_API_KEY", "")
        if not key:
            raise CloudError("Missing Google API key")
        url = url_q("https://www.googleapis.com/drive/v3/files/" +
                    urllib.parse.quote(file["path"], safe=""), alt="media", key=key)
    else:
        token = os.getenv("PACNI_DROPBOX_TOKEN", "")
        if not token:
            raise CloudError("Missing Dropbox token")
        url = "https://content.dropboxapi.com/2/sharing/get_shared_link_file"
        headers["Authorization"] = "Bearer " + token
        headers["Dropbox-API-Arg"] = json.dumps({"url": share, "path": file["path"]})
        post = b""
    if preview:
        headers["Range"] = f"bytes=0-{MAX_PREVIEW - 1}"
    return url, headers, post


def preview_dicom(share, file, fetch=http_request):
    url, headers, post = media_request(share, file, preview=True)
    # A full response to a Range probe is forbidden, even if small:
    # selective mode must have reliable server-side range semantics.
    status, resp_headers, payload = fetch(url, headers=headers, post=post,
                                          max_bytes=MAX_PREVIEW)
    if status != 206:
        raise CloudError("Provider did not honor DICOM header Range request")
    return parse_dicom(payload)


def parse_dicom(data):
    """Small explicit/implicit little-endian DICOM header reader; never pixel data."""
    if len(data) < 132 or data[128:132] != b"DICM":
        raise CloudError("Unsupported DICOM preamble; cannot classify safely")
    wanted = {(0x0008,0x0060): "modality", (0x0008,0x103e): "description",
              (0x0018,0x1030): "protocol", (0x0020,0x000e): "series_uid",
              (0x0010,0x0010): "patient_name", (0x0010,0x0020): "patient_id"}
    cursor = 132
    result = {}
    mode = "explicit"
    ts = None
    for _ in range(18000):
        if cursor + 8 > len(data):
            break
        group, element = struct.unpack_from("<HH", data, cursor)
        if (group,element) == (0x7fe0,0x0010):
            break
        if group != 0x0002 and ts == "1.2.840.10008.1.2":
            mode = "implicit"
        if mode == "explicit":
            vr = data[cursor+4:cursor+6].decode("ascii", "replace")
            if not (vr.isalpha() and vr.isupper()):
                raise CloudError("Unsupported DICOM transfer syntax")
            if vr in EXPLICIT_LONG_VR:
                if cursor+12 > len(data): break
                length = struct.unpack_from("<I",data,cursor+8)[0]
                pos = cursor+12
            else:
                length = struct.unpack_from("<H",data,cursor+6)[0]
                pos = cursor+8
        else:
            length = struct.unpack_from("<I",data,cursor+4)[0]
            pos = cursor+8
        if length == 0xffffffff or length > MAX_PREVIEW:
            break
        if pos+length > len(data):
            break
        if (group,element) == (0x0002,0x0010):
            ts = data[pos:pos+length].decode("ascii", "replace").strip("\0 ")
            if ts not in {"1.2.840.10008.1.2", "1.2.840.10008.1.2.1"}:
                raise CloudError("Unsupported compressed/big-endian DICOM for header peek")
        if (group,element) in wanted:
            result[wanted[(group,element)]] = (
                data[pos:pos+length].decode("utf-8", "replace").strip("\0 ")
            )
        cursor = pos + length
        if {"series_uid","modality","description"}.issubset(result):
            break
    if not result.get("series_uid") or not result.get("modality"):
        raise CloudError("DICOM SeriesInstanceUID/Modality not present in preview")
    return result


def discover_series(share, files, fetch=http_request):
    by_parent = {}
    for entry in files:
        by_parent.setdefault(entry["parent"], []).append(entry)
    options = []
    for parent, members in sorted(by_parent.items()):
        if len(members) < 12:
            continue
        try:
            header = preview_dicom(share, members[0], fetch)
        except CloudError:
            continue
        contrast = " ".join([header.get("description",""),header.get("protocol","")])
        if header.get("modality") != "MR" or EXCLUDE.search(contrast) or not T1.search(contrast):
            continue
        options.append({"folder": parent, "count": len(members),
                        "size": sum(x["size"] for x in members),
                        "description": header.get("description") or header.get("protocol") or "T1",
                        "uid": header["series_uid"], "files": members})
    return options


def validate_entire_series(share, selected, fetch=http_request):
    entries = selected["files"]
    if (len(entries) > MAX_SERIES_FILES or selected["size"] > MAX_SERIES_BYTES):
        raise CloudError("Selected series exceeds safe intake limits")
    expected_uid = selected["uid"]
    same_patient = None
    for entry in entries:
        h = preview_dicom(share, entry, fetch)
        if h.get("modality") != "MR" or h["series_uid"] != expected_uid:
            raise CloudError("Mixed DICOM SeriesInstanceUID detected; no images downloaded")
        identity = (h.get("patient_id") or "").strip()
        if same_patient and identity and identity != same_patient:
            raise CloudError("Mixed DICOM PatientID; refused before full download")
        if identity:
            same_patient = identity
    return True


def download_file(share, file, dest, fetch=http_request):
    # Streaming with bounded size and no target overwrite; verify returned
    # exact byte count when provider supplies a trustworthy length.
    url, headers, post = media_request(share, file, preview=False)
    validate_url(url)
    req = urllib.request.Request(url, data=post, headers=headers,
                                 method="POST" if post is not None else "GET")
    total = 0
    try:
        with _OPENER.open(req, timeout=90) as response:
            if response.status != 200:
                raise CloudError("Cloud download response was not a full file")
            with dest.open("xb") as out:
                while True:
                    block = response.read(1024*1024)
                    if not block: break
                    total += len(block)
                    if total > MAX_SINGLE_FILE or total > file["size"] + 1024:
                        raise CloudError("Cloud file exceeded advertised maximum size")
                    out.write(block)
        if total != file["size"]:
            raise CloudError("Downloaded DICOM file size differs from metadata")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CloudError("Cloud file download failed") from None
    return total


def validate_expiry(date_str):
    try:
        day = datetime.date.fromisoformat(date_str)
    except (ValueError, TypeError):
        raise CloudError("Expected expiry date YYYY-MM-DD")
    if day < datetime.date.today() or day > datetime.date.today()+datetime.timedelta(days=14):
        raise CloudError("Public share must expire within the next 14 days")
    return day


def validate_study_name(name):
    name = name.strip()
    if (not 1 <= len(name) <= 90 or name in {".",".."}
            or not re.fullmatch(r"[A-Za-z0-9А-Яа-яЁё _.-]+", name)
            or name.endswith((".", " "))):
        raise CloudError("Use a short safe study label: letters, numbers, space, underscore, hyphen, dot")
    return name


def interactive(patient_id, share, expiry, input_fn=input):
    patient_id = patients.resolve_patient(patient_id)
    validate_expiry(expiry)
    provider = validate_share(share)
    print("Provider:", provider, "| metadata only until you approve a series.")
    print("No shared URL or patient header is logged or persisted.")
    entries = list_cloud(share)
    options = discover_series(share, entries)
    if not options:
        raise CloudError("No confidently identified T1 series: no full MRI downloaded")
    print("Candidate T1 DICOM series (header metadata, not validated whole-brain coverage):")
    for index,opt in enumerate(options,1):
        print(f'{index}. {opt["description"]} | {opt["count"]} files | '
              f'{opt["size"]/(1024**2):.1f} MiB')
    pick = input_fn("Choose number (Enter to cancel): ").strip()
    if not pick.isdecimal() or not 1 <= int(pick) <= len(options):
        raise CloudError("Cancelled")
    selected = options[int(pick)-1]
    if selected["size"] > MAX_SERIES_BYTES:
        raise CloudError("Selected DICOM series exceeds 4 GiB")
    print("Checking SeriesInstanceUID and PatientID of every selected DICOM header...")
    validate_entire_series(share, selected)
    new_name = validate_study_name(input_fn("Local MRI study folder name (date included): "))
    dest_root = patients.root_for(patient_id)
    dest = dest_root / new_name
    if dest.exists() or dest.is_symlink():
        raise CloudError("MRI study directory already exists; no overwrite")
    print(f'Will download ONLY {selected["count"]} selected DICOM files '
          f'({selected["size"]/(1024**2):.1f} MiB).')
    if input_fn("Type IMPORT to confirm correct patient and series: ").strip() != "IMPORT":
        raise CloudError("Not confirmed")
    staging = dest_root / (".cloud-import-" + uuid.uuid4().hex)
    staging.mkdir(parents=True)
    try:
        for n, entry in enumerate(selected["files"],1):
            suffix = re.sub(r"[^A-Za-z0-9_.-]", "_", entry["name"])[:70]
            name = f"{n:05d}_" + (suffix or "dicom")
            if not name.lower().endswith((".dcm", ".ima")):
                name += ".dcm"
            downloaded = staging / name
            download_file(share, entry, downloaded)
            # The source can change after metadata preflight. Verify the
            # received full file still belongs to the selected series.
            with downloaded.open("rb") as stream:
                measured = parse_dicom(stream.read(MAX_PREVIEW))
            if measured.get("series_uid") != selected["uid"] or measured.get("modality") != "MR":
                raise CloudError("Downloaded DICOM does not match selected series; import rolled back")
        # No shared links or personal DICOM identifiers in Git or public logs.
        (staging / "cloud-import.json").write_text(
            json.dumps({"provider":provider, "count":len(selected["files"]),
                        "series_uid_sha256":hashlib.sha256(selected["uid"].encode()).hexdigest(),
                        "selected_t1":selected["description"], "source":"read_only_public_link",
                        "expired_local":expiry}, ensure_ascii=False, indent=2), encoding="utf-8")
        if dest.exists():
            raise CloudError("Target study appeared during import")
        os.replace(staging, dest)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print("MRI imported into patient folder:", dest)
    print("Next: select this MRI study and run DICOM->T1 conversion (menu 1).")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["interactive"])
    parser.add_argument("patient")
    parser.add_argument("expires")
    args = parser.parse_args()
    share = os.environ.pop("PACNI_CLOUD_LINK", "")
    if not share:
        raise CloudError("Cloud share URL must be passed through temporary PACNI_CLOUD_LINK")
    interactive(args.patient, share, args.expires)


if __name__ == "__main__":
    try:
        main()
    except (CloudError, patients.PatientError, OSError, ValueError) as e:
        print("ERROR:", str(e), file=sys.stderr)
        sys.exit(1)
