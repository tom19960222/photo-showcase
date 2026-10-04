"""以真實程序、暫存相簿與 HTTP 驗證發佈行為。"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from urllib.parse import quote


APPLICATION = Path(__file__).resolve().parents[1] / "app.py"
SAMPLE_JPG = Path(__file__).resolve().parent / "fixtures" / "sample.jpg"


class ApplicationTest(unittest.TestCase):
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

    def test_首頁只列公開相簿並依指定日期由新到舊排列(self):
        self.photos("舊相簿", ("photo1.jpg", "photo2.JPEG", "source.raw", "notes.txt"))
        self.photos("新相簿", ("photo.jpg",))
        self.photos("未列出", ("secret.jpg",))
        self.photos("未設定", ("hidden.jpg",))
        self.configuration([
            self.album("舊相簿", "older", title="早期作品", date="2020-02-29"),
            self.album("新相簿", "newer", title="近期作品", date="2030-10-03"),
            self.album("未列出", "unlisted", title="持有網址作品", status="unlisted"),
            self.album("已停用", "disabled", title="停用作品", status="disabled"),
        ])
        with self.serving() as base:
            page = self.page(base)
        self.assertLess(page.index("近期作品"), page.index("早期作品"))
        self.assertIn("2030-10-03", page)
        self.assertIn("2020-02-29", page)
        self.assertIn("2 張相片", page)
        self.assertIn("1 張相片", page)
        for hidden in ("持有網址作品", "停用作品", "未設定"):
            self.assertNotIn(hidden, page)

    def test_設定只接受第一版與明確的頂層結構(self):
        invalid = [
            {"version": 2, "albums": []},
            {"version": True, "albums": []},
            {"version": 1.0, "albums": []},
            {"version": "1", "albums": []},
            {"version": 1, "albums": [], "scan": True},
            {"version": 1},
            {"albums": []},
            {"version": 1, "albums": {}},
            {"version": 1, "albums": None},
            [],
            None,
        ]
        for configuration in invalid:
            with self.subTest(設定=configuration):
                self.config.write_text(json.dumps(configuration), encoding="utf-8")
                self.assert_startup_fails()

    def test_每本相簿包括停用相簿都須符合欄位合約(self):
        entry = self.album(status="disabled")
        invalid = []
        for field in ("folder", "slug", "title", "date", "status"):
            invalid.append({key: value for key, value in entry.items() if key != field})
            invalid.append({**entry, field: 7})
            invalid.append({**entry, field: None})
        for field, values in {
            "folder": ["", ".", "..", "/tmp", "相簿/子層", "相簿\\子層"],
            "slug": ["", "Album", "has space", "中文"],
            "title": ["", " \n\t "],
            "date": ["2025-2-03", "2023-02-29", "0000-01-01", "2025-01-01T00:00:00"],
            "status": ["PUBLIC", "private", ""],
            "cover": ["", "../photo.jpg", "sub/photo.jpg", "sub\\photo.jpg", "photo.raw", None, 1],
        }.items():
            invalid.extend({**entry, field: value} for value in values)
        invalid.extend([{**entry, "visibility": "public"}, None, [], "相簿"])
        for invalid_entry in invalid:
            with self.subTest(相簿=invalid_entry):
                self.configuration([invalid_entry])
                self.assert_startup_fails()

    def test_相簿識別在當下設定內不得重複(self):
        for second in (
            self.album("相簿甲", "another", status="disabled"),
            self.album("相簿乙", "album-a", status="disabled"),
        ):
            with self.subTest(相簿=second):
                self.configuration([self.album(status="disabled"), second])
                self.assert_startup_fails()

    def test_任一本已發佈相簿缺漏來源或封面時整個程序拒絕啟動(self):
        self.photos("合格相簿")
        for status in ("public", "unlisted"):
            for kind in ("不存在", "沒有輸出", "沒有JPG", "缺少封面", "封面大小寫錯誤", "封面是資料夾"):
                folder = f"{status}-{kind}"
                entry = self.album(folder, f"{status}-{len(folder)}", status=status)
                if kind == "沒有輸出":
                    (self.root / folder).mkdir()
                elif kind == "沒有JPG":
                    output = self.photos(folder, ("source.raw", "notes.txt"))
                    nested = output / "子資料夾"
                    nested.mkdir()
                    (nested / "nested.jpg").write_bytes(SAMPLE_JPG.read_bytes())
                elif kind == "缺少封面":
                    self.photos(folder)
                    entry["cover"] = "missing.jpg"
                elif kind == "封面大小寫錯誤":
                    self.photos(folder, ("PHOTO.jpg",))
                    entry["cover"] = "photo.jpg"
                elif kind == "封面是資料夾":
                    output = self.photos(folder)
                    (output / "cover.jpg").mkdir()
                    entry["cover"] = "cover.jpg"
                with self.subTest(狀態=status, 缺漏=kind):
                    self.configuration([self.album("合格相簿", "valid"), entry])
                    self.assert_startup_fails()

    def test_已發佈來源不得透過檔案或資料夾連結讀取允許範圍外內容(self):
        external = self.directory / "來源外"
        (external / "output").mkdir(parents=True)
        (external / "output" / "photo.jpg").write_bytes(SAMPLE_JPG.read_bytes())
        sibling = self.photos("另一個相簿")
        for status in ("public", "unlisted"):
            for kind in ("相簿連結", "輸出連結", "外部相片連結", "其他相簿相片連結"):
                folder = f"{status}-{kind}"
                if kind == "相簿連結":
                    (self.root / folder).symlink_to(external, target_is_directory=True)
                elif kind == "輸出連結":
                    (self.root / folder).mkdir()
                    (self.root / folder / "output").symlink_to(external / "output", target_is_directory=True)
                else:
                    output = self.photos(folder)
                    target = external / "output" / "photo.jpg" if kind == "外部相片連結" else sibling / "photo1.jpg"
                    (output / "escape.jpg").symlink_to(target)
                with self.subTest(狀態=status, 連結=kind):
                    self.configuration([self.album(folder, f"{status}-{len(folder)}", status=status)])
                    self.assert_startup_fails()

    def test_設定不存在無法解析或不是UTF8時回報錯誤並退出(self):
        self.assert_startup_fails()
        for content in (b'{"version": 1, "albums": [}', b"\xff\xfe", b"null"):
            with self.subTest(內容=content):
                self.config.write_bytes(content)
                self.assert_startup_fails()

    def test_停用相簿不要求來源與封面存在且空首頁有清楚說明(self):
        self.root.rmdir()
        for albums in ([], [self.album(status="disabled", cover="missing.JPG")]):
            with self.subTest(相簿=albums):
                self.configuration(albums)
                with self.serving() as base:
                    self.assertIn("目前尚無公開相簿", self.page(base))

    def test_只有未列出相簿時首頁仍顯示空列表(self):
        self.photos("相簿甲")
        self.configuration([self.album(status="unlisted")])
        with self.serving() as base:
            page = self.page(base)
        self.assertIn("目前尚無公開相簿", page)
        self.assertNotIn("相簿甲", page)

    def test_相片計數包括封面忽略子資料夾且來源保持唯讀(self):
        output = self.photos("相簿甲", ("photo10.jpg", "photo2.JPEG", "notes.txt", "source.raw"))
        (output / "directory.JPG").mkdir()
        (output / "directory.JPG" / "nested.jpg").write_bytes(SAMPLE_JPG.read_bytes())
        (output / "source.raw").write_bytes("原始檔保留".encode("utf-8"))
        paths = list(self.root.rglob("*"))
        for path in paths:
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        before = {path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns) for path in paths if path.is_file()}
        self.configuration([self.album(cover="photo2.JPEG")])
        try:
            with self.serving() as base:
                self.assertIn("2 張相片", self.page(base))
            after = {path.relative_to(self.root): (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.rglob("*") if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(set(paths), set(self.root.rglob("*")))
        finally:
            for path in paths:
                os.chmod(path, 0o755 if path.is_dir() else 0o644)

    def test_不同相簿可同名標題空白會去除且內容安全呈現(self):
        self.photos("甲")
        self.photos("乙")
        self.configuration([
            self.album("甲", "a", title="  <作品>  ", date="9999-12-31"),
            self.album("乙", "b", title="<作品>", date="2000-02-29"),
        ])
        with self.serving() as base:
            page = self.page(base)
        self.assertEqual(page.count("&lt;作品&gt;"), 2)
        self.assertNotIn("  &lt;作品&gt;  ", page)
        self.assertNotIn("<作品>", page)

    def test_任何未提供的入口皆找不到內容且不透露來源(self):
        self.photos("相簿甲")
        self.photos("未設定")
        self.configuration([self.album(), self.album("停用", "disabled", status="disabled")])
        paths = [
            "/config/albums.json", "/app.py", "/albums/disabled", "/albums/unknown",
            "/albums/disabled/photo.jpg", "/albums/unknown/photo.jpg",
            "/../config/albums.json", "/%2e%2e/config/albums.json",
            "/Albums/" + quote("相簿甲/output/photo1.jpg"),
            "/Albums/" + quote("未設定/output/photo1.jpg"),
        ]
        with self.serving() as base:
            for path in paths:
                with self.subTest(入口=path):
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(base + path, timeout=2)
                    with caught.exception as response:
                        self.assertEqual(response.code, 404)
                        page = response.read().decode("utf-8")
                    self.assertIn("找不到這個相簿或相片", page)
                    self.assertIn("返回相簿列表", page)
                    self.assertNotIn(str(self.root), page)
                    self.assertNotIn("相簿甲", page)
                    self.assertNotIn("未設定", page)


if __name__ == "__main__":
    unittest.main()
