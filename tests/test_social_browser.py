"""以真實瀏覽器確認 HTTP 預覽連結開啟原本的相簿與相片。"""

import os
from pathlib import Path
import unittest

from playwright.sync_api import expect, sync_playwright

from support import ApplicationFixture
from test_social import PreviewHead


class SocialBrowserTest(ApplicationFixture):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_停用JS仍讀到預覽且連結開啟正確內容並維持未列出(self):
        filename = '相片 & <2> " #100%.JPEG'
        encoded = '%E7%9B%B8%E7%89%87%20%26%20%3C2%3E%20%22%20%23100%25.JPEG'
        self.photos("公開", ("cover.jpg", filename))
        self.photos("未列出", ("cover.jpg", filename))
        self.configuration([
            self.album("公開", "public", title="公開散步", cover="cover.jpg"),
            self.album("未列出", "unlisted", title="持有網址作品", status="unlisted", cover="cover.jpg"),
        ])
        with self.serving() as base:
            crawler = self.browser.new_page(java_script_enabled=False)
            visitor = self.browser.new_page(viewport={"width": 390, "height": 900})
            try:
                for slug, title in (("public", "公開散步"), ("unlisted", "持有網址作品")):
                    with self.subTest(相簿=slug):
                        album = PreviewHead(self.page(base, "/albums/" + slug))
                        html = self.page(base, f"/albums/{slug}/{encoded}")
                        photo = PreviewHead(html)
                        crawler.goto(photo.metadata["og:url"])
                        self.assertEqual(crawler.locator('head meta[property="og:title"]').get_attribute("content"), filename + " · " + title)
                        self.assertEqual(crawler.locator('head meta[property="og:image"]').get_attribute("content"), base + f"/images/{slug}/{encoded}")
                        visitor.goto(album.metadata["og:url"])
                        expect(visitor.get_by_role("heading", name=title, exact=True)).to_be_visible()
                        expect(visitor.get_by_role("dialog", name="相片看圖器")).to_be_hidden()
                        visitor.goto(photo.metadata["og:url"])
                        dialog = visitor.get_by_role("dialog", name="相片看圖器")
                        expect(dialog).to_be_visible()
                        expect(dialog.locator(".viewer-filename")).to_have_text(filename)
                        expect(dialog.locator(".viewer-stage img")).to_have_attribute("src", f"/images/{slug}/{encoded}")
                        visitor.wait_for_function("document.querySelector('.viewer-stage img').naturalWidth > 0")
                        destination = os.environ.get("PHOTO_SHOWCASE_EVIDENCE")
                        if destination:
                            directory = Path(destination)
                            directory.mkdir(parents=True, exist_ok=True)
                            (directory / f"social-{slug}-photo.html").write_text(html, encoding="utf-8")
                            visitor.screenshot(path=str(directory / f"social-{slug}-photo.png"))
                        visitor.keyboard.press("Escape")
                        expect(visitor).to_have_url(album.metadata["og:url"])
                visitor.goto(base)
                expect(visitor.get_by_role("heading", name="公開散步", exact=True)).to_be_visible()
                self.assertEqual(visitor.get_by_text("持有網址作品", exact=True).count(), 0)
                self.assertEqual(visitor.locator('a[href*="unlisted"]').count(), 0)
                self.assertEqual(visitor.get_by_role("button", name="分享").count(), 0)
            finally:
                crawler.close()
                visitor.close()


if __name__ == "__main__":
    unittest.main()
