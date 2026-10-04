"""以真實 HTTP 交付並解壓相簿 ZIP，核對原始來源。"""

from io import BytesIO
import os
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from zipfile import ZipFile

from support import ApplicationFixture


class ArchiveTest(ApplicationFixture):
    def assert_not_found(self, base, path):
        with self.assertRaises(HTTPError) as caught:
            urlopen(base + path, timeout=5)
        with caught.exception as response:
            self.assertEqual(response.status, 404)
            self.assertIn("text/html", response.headers["Content-Type"])
            content = response.read().decode()
        self.assertIn("找不到這個相簿或相片", content)
        self.assertNotIn(str(self.root), content)

    def test_ZIP恰好包含全部原始JPG含封面及中文檔名且來源唯讀(self):
        filenames = ("photo2.JPG", "photo10.jpeg", "旅途 & <記錄> #100%.JPEG")
        output = self.photos("相簿甲", filenames + ("source.raw", "notes.txt"))
        (output / "子資料夾").mkdir()
        (output / "子資料夾" / "nested.jpg").write_bytes("不應交付".encode())
        self.photos("另一相簿", ("other.jpg",))
        for number, name in enumerate(filenames):
            with (output / name).open("ab") as photo:
                photo.write(f"來源相片 {number}".encode())
        before = {path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in self.root.rglob("*") if path.is_file()}
        paths = list(self.root.rglob("*"))
        for path in paths:
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        self.configuration([self.album(cover="photo10.jpeg")])
        try:
            with self.serving() as base:
                with urlopen(base + "/archives/album-a.zip", timeout=5) as response:
                    self.assertEqual(response.headers["Content-Type"], "application/zip")
                    self.assertIn('attachment; filename="album-a.zip"', response.headers["Content-Disposition"])
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    content = response.read()
                    self.assertEqual(len(content), int(response.headers["Content-Length"]))
                with ZipFile(BytesIO(content)) as archive:
                    self.assertEqual(archive.namelist(), list(filenames))
                    self.assertIsNone(archive.testzip())
                    for name in filenames:
                        self.assertEqual(archive.read(name), (output / name).read_bytes())
            after = {path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns)
                     for path in self.root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(set(paths), set(self.root.rglob("*")), "ZIP 或暫存檔不得寫回來源")
        finally:
            for path in paths:
                os.chmod(path, 0o755 if path.is_dir() else 0o644)

    def test_ZIP入口只允許已發佈相簿且已下載過也不得繞過停用(self):
        self.photos("相簿甲")
        self.photos("未列出")
        self.photos("未設定")
        self.configuration([
            self.album(), self.album("未列出", "unlisted", status="unlisted"),
            self.album("停用", "disabled", status="disabled"),
        ])
        with self.serving() as base:
            for slug in ("album-a", "unlisted"):
                with urlopen(base + f"/archives/{slug}.zip", timeout=5) as response:
                    with ZipFile(BytesIO(response.read())) as archive:
                        self.assertEqual(archive.namelist(), ["photo1.jpg"])
            for path in (
                "/archives/disabled.zip", "/archives/unconfigured.zip", "/archives/missing.zip",
                "/archives/%2e%2e.zip", "/archives/%2e%2e%2falbum-a.zip",
                "/archives/album-a%2f..zip", "/archives/album-a%5c..zip",
                "/archives/album-a%00.zip", "/archives/%252e%252e%252falbum-a.zip",
                "/archives/album-a.zip/../unlisted.zip", "/archives/album-a.zip/photo1.jpg",
            ):
                with self.subTest(入口=path):
                    self.assert_not_found(base, path)
        self.configuration([self.album(status="disabled")])
        with self.serving() as base:
            self.assert_not_found(base, "/archives/album-a.zip")
            self.assert_not_found(base, "/archives/unlisted.zip")

    def test_來源中途缺漏或被連結替換時不得交付不完整或越界ZIP(self):
        output = self.photos("相簿甲", ("photo1.jpg", "photo2.jpg", "source.raw"))
        outside = self.photos("另一相簿")
        self.configuration([self.album()])
        path = "/archives/album-a.zip"
        photo = output / "photo2.jpg"
        original = photo.read_bytes()
        with self.serving() as base:
            with urlopen(base + path, timeout=5) as response:
                self.assertEqual(response.status, 200)
            photo.unlink()
            self.assert_not_found(base, path)
            for target in (outside / "photo1.jpg", output / "source.raw", photo):
                with self.subTest(替換相片=target.name):
                    photo.symlink_to(target)
                    try:
                        self.assert_not_found(base, path)
                    finally:
                        photo.unlink()
            photo.write_bytes(original)
            for source in (output, output.parent, self.root):
                with self.subTest(替換來源=source.name):
                    saved = source.with_name(source.name + "-saved")
                    source.rename(saved)
                    source.symlink_to(saved, target_is_directory=True)
                    try:
                        self.assert_not_found(base, path)
                    finally:
                        source.unlink()
                        saved.rename(source)
            with urlopen(base + path, timeout=5) as response:
                with ZipFile(BytesIO(response.read())) as archive:
                    self.assertEqual(archive.namelist(), ["photo1.jpg", "photo2.jpg"])
                    self.assertEqual(archive.read("photo2.jpg"), original)


if __name__ == "__main__":
    unittest.main()
