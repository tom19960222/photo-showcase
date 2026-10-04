"""以真實 Chromium 驗收深色看圖器及固定網址操作。"""

import os
from pathlib import Path
import unittest
from urllib.parse import quote

from playwright.sync_api import expect

from support import BrowserFixture
import test_album_browser


class ViewerBrowserTest(BrowserFixture):
    mixed_album = test_album_browser.AlbumBrowserTest.mixed_album
    def save_evidence(self, page, name):
        destination = os.environ.get("PHOTO_SHOWCASE_EVIDENCE")
        if destination:
            directory = Path(destination)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(directory / f"{name}.png"))

    def test_大圖載入失敗保留比例可重試換圖返回並遵循減少動態(self):
        self.mixed_album()
        with self.serving() as base:
            page = self.browser.new_page(viewport={"width": 390, "height": 900}, reduced_motion="reduce")
            try:
                pending = []
                image_url = "**/images/album-a/photo1.jpg"
                page.route(image_url, lambda route: pending.append(route))
                page.goto(base + "/albums/album-a/photo1.jpg", wait_until="domcontentloaded")
                dialog = page.get_by_role("dialog", name="相片看圖器")
                frame = dialog.locator(".viewer-stage .photo-frame")
                expect(frame).to_have_attribute("aria-busy", "true")
                before = frame.bounding_box()
                self.assertAlmostEqual(before["width"] / before["height"], 1.5, delta=.01)
                self.assertGreater(before["height"], 200)
                for route in pending:
                    route.abort()
                failure = dialog.get_by_text("相片載入失敗", exact=True)
                expect(failure).to_be_visible()
                page.unroute(image_url)
                self.assertEqual(frame.bounding_box(), before)
                self.save_evidence(page, "viewer-failed")
                dialog.get_by_role("button", name="重試", exact=True).click()
                expect(failure).to_be_hidden()
                expect(frame).to_have_attribute("aria-busy", "false")
                self.assertGreater(frame.locator("img").evaluate("el => el.naturalWidth"), 0)
                self.assertEqual(frame.bounding_box(), before)
                self.assertTrue(dialog.evaluate("el => [...el.querySelectorAll('*')].every(node => {const s = getComputedStyle(node); return s.animationDuration === '0s' && s.transitionDuration === '0s' && s.scrollBehavior !== 'smooth';})"))
                page.route("**/images/album-a/photo2.JPG", lambda route: route.abort())
                page.keyboard.press("ArrowRight")
                expect(failure).to_be_visible()
                dialog.get_by_role("button", name="重試", exact=True).focus()
                page.keyboard.press("ArrowRight")
                expect(dialog.get_by_text("3 / 12", exact=True)).to_be_visible()
                expect(failure).to_be_hidden()
                self.assertTrue(dialog.evaluate("el => el.contains(document.activeElement)"))
                page.keyboard.press("ArrowLeft")
                expect(failure).to_be_visible()
                page.keyboard.press("Escape")
                expect(dialog).to_be_hidden()
                expect(page).to_have_url(base + "/albums/album-a")
            finally:
                page.close()

    def test_重整及上一頁下一頁維持目前相片且直接中文網址可返回相簿(self):
        filenames = self.mixed_album()
        with self.serving() as base:
            page = self.browser.new_page(viewport={"width": 390, "height": 900})
            try:
                album = base + "/albums/album-a"
                page.goto(album)
                opener = page.get_by_role("link", name="開啟相片：photo2.JPG", exact=True)
                opener.click()
                page.keyboard.press("ArrowRight")
                expect(page).to_have_url(album + "/photo3.jpg")
                page.reload()
                dialog = page.get_by_role("dialog", name="相片看圖器")
                expect(dialog.get_by_text("photo3.jpg", exact=True)).to_be_visible()
                page.go_back()
                expect(page).to_have_url(album)
                expect(dialog).to_be_hidden()
                expect(opener).to_be_focused()
                page.go_forward()
                expect(page).to_have_url(album + "/photo3.jpg")
                expect(dialog.get_by_text("photo3.jpg", exact=True)).to_be_visible()
                page.keyboard.press("Escape")
                expect(page).to_have_url(album)
                expect(opener).to_be_focused()
                page.goto(album + "/" + quote(filenames[-1], safe=""))
                expect(dialog.get_by_text(filenames[-1], exact=True)).to_be_visible()
                page.reload()
                expect(dialog.get_by_text(filenames[-1], exact=True)).to_be_visible()
                expect(dialog.get_by_text("12 / 12", exact=True)).to_be_visible()
                page.keyboard.press("Escape")
                expect(page).to_have_url(album)
                expect(page.get_by_role("link", name=f"開啟相片：{filenames[-1]}", exact=True)).to_be_focused()
            finally:
                page.close()

    def test_底片能定位全部相片且支援按鈕鍵盤滑動與首尾邊界(self):
        filenames = self.mixed_album()
        with self.serving() as base:
            for width in (390, 800, 1280):
                with self.subTest(畫面寬度=width):
                    page = self.browser.new_page(viewport={"width": width, "height": 900}, has_touch=True)
                    try:
                        page.goto(base + "/albums/album-a/photo1.jpg")
                        dialog = page.get_by_role("dialog", name="相片看圖器")
                        previous = dialog.get_by_role("button", name="上一張", exact=True)
                        next_photo = dialog.get_by_role("button", name="下一張", exact=True)
                        expect(previous).to_be_disabled(timeout=2000)
                        page.keyboard.press("ArrowLeft")
                        expect(dialog.get_by_text("1 / 12", exact=True)).to_be_visible()
                        next_photo.click()
                        expect(dialog.get_by_text("2 / 12", exact=True)).to_be_visible()
                        page.keyboard.press("ArrowRight")
                        expect(dialog.get_by_text("3 / 12", exact=True)).to_be_visible()
                        page.keyboard.press("ArrowLeft")
                        expect(dialog.get_by_text("2 / 12", exact=True)).to_be_visible()
                        previous.click()
                        strip = dialog.get_by_role("navigation", name="底片導覽")
                        expect(strip.get_by_role("button")).to_have_count(12)
                        thumbnails = strip.locator("img")
                        boxes = [image.bounding_box() for image in thumbnails.all()]
                        self.assertLess(max(box["height"] for box in boxes) - min(box["height"] for box in boxes), 1)
                        self.assertAlmostEqual(boxes[0]["width"] / boxes[0]["height"], 1.5, delta=.01)
                        self.assertAlmostEqual(boxes[1]["width"] / boxes[1]["height"], 2 / 3, delta=.01)
                        for index in (6, 11, 0):
                            target = strip.get_by_role("button", name=f"第 {index + 1} 張：{filenames[index]}", exact=True)
                            target.click()
                            expect(target).to_have_attribute("aria-current", "true")
                            expect(target).to_be_in_viewport()
                            expect(page).to_have_url(base + "/albums/album-a/" + quote(filenames[index], safe=""))
                            expect(dialog.get_by_text(f"{index + 1} / 12", exact=True)).to_be_visible()
                            if index == 11:
                                expect(next_photo).to_be_disabled()
                                page.keyboard.press("ArrowRight")
                                expect(dialog.get_by_text("12 / 12", exact=True)).to_be_visible()
                        session = page.context.new_cdp_session(page)
                        for start, end, count in (((280, 400), (90, 410), 2), ((90, 400), (280, 410), 1), ((200, 400), (210, 600), 1)):
                            session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": start[0], "y": start[1]}]})
                            session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": end[0], "y": end[1]}]})
                            session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                            expect(dialog.get_by_text(f"{count} / 12", exact=True)).to_be_visible()
                        session.detach()
                        self.save_evidence(page, f"viewer-strip-{width}")
                    finally:
                        page.close()

    def test_三種螢幕開啟深色看圖器保留橫直幅完整比例並關閉回原入口(self):
        self.mixed_album()
        with self.serving() as base:
            for width in (390, 800, 1280):
                with self.subTest(畫面寬度=width):
                    page = self.browser.new_page(viewport={"width": width, "height": 900})
                    try:
                        page.goto(base + "/albums/album-a")
                        for filename, ratio in (("photo1.jpg", 1.5), ("photo2.JPG", 2 / 3)):
                            opener = page.get_by_role("link", name=f"開啟相片：{filename}", exact=True)
                            opener.click()
                            dialog = page.get_by_role("dialog", name="相片看圖器")
                            expect(dialog).to_be_visible(timeout=2000)
                            self.assertEqual(dialog.bounding_box(), {"x": 0, "y": 0, "width": width, "height": 900})
                            expect(page).to_have_url(base + "/albums/album-a/" + filename)
                            expect(dialog.get_by_text(filename, exact=True)).to_be_visible()
                            self.assertEqual(dialog.evaluate("el => getComputedStyle(el).backgroundColor"), "rgb(18, 19, 19)")
                            image = dialog.locator(".viewer-stage img")
                            expect(image).to_have_js_property("complete", True)
                            self.assertGreater(image.evaluate("el => el.naturalWidth"), 0)
                            box = image.bounding_box()
                            self.assertAlmostEqual(box["width"] / box["height"], ratio, delta=.01)
                            self.assertGreater(box["height"], 190)
                            self.assertGreaterEqual(box["x"], 0)
                            self.assertLessEqual(box["x"] + box["width"], width)
                            self.assertLessEqual(box["y"] + box["height"], 900)
                            self.assertEqual(image.evaluate("el => getComputedStyle(el).objectFit"), "contain")
                            self.assertTrue(dialog.evaluate("el => el.contains(document.activeElement)"))
                            page.keyboard.press("Tab")
                            self.assertTrue(dialog.evaluate("el => el.contains(document.activeElement)"))
                            self.assertNotEqual(page.locator(":focus").evaluate("el => getComputedStyle(el).outlineStyle"), "none")
                            page.keyboard.press("Shift+Tab")
                            page.keyboard.press("Shift+Tab")
                            self.assertTrue(dialog.evaluate("el => el.contains(document.activeElement)"))
                            self.save_evidence(page, f"viewer-{width}-{filename}")
                            if filename == "photo1.jpg":
                                page.keyboard.press("Escape")
                            else:
                                dialog.get_by_role("button", name="關閉", exact=True).click()
                            expect(dialog).to_be_hidden()
                            expect(page).to_have_url(base + "/albums/album-a")
                            expect(opener).to_be_focused()
                    finally:
                        page.close()


if __name__ == "__main__":
    unittest.main()
