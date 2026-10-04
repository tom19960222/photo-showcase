"""由真實 HTTP 驗收單張 JPG 交付與來源邊界。"""

from email.message import Message
from io import BytesIO
import os
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

from PIL import Image, ImageDraw

from support import ApplicationFixture


class DownloadTest(ApplicationFixture):
    def assert_not_found(self, base, path):
        with self.assertRaises(HTTPError) as caught:
            urlopen(base + path, timeout=2)
        with caught.exception as response:
            self.assertEqual(response.code, 404)
            html = response.read().decode("utf-8")
        self.assertNotIn(str(self.root), html)
        self.assertIn("找不到這個相簿或相片", html)

    def test_兩種下載共用發佈白名單並拒絕跨相簿非相片與編碼跳脫(self):
        self.photos("相簿甲", ("photo1.jpg", "notes.txt"))
        self.photos("未列出", ("photo1.jpg", "only-other.jpg"))
        self.photos("未設定")
        self.configuration([
            self.album(), self.album("未列出", "unlisted", status="unlisted"),
            self.album("停用", "disabled", status="disabled"),
        ])
        with self.serving() as base:
            for variant in ("display", "original"):
                for slug in ("album-a", "unlisted"):
                    with urlopen(base + f"/downloads/{slug}/{variant}/photo1.jpg", timeout=2) as response:
                        self.assertEqual(response.status, 200)
                for slug in ("disabled", "unconfigured", "missing"):
                    self.assert_not_found(base, f"/downloads/{slug}/{variant}/photo1.jpg")
                for filename in (
                    "missing.jpg", "notes.txt", "only-other.jpg", "../photo1.jpg",
                    "%2e%2e%2fphoto1.jpg", "%2e%2e%5cphoto1.jpg", "%00photo1.jpg",
                    "sub%2fphoto1.jpg", "%252e%252e%252fphoto1.jpg", "%ff.jpg",
                    "%2Fetc%2Fphoto1.jpg",
                ):
                    with self.subTest(類型=variant, 檔名=filename):
                        self.assert_not_found(base, f"/downloads/album-a/{variant}/{filename}")
            self.assert_not_found(base, "/downloads/album-a/raw/photo1.jpg")
        self.configuration([self.album(status="disabled")])
        with self.serving() as base:
            for variant in ("display", "original"):
                self.assert_not_found(base, f"/downloads/album-a/{variant}/photo1.jpg")
                self.assert_not_found(base, f"/downloads/unlisted/{variant}/photo1.jpg")

    def test_已產生下載後仍拒絕缺漏與越界連結並和圖片入口保持相同邊界(self):
        output = self.photos("相簿甲", ("photo1.jpg", "source.raw"))
        outside = self.photos("另一相簿")
        self.configuration([self.album()])
        filename = output / "photo1.jpg"
        original = filename.read_bytes()
        routes = ["/images/album-a/photo1.jpg", "/downloads/album-a/display/photo1.jpg", "/downloads/album-a/original/photo1.jpg"]
        with self.serving() as base:
            for route in routes:
                with urlopen(base + route, timeout=2) as response:
                    self.assertEqual(response.status, 200)
            filename.unlink()
            for route in routes:
                self.assert_not_found(base, route)
            for target in (outside / "photo1.jpg", output / "source.raw", filename):
                with self.subTest(替換=target.name):
                    filename.symlink_to(target)
                    try:
                        for route in routes:
                            self.assert_not_found(base, route)
                    finally:
                        filename.unlink()
            filename.write_bytes(original)
            for source in (output, output.parent, self.root):
                with self.subTest(替換層級=source.name):
                    saved = source.with_name(source.name + "-saved")
                    source.rename(saved)
                    source.symlink_to(saved, target_is_directory=True)
                    try:
                        for route in routes:
                            self.assert_not_found(base, route)
                    finally:
                        source.unlink()
                        saved.rename(source)

    def test_瀏覽用下載沿用圖片入口的最佳化JPG並保留完整直幅(self):
        filename = "直幅 #100%.JPEG"
        output = self.photos("相簿甲", (filename,))
        photo = Image.new("RGB", (3600, 2400), "white")
        drawing = ImageDraw.Draw(photo)
        drawing.rectangle((0, 0, 150, 2399), fill="red")
        drawing.rectangle((3449, 0, 3599, 2399), fill="blue")
        exif = Image.Exif()
        exif[274] = 6
        photo.save(output / filename, exif=exif, quality=95)
        before = (output / filename).read_bytes()
        self.configuration([self.album()])
        with self.serving() as base:
            with urlopen(base + "/images/album-a/" + quote(filename, safe="")) as response:
                image = response.read()
            with urlopen(base + "/downloads/album-a/display/" + quote(filename, safe="")) as response:
                self.assertEqual(response.headers["Content-Type"], "image/jpeg")
                disposition = Message()
                disposition["Content-Disposition"] = response.headers["Content-Disposition"]
                self.assertEqual(disposition.get_filename(), "直幅 #100%-display.jpg")
                content = response.read()
            self.assertEqual(content, image, "下載與看圖器使用同一份最佳化結果")
            with Image.open(BytesIO(content)) as display:
                self.assertEqual(display.format, "JPEG")
                self.assertEqual(display.size, (1600, 2400))
                self.assertGreater(display.getpixel((800, 0))[0], 200)
                self.assertGreater(display.getpixel((800, 2399))[2], 200)
            self.assertLess(len(content), len(before))
        self.assertEqual((output / filename).read_bytes(), before)

    def test_原始JPG下載保留中文特殊檔名中繼資料與全部來源位元組(self):
        filename = '旅途 "#100% &+.JPEG'
        output = self.photos("相簿甲", (filename,))
        exif = Image.Exif()
        exif[274] = 6
        exif[315] = "Photographer"
        Image.new("RGB", (1200, 800), "#bd794c").save(output / filename, exif=exif)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in output.iterdir()}
        self.configuration([self.album()])
        for path in [output, output / filename]:
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        try:
            with self.serving() as base:
                with urlopen(base + "/downloads/album-a/original/" + quote(filename, safe="")) as response:
                    self.assertEqual(response.headers["Content-Type"], "image/jpeg")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    disposition = Message()
                    disposition["Content-Disposition"] = response.headers["Content-Disposition"]
                    self.assertEqual(disposition.get_content_disposition(), "attachment")
                    self.assertEqual(disposition.get_filename(), filename)
                    content = response.read()
                    self.assertEqual(len(content), int(response.headers["Content-Length"]))
                    self.assertEqual(content, before[output / filename][0])
            self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in output.iterdir()}, "來源位元組、修改時間及檔案集合必須不變")
        finally:
            os.chmod(output, 0o755)
            os.chmod(output / filename, 0o644)


if __name__ == "__main__":
    unittest.main()
