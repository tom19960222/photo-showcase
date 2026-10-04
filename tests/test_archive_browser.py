"""在真實 HTTP 與瀏覽器網路邊界驗收 ZIP 操作。"""

import os
from pathlib import Path
import unittest
from zipfile import ZipFile

from playwright.sync_api import expect

from support import BrowserFixture


class ArchiveBrowserTest(BrowserFixture):
    def save_evidence(self, page, name):
        destination = os.environ.get("PHOTO_SHOWCASE_EVIDENCE")
        if destination:
            directory = Path(destination)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(directory / f"{name}.png"))

    def test_三種尺寸可準備並取得真實ZIP且狀態清楚不溢出(self):
        filenames = ("photo2.JPG", "photo10.jpeg", "旅途 #100%.JPEG")
        output = self.photos("相簿甲", filenames)
        self.configuration([self.album(title="午後散步", cover="photo10.jpeg")])
        with self.serving() as base:
            for width in (390, 800, 1280):
                with self.subTest(畫面寬度=width):
                    page = self.browser.new_page(viewport={"width": width, "height": 900})
                    try:
                        pending = []
                        page.route("**/archives/album-a.zip", lambda route: pending.append(route))
                        page.goto(base + "/albums/album-a")
                        start = page.get_by_role("button", name="下載整本相簿 ZIP", exact=True)
                        expect(start).to_be_visible(timeout=2000)
                        start.click()
                        expect(page.get_by_text("正在準備相簿 ZIP…", exact=True)).to_be_visible()
                        expect(page.get_by_role("button", name="取消準備", exact=True)).to_be_visible()
                        progress = page.get_by_role("progressbar")
                        expect(progress).to_be_visible()
                        self.assertIsNone(progress.get_attribute("value"), "未知進度不顯示百分比")
                        self.save_evidence(page, f"archive-preparing-{width}")
                        response = pending[0].fetch()
                        self.assertEqual(response.status, 200)
                        pending[0].fulfill(response=response)
                        expect(page.get_by_text("ZIP 已準備完成", exact=True)).to_be_visible()
                        expect(progress).to_be_hidden()
                        link = page.get_by_role("link", name="取得相簿 ZIP", exact=True)
                        expect(link).to_be_visible()
                        with page.expect_download() as downloading:
                            link.click()
                        download = downloading.value
                        self.assertEqual(download.suggested_filename, "album-a.zip")
                        self.assertIsNone(download.failure())
                        with ZipFile(download.path()) as archive:
                            self.assertEqual(archive.namelist(), list(filenames))
                            for name in filenames:
                                self.assertEqual(archive.read(name), (output / name).read_bytes())
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                        expect(start).to_be_enabled()
                        self.save_evidence(page, f"archive-ready-{width}")
                    finally:
                        page.close()

    def test_取消後可重試且舊的真實結果不覆蓋新操作(self):
        self.photos("相簿甲")
        self.configuration([self.album()])
        with self.serving() as base:
            page = self.browser.new_page(viewport={"width": 390, "height": 900})
            try:
                pending, downloads = [], []
                page.route("**/archives/album-a.zip", lambda route: pending.append(route))
                page.on("download", lambda download: downloads.append(download))
                page.goto(base + "/albums/album-a")
                start = page.get_by_role("button", name="下載整本相簿 ZIP", exact=True)
                start.click()
                expect(page.get_by_text("正在準備相簿 ZIP…", exact=True)).to_be_visible()
                old_response = pending[0].fetch()
                page.get_by_role("button", name="取消準備", exact=True).click()
                expect(page.get_by_text("已取消準備", exact=True)).to_be_visible(timeout=2000)
                expect(page.get_by_role("link", name="取得相簿 ZIP", exact=True)).to_be_hidden()
                expect(page.get_by_role("progressbar")).to_be_hidden()
                expect(start).to_be_enabled()
                self.save_evidence(page, "archive-cancelled")
                start.click()
                expect(page.get_by_text("正在準備相簿 ZIP…", exact=True)).to_be_visible()
                pending[0].fulfill(response=old_response)
                page.wait_for_timeout(150)
                expect(page.get_by_text("正在準備相簿 ZIP…", exact=True)).to_be_visible()
                expect(page.get_by_role("link", name="取得相簿 ZIP", exact=True)).to_be_hidden()
                self.assertEqual(downloads, [], "已取消的操作不得觸發下載")
                pending[1].fulfill(response=pending[1].fetch())
                expect(page.get_by_text("ZIP 已準備完成", exact=True)).to_be_visible()
                with page.expect_download() as downloading:
                    page.get_by_role("link", name="取得相簿 ZIP", exact=True).click()
                with ZipFile(downloading.value.path()) as archive:
                    self.assertEqual(archive.namelist(), ["photo1.jpg"])
            finally:
                page.close()

    def test_來源缺漏及網路交付失敗都有錯誤狀態並可重試(self):
        output = self.photos("相簿甲", ("photo1.jpg", "photo2.jpg"))
        self.configuration([self.album(status="unlisted")])
        with self.serving() as base:
            for failure in ("來源缺漏", "網路交付失敗"):
                with self.subTest(失敗原因=failure):
                    page = self.browser.new_page(viewport={"width": 390, "height": 900})
                    try:
                        page.goto(base + "/albums/album-a")
                        start = page.get_by_role("button", name="下載整本相簿 ZIP", exact=True)
                        if failure == "來源缺漏":
                            (output / "photo2.jpg").rename(output / "saved.jpg")
                        else:
                            def fail_delivery(route):
                                self.assertEqual(route.fetch().status, 200, "先由真實應用程式備妥 ZIP 再中斷交付")
                                route.abort("connectionfailed")
                            page.route("**/archives/album-a.zip", fail_delivery, times=1)
                        start.click()
                        expect(page.get_by_text("相簿 ZIP 準備失敗，請重試", exact=True)).to_be_visible(timeout=2000)
                        expect(start).to_be_enabled()
                        expect(page.get_by_role("progressbar")).to_be_hidden()
                        expect(page.get_by_role("link", name="取得相簿 ZIP", exact=True)).to_be_hidden()
                        self.save_evidence(page, f"archive-failed-{failure}")
                        if failure == "來源缺漏":
                            (output / "saved.jpg").rename(output / "photo2.jpg")
                        start.click()
                        expect(page.get_by_text("ZIP 已準備完成", exact=True)).to_be_visible()
                        with page.expect_download() as downloading:
                            page.get_by_role("link", name="取得相簿 ZIP", exact=True).click()
                        with ZipFile(downloading.value.path()) as archive:
                            self.assertEqual(archive.namelist(), ["photo1.jpg", "photo2.jpg"])
                    finally:
                        if (output / "saved.jpg").exists():
                            (output / "saved.jpg").rename(output / "photo2.jpg")
                        page.close()


if __name__ == "__main__":
    unittest.main()
