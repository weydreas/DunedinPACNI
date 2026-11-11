"""Provider contract smoke tests: mocks only, no links, tokens, or PHI."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import remote_mri as cloud

class ProviderContracts(unittest.TestCase):
    def test_yandex_nested_public_metadata_listing(self):
        calls=[]
        def fake_json(url, headers=None, post=None):
            calls.append(url)
            if "path=%2FT1" in url:
                return {"type":"dir","_embedded":{"items":[
                    {"name":"IM0001.dcm","type":"file","size":1024}]}}
            return {"type":"dir","_embedded":{"items":[{"name":"T1","type":"dir"}]}}
        with mock.patch.object(cloud,"as_json",side_effect=fake_json):
            files=cloud.listing_yandex("https://disk.yandex.ru/d/TEST")
        self.assertEqual(len(files),1)
        self.assertEqual(files[0]["path"],"/T1/IM0001.dcm")
        self.assertTrue(all("cloud-api.yandex.net" in url for url in calls))

    def test_google_resource_key_and_shared_drive_listing(self):
        calls=[]
        def fake_json(url,headers=None,post=None):
            calls.append((url,headers))
            return {"files":[{"id":"file123","name":"IM0001.dcm",
                "mimeType":"application/dicom","size":"1234",
                "resourceKey":"0-child"}]}
        link="https://drive.google.com/drive/folders/dir123?resourcekey=0-parent"
        with mock.patch.dict(os.environ,{"PACNI_GOOGLE_API_KEY":"TEST"}), \
             mock.patch.object(cloud,"as_json",side_effect=fake_json):
            files=cloud.listing_google(link)
        self.assertEqual(len(files),1)
        self.assertEqual(files[0]["resource_key"],"0-child")
        self.assertEqual(calls[0][1],
                         {"X-Goog-Drive-Resource-Keys":"dir123/0-parent"})
        self.assertIn("supportsAllDrives=true",calls[0][0])

    def test_dropbox_token_and_public_folder_listing(self):
        def fake_rpc(route,payload,token):
            self.assertEqual(token,"dummy-read-token")
            self.assertEqual(payload["shared_link"]["url"],
                             "https://www.dropbox.com/sh/TEST")
            return {"entries":[{".tag":"file","name":"IM0001.dcm",
                               "size":1234}],"has_more":False}
        with mock.patch.dict(os.environ,{"PACNI_DROPBOX_TOKEN":"dummy-read-token"}), \
             mock.patch.object(cloud,"dropbox_rpc",side_effect=fake_rpc):
            files=cloud.listing_dropbox("https://www.dropbox.com/sh/TEST")
        self.assertEqual(files[0]["path"],"/IM0001.dcm")

    def test_range_not_honored_rejects_before_download(self):
        f={"provider":"yandex","name":"IM0001.dcm","path":"/IM0001.dcm",
           "parent":"/","size":100000}
        with mock.patch.object(cloud,"media_request",return_value=(
              "https://cloud-api.yandex.net/asset",{},None)):
            with self.assertRaisesRegex(cloud.CloudError,"Range"):
                cloud.preview_dicom("https://disk.yandex.ru/d/TEST",f,
                                    fetch=lambda *args,**kwargs: (200,{},b"short"))

if __name__=="__main__":
    unittest.main()
