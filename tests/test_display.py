"""透過真實 HTTP 驗證完整比例的瀏覽用 JPG。"""

from html.parser import HTMLParser
from io import BytesIO
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from urllib.parse import quote

from PIL import Image, ImageDraw
from support import ApplicationFixture


class CoverImages(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.images = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.images.append(dict(attrs))


class DisplayTest(ApplicationFixture):
    def test_首頁封面透過HTTP交付清晰且完整比例的瀏覽用JPG(self):
        output = self.photos("相簿甲", ("photo10.jpg", "photo2.JPG"))
        photo = Image.new("RGB", (3600, 2400), "#ded3b7")
        draw = ImageDraw.Draw(photo)
        draw.rectangle((0, 0, 150, 2399), fill="red")
        draw.rectangle((3449, 0, 3599, 2399), fill="blue")
        photo.save(output / "photo2.JPG", quality=95)
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in output.iterdir()}
        self.configuration([self.album()])
        with self.serving() as base:
            images = CoverImages(self.page(base)).images
            self.assertEqual(len(images), 1, "首頁必須呈現實際封面")
            self.assertIn("photo2.JPG", images[0]["src"], "未指定封面時使用自然排序第一張")
            with urlopen(base + images[0]["src"]) as response:
                self.assertEqual(response.headers["Content-Type"], "image/jpeg")
                content = response.read()
            with Image.open(BytesIO(content)) as display:
                self.assertEqual(display.format, "JPEG")
                self.assertEqual(display.width / display.height, 1.5)
                self.assertGreaterEqual(display.width, 1600, "大圖應足以清晰呈現桌面封面")
                self.assertLess(display.width, 3600, "瀏覽用 JPG 應合理縮放")
                self.assertGreater(display.getpixel((0, display.height // 2))[0], 200)
                self.assertGreater(display.getpixel((display.width - 1, display.height // 2))[2], 200)
            self.assertLess(len(content), len(before["photo2.JPG"][0]))
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in output.iterdir()}
        self.assertEqual(before, after, "來源位元組、修改時間及檔案集合必須不變")

    def test_指定中文封面正確套用EXIF方向並保留自然直幅(self):
        filename = "直幅 # 100%.JPEG"
        output = self.photos("相簿甲", ("photo1.jpg", filename))
        photo = Image.new("RGB", (1200, 800), "white")
        draw = ImageDraw.Draw(photo)
        draw.rectangle((0, 0, 150, 799), fill="red")
        exif = Image.Exif()
        exif[274] = 6
        photo.save(output / filename, exif=exif)
        original = (output / filename).read_bytes()
        self.configuration([self.album(cover=filename)])
        with self.serving() as base:
            images = CoverImages(self.page(base)).images
            self.assertEqual(len(images), 1)
            self.assertIn(quote(filename, safe=""), images[0]["src"])
            with urlopen(base + images[0]["src"]) as response:
                with Image.open(BytesIO(response.read())) as display:
                    self.assertEqual(display.size, (800, 1200), "EXIF 旋轉後應為完整直幅")
                    self.assertGreater(display.getpixel((400, 0))[0], 200)
                    self.assertLess(display.getpixel((400, 0))[1], 40)
                    self.assertNotIn(display.getexif().get(274), (6, 8), "衍生圖不得再次旋轉")
        self.assertEqual((output / filename).read_bytes(), original)

    def assert_not_found(self, base, path):
        with self.assertRaises(HTTPError) as caught:
            urlopen(base + path)
        with caught.exception as response:
            self.assertEqual(response.code, 404)
            html = response.read().decode("utf-8")
        self.assertNotIn(str(self.root), html)
        self.assertIn("找不到這個相簿或相片", html)

    def test_圖片入口共用發佈白名單並拒絕編碼越界與非相片(self):
        self.photos("相簿甲", ("photo1.jpg", "notes.txt"))
        self.photos("未列出")
        self.photos("未設定")
        self.configuration([
            self.album(), self.album("未列出", "unlisted", status="unlisted"),
            self.album("已停用", "disabled", status="disabled"),
        ])
        with self.serving() as base:
            for slug in ("album-a", "unlisted"):
                with urlopen(base + f"/images/{slug}/photo1.jpg") as response:
                    self.assertEqual(response.status, 200)
            for path in (
                "/images/disabled/photo1.jpg", "/images/unknown/photo1.jpg",
                "/images/album-a/missing.jpg", "/images/album-a/notes.txt",
                "/images/album-a/../photo1.jpg", "/images/album-a/%2e%2e%2fphoto1.jpg",
                "/images/album-a/%2e%2e%5cphoto1.jpg", "/images/album-a/%00photo1.jpg",
                "/images/album-a/sub%2fphoto1.jpg", "/images/album-a/%252e%252e%252fphoto1.jpg",
            ):
                with self.subTest(入口=path):
                    self.assert_not_found(base, path)
        self.configuration([self.album(status="disabled")])
        with self.serving() as base:
            self.assert_not_found(base, "/images/album-a/photo1.jpg")

    def test_已成功產生圖片後仍拒絕替換來源連結(self):
        output = self.photos("相簿甲", ("photo1.jpg", "source.raw"))
        outside = self.photos("另一相簿")
        self.configuration([self.album()])
        filename = output / "photo1.jpg"
        content = filename.read_bytes()
        with self.serving() as base:
            image_path = CoverImages(self.page(base)).images[0]["src"]
            with urlopen(base + image_path) as response:
                self.assertEqual(response.status, 200)
            for target in (outside / "photo1.jpg", output / "source.raw", filename):
                with self.subTest(替換=target.name):
                    filename.unlink()
                    filename.symlink_to(target)
                    self.assert_not_found(base, image_path)
            filename.unlink()
            filename.write_bytes(content)
            for source in (output, output.parent, self.root):
                with self.subTest(替換層級=source.name):
                    saved = source.with_name(source.name + "-saved")
                    source.rename(saved)
                    source.symlink_to(saved, target_is_directory=True)
                    try:
                        self.assert_not_found(base, image_path)
                    finally:
                        source.unlink()
                        saved.rename(source)


if __name__ == "__main__":
    unittest.main()
