"""以真實 HTTP 與合成相片驗收自然比例相簿。"""

from html.parser import HTMLParser
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

from support import ApplicationFixture


class AlbumMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.images = []
        self.links = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "img":
            self.images.append(attributes)
        elif tag == "a":
            self.links.append(attributes)

    def handle_data(self, data):
        self.text.append(data)


class AlbumTest(ApplicationFixture):
    def test_固定網址顯示全部自然排序相片並保留封面及中文名稱(self):
        expected = (
            "photo1.jpg", "photo2.JPG", "photo3.jpg", "photo4.jpg", "photo5.jpg",
            "photo6.jpg", "photo7.jpg", "photo8.jpg", "photo9.jpg", "photo10.jpeg",
            "photo11.jpg", "旅途 & <記錄> #100%.JPEG",
        )
        output = self.photos("相簿甲", tuple(reversed(expected)) + ("source.raw", "notes.txt"))
        nested = output / "子資料夾"
        nested.mkdir()
        (nested / "nested.jpg").write_bytes((output / "photo1.jpg").read_bytes())
        before = {path.relative_to(output): (path.read_bytes(), path.stat().st_mtime_ns) for path in output.rglob("*") if path.is_file()}
        self.configuration([self.album(title="旅途 & <記錄>", date="2025-03-15", cover="photo10.jpeg")])
        with self.serving() as base:
            markup = AlbumMarkup(self.page(base, "/albums/album-a"))
            self.assertIn("旅途 & <記錄>", markup.text)
            self.assertIn("2025-03-15", markup.text)
            self.assertIn("12 張相片", " ".join(markup.text))
            self.assertTrue(any(link.get("href") == "/" for link in markup.links))
            self.assertEqual([image["src"] for image in markup.images], [
                "/images/album-a/" + quote(filename, safe="") for filename in expected
            ], "封面仍為一般相片，且完整列出自然排序相片")
            for image in markup.images:
                with urlopen(base + image["src"], timeout=2) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn("image/jpeg", response.headers["Content-Type"])
        after = {path.relative_to(output): (path.read_bytes(), path.stat().st_mtime_ns) for path in output.rglob("*") if path.is_file()}
        self.assertEqual(before, after, "瀏覽相簿與全部 JPG 不得修改來源")

    def test_未列出相簿可直接存取但停用未知及編碼越界皆回傳404(self):
        self.photos("相簿甲")
        self.photos("未列出")
        self.photos("未設定")
        self.configuration([
            self.album(), self.album("未列出", "unlisted", title="持有網址作品", status="unlisted"),
            self.album("停用", "disabled", title="停用作品", status="disabled"),
        ])
        with self.serving() as base:
            home = self.page(base)
            self.assertNotIn("持有網址作品", home)
            self.assertNotIn("/albums/unlisted", home)
            self.assertIn("持有網址作品", self.page(base, "/albums/unlisted"))
            for path in (
                "/albums/disabled", "/albums/unknown", "/albums/" + quote("未設定"),
                "/albums/%2e%2e", "/albums/%2e%2e%2fconfig", "/albums/%2Falbum-a",
                "/albums/album-a%2f..", "/albums/album-a%5c..", "/albums/album-a%00",
                "/albums/%252e%252e%252fconfig", "/albums/album-a/missing.jpg",
            ):
                with self.subTest(入口=path):
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(base + path, timeout=2)
                    with caught.exception as response:
                        self.assertEqual(response.status, 404)
                        html = response.read().decode("utf-8")
                    self.assertIn("找不到這個相簿或相片", html)
                    self.assertIn("返回相簿列表", html)
                    self.assertNotIn(str(self.root), html)
                    self.assertNotIn("持有網址作品", html)
        self.configuration([self.album(status="disabled")])
        with self.serving() as base:
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + "/albums/album-a", timeout=2)
            caught.exception.close()
            self.assertEqual(caught.exception.code, 404, "重新啟動後停用相簿不得保留舊頁面")


if __name__ == "__main__":
    unittest.main()
