"""以真實瀏覽器驗收相簿入口、自然比例與載入復原。"""

import os
from pathlib import Path
import unittest

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

from support import ApplicationFixture


class AlbumBrowserTest(ApplicationFixture):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def mixed_album(self):
        filenames = (
            "photo1.jpg", "photo2.JPG", "photo3.jpg", "photo4.jpg", "photo5.jpg",
            "photo6.jpg", "photo7.jpg", "photo8.jpg", "photo9.jpg", "photo10.jpeg",
            "photo11.jpg", "旅途 & <記錄> #100%.JPEG",
        )
        output = self.photos("相簿甲", tuple(reversed(filenames)))
        sizes = ((1200, 800), (800, 1200), (1000, 1000), (1600, 700))
        colors = ("#ab6d46", "#507e89", "#818965", "#c29364")
        for number, filename in enumerate(filenames):
            size = sizes[number % 4]
            photo = Image.new("RGB", size, colors[number % 4])
            drawing = ImageDraw.Draw(photo)
            drawing.rectangle((0, 0, size[0] - 1, size[1] - 1), outline="#f6dfab", width=24)
            drawing.ellipse((size[0] // 3, size[1] // 3, size[0] * 2 // 3, size[1] * 2 // 3), fill="#dac9a9")
            photo.save(output / filename)
        self.configuration([self.album(title="午後散步", date="2025-03-15", cover="photo10.jpeg")])
        return filenames

    def save_evidence(self, page, name):
        destination = os.environ.get("PHOTO_SHOWCASE_EVIDENCE")
        if destination:
            directory = Path(destination)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(directory / f"{name}.png"), full_page=True)

    def test_首頁封面與標題可開啟固定相簿網址並重新整理(self):
        self.photos("相簿甲", ("photo2.JPG", "photo10.jpg"))
        self.configuration([self.album(title="午後 & 旅途", date="2025-03-15")])
        with self.serving() as base:
            page = self.browser.new_page()
            try:
                page.goto(base)
                cover = page.locator("a").filter(has=page.locator("img"))
                expect(cover).to_have_count(1, timeout=2000)
                cover.click()
                expect(page).to_have_url(base + "/albums/album-a")
                expect(page.get_by_role("heading", level=1, name="午後 & 旅途")).to_be_visible()
                expect(page.get_by_text("2025-03-15", exact=True)).to_be_visible()
                expect(page.get_by_text("2 張相片", exact=False)).to_be_visible()
                page.reload()
                expect(page.get_by_role("heading", level=1, name="午後 & 旅途")).to_be_visible()
                page.get_by_role("link", name="← 所有相簿", exact=True).click()
                expect(page).to_have_url(base + "/")
                page.get_by_role("heading", level=2).get_by_role("link", name="午後 & 旅途", exact=True).click()
                expect(page).to_have_url(base + "/albums/album-a")
            finally:
                page.close()

    def test_混合比例相片依自然順序完整呈現一二三欄且長相簿可捲動(self):
        filenames = self.mixed_album()
        with self.serving() as base:
            for width, columns in ((390, 1), (640, 1), (641, 2), (800, 2), (1000, 2), (1001, 3), (1280, 3)):
                with self.subTest(畫面寬度=width):
                    page = self.browser.new_page(viewport={"width": width, "height": 900})
                    try:
                        page.goto(base + "/albums/album-a")
                        for image in page.locator("img").all():
                            image.scroll_into_view_if_needed()
                            expect(image).to_have_js_property("complete", True)
                            self.assertGreater(image.evaluate("el => el.naturalWidth"), 0)
                        self.assertEqual(page.locator("figcaption").all_text_contents(), list(filenames))
                        boxes = [photo.bounding_box() for photo in page.get_by_role("listitem").all()]
                        self.assertEqual(len(boxes), 12, "不限制為原型的八張相片")
                        self.assertEqual(len({round(box["x"]) for box in boxes}), columns)
                        self.assertLess(max(box["width"] for box in boxes) - min(box["width"] for box in boxes), 1)
                        for image in page.locator("img").all():
                            box = image.bounding_box()
                            natural_ratio = image.evaluate("el => el.naturalWidth / el.naturalHeight")
                            self.assertAlmostEqual(box["width"] / box["height"], natural_ratio, delta=.01)
                            self.assertTrue(image.get_attribute("width"), "載入前須保留寬度比例")
                            self.assertTrue(image.get_attribute("height"), "載入前須保留高度比例")
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                        page.locator("figcaption").last.scroll_into_view_if_needed()
                        expect(page.locator("figcaption").last).to_be_in_viewport()
                        self.assertGreater(page.evaluate("window.scrollY"), 0)
                        if width in (390, 800, 1280):
                            self.save_evidence(page, f"album-{width}")
                    finally:
                        page.close()

    def test_長相簿先載入接近畫面的相片並在捲動後載入後段相片(self):
        self.mixed_album()
        with self.serving() as base:
            page = self.browser.new_page(viewport={"width": 390, "height": 900})
            try:
                page.goto(base + "/albums/album-a", wait_until="networkidle")
                self.assertGreater(page.locator("img").first.evaluate("el => el.naturalWidth"), 0)
                self.assertEqual(page.locator("img").last.evaluate("el => el.naturalWidth"), 0, "剛開啟長相簿不應立即讀取遠處的 JPG")
                page.locator("img").last.scroll_into_view_if_needed()
                expect(page.locator("img").last).to_have_js_property("complete", True)
                self.assertGreater(page.locator("img").last.evaluate("el => el.naturalWidth"), 0)
            finally:
                page.close()

    def test_載入中與失敗保留比例空間且其他相片可瀏覽並可重試(self):
        self.mixed_album()
        with self.serving() as base:
            page = self.browser.new_page(viewport={"width": 390, "height": 900})
            try:
                pending = []
                page.route("**/images/album-a/photo1.jpg", lambda route: pending.append(route), times=1)
                page.goto(base + "/albums/album-a", wait_until="domcontentloaded")
                image = page.locator("img").first
                expect(image).to_have_js_property("complete", False)
                before = page.get_by_role("listitem").first.bounding_box()
                self.assertGreater(before["height"], 200, "載入中保留橫幅比例空間")
                pending[0].abort()
                failure = page.get_by_text("相片載入失敗", exact=True).first
                expect(failure).to_be_visible()
                page.locator("img").last.scroll_into_view_if_needed()
                expect(page.locator("img").last).to_be_in_viewport()
                for other in page.locator("img").all()[1:]:
                    other.scroll_into_view_if_needed()
                    expect(other).to_have_js_property("complete", True)
                    self.assertGreater(other.evaluate("el => el.naturalWidth"), 0)
                failed = page.get_by_role("listitem").first.bounding_box()
                self.assertAlmostEqual(before["height"], failed["height"], delta=1)
                self.save_evidence(page, "album-failed")
                page.get_by_role("button", name="重試", exact=True).click()
                expect(failure).to_be_hidden()
                page.wait_for_function("document.images[0].complete && document.images[0].naturalWidth > 0")
                after = page.get_by_role("listitem").first.bounding_box()
                self.assertAlmostEqual(before["height"], after["height"], delta=1)
            finally:
                page.close()

    def test_未列出相簿可直接開啟且404可返回首頁(self):
        self.photos("未列出")
        self.configuration([self.album("未列出", "unlisted", title="持有網址作品", status="unlisted")])
        with self.serving() as base:
            page = self.browser.new_page(viewport={"width": 390, "height": 900})
            try:
                page.goto(base)
                expect(page.get_by_text("目前尚無公開相簿", exact=True)).to_be_visible()
                expect(page.get_by_role("link", name="持有網址作品")).to_have_count(0)
                page.goto(base + "/albums/unlisted")
                expect(page.get_by_role("heading", level=1, name="持有網址作品")).to_be_visible()
                page.reload()
                expect(page.get_by_role("heading", level=1, name="持有網址作品")).to_be_visible()
                response = page.goto(base + "/albums/missing")
                self.assertEqual(response.status, 404)
                expect(page.get_by_role("heading", name="找不到這個相簿或相片")).to_be_visible()
                self.save_evidence(page, "album-not-found")
                page.get_by_role("link", name="返回相簿列表", exact=True).click()
                expect(page).to_have_url(base + "/")
                expect(page.get_by_text("目前尚無公開相簿", exact=True)).to_be_visible()
            finally:
                page.close()


if __name__ == "__main__":
    unittest.main()
