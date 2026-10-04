"""以正式 HTTP 相片網址驗收檔名識別與發佈邊界。"""

import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

from support import ApplicationFixture
from test_album import AlbumMarkup


class ViewerTest(ApplicationFixture):
    def test_相片網址共用白名單並拒絕不存在非相片越界及替換來源(self):
        output = self.photos("相簿甲", ("photo1.jpg", "notes.txt"))
        outside = self.photos("未列出", ("other.jpg",))
        self.photos("未設定")
        self.configuration([
            self.album(), self.album("未列出", "unlisted", status="unlisted"),
            self.album("停用", "disabled", status="disabled"),
        ])

        def missing(path):
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + path, timeout=2)
            with caught.exception as response:
                self.assertEqual(response.status, 404)
                html = response.read().decode("utf-8")
            self.assertIn("找不到這個相簿或相片", html)
            self.assertIn('href="/"', html)
            self.assertIn("返回相簿列表", html)
            self.assertNotIn(str(self.root), html)

        with self.serving() as base:
            self.assertIn("other.jpg", self.page(base, "/albums/unlisted/other.jpg"))
            self.page(base, "/albums/album-a/photo1.jpg")
            for path in (
                "/albums/disabled/photo1.jpg", "/albums/unknown/photo1.jpg",
                "/albums/album-a/missing.jpg", "/albums/album-a/other.jpg", "/albums/album-a/notes.txt",
                "/albums/album-a/../photo1.jpg", "/albums/album-a/%2e%2e%2fphoto1.jpg",
                "/albums/album-a/%2e%2e%5cphoto1.jpg", "/albums/album-a/%00photo1.jpg",
                "/albums/album-a/sub%2fphoto1.jpg", "/albums/album-a/%252e%252e%252fphoto1.jpg",
                "/albums/album-a/%FF.jpg",
            ):
                with self.subTest(入口=path):
                    missing(path)
            (output / "photo1.jpg").unlink()
            (output / "photo1.jpg").symlink_to(outside / "other.jpg")
            missing("/albums/album-a/photo1.jpg")
        self.configuration([self.album(status="disabled")])
        with self.serving() as base:
            missing("/albums/album-a/photo1.jpg")

    def test_相片固定網址使用實際編碼檔名並不受標題及排序變更影響(self):
        filename = "旅途 & <記錄> #100%.JPEG"
        output = self.photos("相簿甲", ("photo2.JPG", "photo10.jpeg", filename))
        self.configuration([self.album(title="午後 & 散步")])
        path = "/albums/album-a/" + quote(filename, safe="")
        with self.serving() as base:
            markup = AlbumMarkup(self.page(base, "/albums/album-a"))
            self.assertIn(path, [link.get("href") for link in markup.links])
            photo = self.page(base, path)
            self.assertIn("旅途 &amp; &lt;記錄&gt; #100%.JPEG", photo)
            self.assertIn("午後 &amp; 散步", photo)
        (output / "photo0.jpg").write_bytes((output / "photo2.JPG").read_bytes())
        self.configuration([self.album(title="散步新名稱")])
        with self.serving() as base:
            photo = self.page(base, path)
            self.assertIn("散步新名稱", photo)
            self.assertIn("旅途 &amp; &lt;記錄&gt; #100%.JPEG", photo)


if __name__ == "__main__":
    unittest.main()
