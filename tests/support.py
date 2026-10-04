"""以真實程序、暫存相簿與 HTTP 驗證發佈行為。"""

from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen


APPLICATION = Path(__file__).resolve().parents[1] / "app.py"
SAMPLE_JPG = Path(__file__).resolve().parent / "fixtures" / "sample.jpg"


class ApplicationFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "Albums"
        self.root.mkdir()
        (self.directory / "config").mkdir()
        self.config = self.directory / "config" / "albums.json"

    def album(self, folder="相簿甲", slug="album-a", **changes):
        entry = dict(folder=folder, slug=slug, title="相簿甲", date="2025-01-01", status="public")
        entry.update(changes)
        return entry

    def photos(self, folder, filenames=("photo1.jpg",)):
        output = self.root / folder / "output"
        output.mkdir(parents=True)
        for name in filenames:
            (output / name).write_bytes(SAMPLE_JPG.read_bytes())
        return output

    def configuration(self, albums):
        self.config.write_text(json.dumps({"version": 1, "albums": albums}, ensure_ascii=False), encoding="utf-8")

    def command(self, port):
        return [sys.executable, str(APPLICATION), "--albums-root", str(self.root), "--port", str(port)]

    @contextmanager
    def serving(self):
        with socket.socket() as socket_for_port:
            socket_for_port.bind(("127.0.0.1", 0))
            port = socket_for_port.getsockname()[1]
        process = subprocess.Popen(self.command(port), cwd=self.directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    out, err = process.communicate()
                    self.fail(f"應用程式未能啟動：{out}{err}")
                try:
                    with urlopen(base, timeout=0.2):
                        break
                except (URLError, TimeoutError):
                    time.sleep(0.02)
            else:
                self.fail("應用程式未在期限內提供 HTTP")
            yield base
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)

    def page(self, base, path="/"):
        with urlopen(base + path, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers["Content-Type"])
            return response.read().decode("utf-8")

    def assert_startup_fails(self):
        process = subprocess.Popen(self.command(0), cwd=self.directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            try:
                output, error = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                self.fail("不合格的設定仍讓應用程式啟動")
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("無法啟動相簿", error)
            self.assertNotIn("相簿已啟動", output)
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)


class BrowserFixture(ApplicationFixture):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        cls.browser = playwright.chromium.launch()
        cls.addClassCleanup(cls.browser.close)
