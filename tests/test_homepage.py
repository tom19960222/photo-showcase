"""以合成相片與真實瀏覽器驗收 C 首頁。"""

import os
from pathlib import Path
import unittest

from PIL import Image, ImageDraw
from playwright.sync_api import expect

from support import BrowserFixture


class HomepageTest(BrowserFixture):
    def public_albums(self):
        titles = ("午後散步", "海岸微風", "山間日常", "巷口時光", "冬日晨光", "夏末河畔", "旅途片刻", "春日印象")
        colors = ("#ab6d46", "#507e89", "#818965", "#c29364", "#76868e", "#88896d", "#ba8c75", "#779c90")
        albums = []
        for number, (title, color) in enumerate(zip(titles, colors)):
            output = self.photos(title, ("photo2.JPG", "photo10.jpg"))
            size = (800, 1200) if number % 2 else (1200, 800)
            photo = Image.new("RGB", size, color)
            draw = ImageDraw.Draw(photo)
            draw.rectangle((0, 0, size[0] - 1, size[1] - 1), outline="#f6dfab", width=24)
            draw.line((0, size[1] // 2, size[0], size[1] // 2), fill="#e4c4a2", width=8)
            draw.ellipse((size[0] // 3, size[1] // 3, size[0] * 2 // 3, size[1] * 2 // 3), fill="#dac9a9")
            photo.save(output / "photo2.JPG")
            albums.append(self.album(title, f"album-{number}", title=title, date=f"2025-{12 - number:02}-01"))
        self.configuration(albums)

    def save_evidence(self, page, name):
        destination = os.environ.get("PHOTO_SHOWCASE_EVIDENCE")
        if destination:
            directory = Path(destination)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(directory / f"{name}.png"), full_page=True)

    def test_手機平板桌面呈現一二三欄且最新相簿跨列(self):
        self.public_albums()
        with self.serving() as base:
            for width, columns in ((390, 1), (800, 2), (1280, 3)):
                with self.subTest(畫面寬度=width):
                    page = self.browser.new_page(viewport={"width": width, "height": 900})
                    try:
                        page.goto(base)
                        page.wait_for_function("Array.from(document.images).every(image => image.complete && image.naturalWidth > 0)")
                        cards = page.get_by_role("listitem")
                        self.assertEqual(cards.count(), 8, "不限制為原型的七本相簿")
                        self.assertEqual(page.get_by_role("heading", level=2).first.inner_text(), "午後散步")
                        self.assertTrue(all("2 張相片" in card.inner_text() for card in cards.all()))
                        boxes = [card.bounding_box() for card in cards.all()]
                        first_row = [box for box in boxes[1:] if abs(box["y"] - boxes[1]["y"]) < 2]
                        self.assertEqual(len(first_row), columns)
                        self.assertLess(boxes[0]["y"], boxes[1]["y"])
                        if columns > 1:
                            self.assertGreater(boxes[0]["width"], boxes[1]["width"] * 1.8)
                        images = page.locator("img")
                        for image in images.all():
                            self.assertEqual(image.evaluate("el => getComputedStyle(el).objectFit"), "contain", "完整保留照片構圖")
                            self.assertTrue(image.get_attribute("width"), "載入前須預留比例")
                            self.assertTrue(image.get_attribute("height"), "載入前須預留比例")
                        lower = [image.bounding_box() for image in images.all()[1:]]
                        self.assertLess(max(box["height"] for box in lower) - min(box["height"] for box in lower), 2, "混合直橫幅使用一致橫幅容器")
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                        self.save_evidence(page, f"homepage-{width}")
                    finally:
                        page.close()

    def test_封面載入失敗保留空間且重試能恢復(self):
        self.public_albums()
        with self.serving() as base:
            page = self.browser.new_page(viewport={"width": 390, "height": 900}, reduced_motion="reduce")
            try:
                page.route("**/images/album-0/*", lambda route: route.abort(), times=1)
                page.goto(base)
                failure = page.get_by_role("listitem").first.get_by_text("相片載入失敗", exact=True)
                expect(failure).to_be_visible(timeout=3000)
                retry = page.get_by_role("button", name="重試", exact=True)
                expect(retry).to_be_visible()
                before = page.get_by_role("listitem").first.bounding_box()
                self.save_evidence(page, "homepage-failed")
                retry.click()
                expect(failure).to_be_hidden()
                page.wait_for_function("document.images[0].complete && document.images[0].naturalWidth > 0")
                after = page.get_by_role("listitem").first.bounding_box()
                self.assertAlmostEqual(before["height"], after["height"], delta=1)
                self.assertTrue(page.evaluate("Array.from(document.querySelectorAll('*')).every(el => {const s = getComputedStyle(el); return s.animationDuration === '0s' && s.transitionDuration === '0s';})"))
                self.assertEqual(page.get_by_role("button").count(), 0, "成功載入後不呈現版型模擬控制")
            finally:
                page.close()

    def test_沒有公開相簿時瀏覽器顯示空列表訊息(self):
        self.configuration([])
        with self.serving() as base:
            page = self.browser.new_page(viewport={"width": 800, "height": 900})
            try:
                page.goto(base)
                expect(page.get_by_text("目前尚無公開相簿", exact=True)).to_be_visible()
                self.assertEqual(page.locator("img").count(), 0)
                self.save_evidence(page, "homepage-empty")
            finally:
                page.close()


if __name__ == "__main__":
    unittest.main()
