"""以真實 Chromium 下載事件驗收目前相片的兩種 JPG。"""

import os
from pathlib import Path
import unittest
from urllib.parse import quote
from urllib.request import urlopen

from PIL import Image
from playwright.sync_api import expect

from support import BrowserFixture


class DownloadBrowserTest(BrowserFixture):
    def save_evidence(self, page, name):
        destination = os.environ.get("PHOTO_SHOWCASE_EVIDENCE")
        if destination:
            directory = Path(destination)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(directory / f"{name}.png"))

    def test_換圖後不交付尚未完成的舊下載也不讓舊失敗覆蓋新操作(self):
        output = self.photos("相簿甲", ("photo1.jpg", "photo2.jpg"))
        Image.new("RGB", (800, 1200), "#6f8ca0").save(output / "photo2.jpg")
        self.configuration([self.album()])
        with self.serving() as base:
            for old_result in ("成功", "失敗"):
                with self.subTest(舊結果=old_result):
                    page = self.browser.new_page(viewport={"width": 390, "height": 900})
                    try:
                        pending, downloads = [], []
                        page.route("**/downloads/**", lambda route: pending.append(route))
                        page.on("download", lambda download: downloads.append(download))
                        page.goto(base + "/albums/album-a/photo1.jpg")
                        dialog = page.get_by_role("dialog", name="相片看圖器")
                        button = dialog.get_by_role("button", name="下載原始 JPG", exact=True)
                        with page.expect_request("**/downloads/album-a/original/photo1.jpg"):
                            button.click()
                        if old_result == "失敗":
                            (output / "photo1.jpg").rename(output / "saved.jpg")
                        old_response = pending[0].fetch()
                        self.assertEqual(old_response.status, 404 if old_result == "失敗" else 200)
                        dialog.get_by_role("button", name="下一張", exact=True).click()
                        with page.expect_request("**/downloads/album-a/original/photo2.jpg"):
                            button.click()
                        pending[0].fulfill(response=old_response)
                        page.wait_for_timeout(150)
                        self.assertEqual(downloads, [], "換圖時仍在準備的舊下載不得交付")
                        expect(dialog.get_by_text("下載失敗，請重試", exact=True)).to_be_hidden()
                        with page.expect_download(timeout=3000) as downloading:
                            pending[1].fulfill(response=pending[1].fetch())
                        self.assertEqual(downloading.value.suggested_filename, "photo2.jpg")
                        self.assertEqual(Path(downloading.value.path()).read_bytes(), (output / "photo2.jpg").read_bytes())
                    finally:
                        if (output / "saved.jpg").exists():
                            (output / "saved.jpg").rename(output / "photo1.jpg")
                        page.close()

    def test_兩種下載遇到來源缺漏或網路失敗都顯示錯誤且可以重試(self):
        output = self.photos("相簿甲")
        self.configuration([self.album()])
        with self.serving() as base:
            for variant, label in (("display", "下載瀏覽用 JPG"), ("original", "下載原始 JPG")):
                for failure in ("來源缺漏", "網路失敗"):
                    with self.subTest(類型=variant, 失敗=failure):
                        page = self.browser.new_page(viewport={"width": 390, "height": 900})
                        try:
                            downloads = []
                            page.on("download", lambda download: downloads.append(download))
                            page.goto(base + "/albums/album-a/photo1.jpg")
                            dialog = page.get_by_role("dialog", name="相片看圖器")
                            button = dialog.get_by_role("button", name=label, exact=True)
                            if failure == "來源缺漏":
                                (output / "photo1.jpg").rename(output / "saved.jpg")
                            else:
                                def fail_delivery(route):
                                    self.assertEqual(route.fetch().status, 200)
                                    route.abort("connectionfailed")
                                page.route(f"**/downloads/album-a/{variant}/photo1.jpg", fail_delivery, times=1)
                            button.click()
                            error = dialog.get_by_text("下載失敗，請重試", exact=True)
                            expect(error).to_be_visible(timeout=1000)
                            self.assertEqual(downloads, [])
                            expect(button).to_be_enabled()
                            self.save_evidence(page, f"download-failed-{variant}-{failure}")
                            if failure == "來源缺漏":
                                (output / "saved.jpg").rename(output / "photo1.jpg")
                            with page.expect_download(timeout=3000) as downloading:
                                button.click()
                            self.assertIsNone(downloading.value.failure())
                            expect(error).to_be_hidden()
                        finally:
                            if (output / "saved.jpg").exists():
                                (output / "saved.jpg").rename(output / "photo1.jpg")
                            page.close()

    def test_三種尺寸可下載目前相片的兩種JPG且換圖後更新目標(self):
        filenames = ("photo1.jpg", "旅途 #100%.JPEG")
        output = self.photos("相簿甲", filenames)
        Image.new("RGB", (1600, 1000), "#b58e6f").save(output / filenames[0])
        Image.new("RGB", (800, 1200), "#6f8ca0").save(output / filenames[1])
        self.configuration([self.album(status="unlisted")])
        with self.serving() as base:
            for width in (390, 800, 1280):
                with self.subTest(畫面寬度=width):
                    page = self.browser.new_page(viewport={"width": width, "height": 900})
                    try:
                        page.goto(base + "/albums/album-a/photo1.jpg")
                        dialog = page.get_by_role("dialog", name="相片看圖器")
                        requests = []
                        page.on("request", lambda request: requests.append(request.url) if "/downloads/" in request.url else None)
                        for filename in filenames:
                            expect(dialog.locator(".viewer-filename")).to_have_text(filename)
                            for variant, label, description in (
                                ("display", "下載瀏覽用 JPG", "適合瀏覽與日常傳送"),
                                ("original", "下載原始 JPG", "Lightroom 匯出的完整尺寸 JPG"),
                            ):
                                button = dialog.get_by_role("button", name=label, exact=True)
                                expect(button).to_be_visible(timeout=1000)
                                expect(dialog.get_by_text(description, exact=True)).to_be_visible()
                                self.assertGreaterEqual(button.bounding_box()["height"], 44)
                                with page.expect_download(timeout=3000) as downloading:
                                    button.click()
                                download = downloading.value
                                self.assertIsNone(download.failure())
                                self.assertEqual(requests[-1], base + f"/downloads/album-a/{variant}/" + quote(filename, safe=""))
                                if variant == "original":
                                    self.assertEqual(download.suggested_filename, filename)
                                    self.assertEqual(Path(download.path()).read_bytes(), (output / filename).read_bytes())
                                else:
                                    self.assertEqual(download.suggested_filename, Path(filename).stem + "-display.jpg")
                                    with urlopen(base + "/images/album-a/" + quote(filename, safe="")) as response:
                                        self.assertEqual(Path(download.path()).read_bytes(), response.read())
                            self.assertLessEqual(dialog.evaluate("el => el.scrollWidth"), width)
                            self.save_evidence(page, f"download-{width}-{filenames.index(filename) + 1}")
                            if filename == filenames[0]:
                                dialog.get_by_role("button", name="下一張", exact=True).click()
                    finally:
                        page.close()


if __name__ == "__main__":
    unittest.main()
