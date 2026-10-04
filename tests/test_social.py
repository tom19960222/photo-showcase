"""不執行 JavaScript，透過真實 HTTP 驗收社群預覽與圖片。"""

from html.parser import HTMLParser
from io import BytesIO
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image
from support import ApplicationFixture


class PreviewHead(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.in_head = False
        self.metadata = {}
        self.canonical = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True
        if not self.in_head:
            return
        attributes = dict(attrs)
        if tag == "meta":
            name = attributes.get("property", attributes.get("name"))
            if name:
                self.metadata[name] = attributes.get("content")
        elif tag == "link" and attributes.get("rel") == "canonical":
            self.canonical = attributes.get("href")

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False


class SocialTest(ApplicationFixture):
    def command(self, port):
        command = super().command(port)
        if hasattr(self, "public_url"):
            command += ["--public-url", self.public_url.format(port=port)]
        return command

    def assert_preview_not_found(self, base, path):
        with self.assertRaises(HTTPError) as caught:
            urlopen(base + path, timeout=2)
        with caught.exception as response:
            self.assertEqual(response.status, 404)
            html = response.read().decode("utf-8")
        head = PreviewHead(html)
        self.assertFalse(any(name.startswith("og:") for name in head.metadata))
        self.assertIsNone(head.canonical)
        self.assertNotIn(str(self.root), html)
        self.assertNotIn("已停用作品", html)
        self.assertIn("找不到這個相簿或相片", html)

    def test_預覽與JPG共用三種發佈狀態且停用後不沿用舊圖(self):
        self.photos("相簿甲")
        self.photos("未列出")
        self.photos("未設定")
        self.configuration([
            self.album(), self.album("未列出", "unlisted", title="持有網址作品", status="unlisted"),
            self.album("已停用", "disabled", title="已停用作品", status="disabled"),
        ])
        with self.serving() as base:
            self.assertNotIn("unlisted", self.page(base))
            self.assertNotIn("持有網址作品", self.page(base))
            for slug, title in (("album-a", "相簿甲"), ("unlisted", "持有網址作品")):
                for suffix, expected_title in (("", title), ("/photo1.jpg", "photo1.jpg · " + title)):
                    with self.subTest(相簿=slug, 網址=suffix):
                        head = PreviewHead(self.page(base, "/albums/" + slug + suffix))
                        self.assertEqual(head.metadata.get("og:title"), expected_title)
                        with urlopen(head.metadata["og:image"], timeout=2) as response:
                            self.assertEqual(response.status, 200)
            for path in (
                "/albums/disabled", "/albums/unknown", "/albums/%2e%2e%2fconfig",
                "/albums/disabled/photo1.jpg", "/albums/unknown/photo1.jpg",
                "/albums/album-a/missing.jpg", "/albums/album-a/%FF.jpg",
                "/albums/album-a/%2e%2e%2fphoto1.jpg", "/albums/album-a/%00photo1.jpg",
                "/images/disabled/photo1.jpg", "/images/unknown/photo1.jpg",
                "/images/album-a/missing.jpg", "/images/album-a/sub%2Fphoto1.jpg",
            ):
                with self.subTest(無效網址=path):
                    self.assert_preview_not_found(base, path)
        self.configuration([self.album(title="已停用作品", status="disabled")])
        with self.serving() as base:
            for slug in ("album-a", "unlisted"):
                for path in (f"/albums/{slug}", f"/albums/{slug}/photo1.jpg", f"/images/{slug}/photo1.jpg"):
                    with self.subTest(停用或移除=path):
                        self.assert_preview_not_found(base, path)

    def test_已提供預覽後來源遭越界替換則頁面與圖片皆回傳404(self):
        output = self.photos("相簿甲")
        outside = self.photos("未設定")
        self.configuration([self.album()])
        with self.serving() as base:
            head = PreviewHead(self.page(base, "/albums/album-a"))
            with urlopen(head.metadata["og:image"], timeout=2) as response:
                self.assertEqual(response.status, 200)
            photo = output / "photo1.jpg"
            photo.unlink()
            photo.symlink_to(outside / "photo1.jpg")
            for path in ("/albums/album-a", "/albums/album-a/photo1.jpg", "/images/album-a/photo1.jpg"):
                with self.subTest(來源已無效=path):
                    self.assert_preview_not_found(base, path)

    def test_公開站點網址僅接受不含路徑帳密或查詢的HTTP來源(self):
        self.configuration([])
        for value in (
            "", "photos.example.test", "https://", "javascript:alert(1)",
            "https://photos.example.test/albums", "https://user:password@photos.example.test",
            "https://photos.example.test?", "https://photos.example.test#preview",
            "https://photos.example.test:invalid", "https://photos.example.test:99999",
            "https://photos.example.test\n", "https://photos.example.test\\other",
        ):
            with self.subTest(公開網址=value):
                self.public_url = value
                self.assert_startup_fails()

    def test_公開站點網址可獨立於監聽位置且不受請求標頭影響(self):
        self.photos("相簿甲")
        self.configuration([self.album()])
        self.public_url = "http://localhost:{port}/"
        with self.serving() as base:
            origin = base.replace("127.0.0.1", "localhost")
            head = PreviewHead(self.page(base, "/albums/album-a/photo1.jpg"))
            self.assertEqual(head.metadata.get("og:url"), origin + "/albums/album-a/photo1.jpg")
            self.assertEqual(head.metadata.get("og:image"), origin + "/images/album-a/photo1.jpg")
            with urlopen(head.metadata["og:image"], timeout=2) as response:
                self.assertEqual(response.status, 200, "公開 origin 的預覽 JPG 必須能實際讀取")
        self.public_url = "https://photos.example.test:8443/"
        with self.serving() as base:
            request = Request(base + "/albums/album-a", headers={
                "Host": "untrusted.example", "X-Forwarded-Host": "untrusted.example", "X-Forwarded-Proto": "http",
            })
            with urlopen(request, timeout=2) as response:
                head = PreviewHead(response.read().decode("utf-8"))
            self.assertEqual(head.metadata.get("og:url"), "https://photos.example.test:8443/albums/album-a")
            self.assertEqual(head.metadata.get("og:image"), "https://photos.example.test:8443/images/album-a/photo1.jpg")

    def test_相片HTTP預覽採目前相片並安全編碼中文及特殊檔名(self):
        filename = '相片 & <2> " #100%.JPEG'
        encoded = '%E7%9B%B8%E7%89%87%20%26%20%3C2%3E%20%22%20%23100%25.JPEG'
        output = self.photos("相簿甲", ("cover.jpg", filename))
        Image.new("RGB", (600, 900), "green").save(output / filename)
        self.configuration([self.album(title='午後 & <散步> "相簿"', cover="cover.jpg")])
        with self.serving() as base:
            path = f"/albums/album-a/{encoded}"
            html = self.page(base, path + "?campaign=shared")
            head = PreviewHead(html)
            self.assertEqual(head.metadata.get("og:title"), filename + ' · 午後 & <散步> "相簿"')
            self.assertEqual(head.metadata.get("og:url"), base + path)
            self.assertEqual(head.canonical, base + path)
            self.assertEqual(head.metadata.get("og:image"), base + "/images/album-a/" + encoded)
            self.assertIn("相片 &amp; &lt;2&gt; &quot; #100%.JPEG", html)
            self.assertNotIn('<2>', html, "特殊字元不得變成 HTML 標籤")
            with urlopen(head.metadata["og:image"], timeout=2) as response:
                with Image.open(BytesIO(response.read())) as image:
                    self.assertEqual(image.size, (600, 900), "單張預覽不得回退成相簿封面")
            cover = PreviewHead(self.page(base, "/albums/album-a/cover.jpg"))
            self.assertEqual(cover.metadata.get("og:image"), base + "/images/album-a/cover.jpg")
            self.assertNotEqual(cover.metadata.get("og:title"), head.metadata["og:title"])

    def test_相簿HTTP預覽使用各自標題與指定或自然排序封面(self):
        for folder in ("指定封面", "回退封面"):
            output = self.photos(folder, ("photo10.jpg", "photo2.JPG"))
            Image.new("RGB", (800, 1200), "red").save(output / "photo10.jpg")
            Image.new("RGB", (1200, 800), "blue").save(output / "photo2.JPG")
        self.configuration([
            self.album("指定封面", "chosen", title='午後 & <散步> "相簿"', cover="photo10.jpg"),
            self.album("回退封面", "fallback", title="回退封面相簿"),
        ])
        with self.serving() as base:
            for slug, title, filename, size in (
                ("chosen", '午後 & <散步> "相簿"', "photo10.jpg", (800, 1200)),
                ("fallback", "回退封面相簿", "photo2.JPG", (1200, 800)),
            ):
                with self.subTest(相簿=slug):
                    head = PreviewHead(self.page(base, f"/albums/{slug}?campaign=shared"))
                    self.assertEqual(head.metadata.get("og:title"), title)
                    self.assertEqual(head.metadata.get("og:url"), f"{base}/albums/{slug}")
                    self.assertEqual(head.canonical, f"{base}/albums/{slug}")
                    self.assertEqual(head.metadata.get("og:image"), f"{base}/images/{slug}/{filename}")
                    with urlopen(head.metadata["og:image"], timeout=2) as response:
                        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
                        with Image.open(BytesIO(response.read())) as image:
                            self.assertEqual(image.size, size, "實際預覽圖必須來自所選封面")


if __name__ == "__main__":
    unittest.main()
