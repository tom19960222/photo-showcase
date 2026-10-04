"""以明確白名單發佈 Lightroom 相簿。"""

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date
from html import escape
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from shutil import copyfileobj
import sys
import stat
from tempfile import TemporaryFile
from urllib.parse import quote, unquote, urlsplit
from zipfile import ZipFile

from PIL import Image, ImageOps


@dataclass(frozen=True)
class Album:
    folder: str
    slug: str
    title: str
    date: date
    status: str
    source: Path
    photos: tuple[str, ...]
    cover: str


def single_filename(value: str) -> bool:
    return isinstance(value, str) and value not in {"", ".", ".."} and not any(character in value for character in ("/", "\\", "\0"))


def natural_key(filename: str):
    return tuple(int(part) if number % 2 else part.lower() for number, part in enumerate(re.split(r"([0-9]+)", filename))), filename


def load_catalog(root: Path) -> dict[str, Album]:
    configuration = json.loads(Path("config/albums.json").read_text(encoding="utf-8"))
    if (not isinstance(configuration, dict) or set(configuration) != {"version", "albums"}
            or type(configuration["version"]) is not int or configuration["version"] != 1
            or not isinstance(configuration["albums"], list)):
        raise ValueError("設定只接受 version 為 1 與 albums 陣列。")
    catalog = {}
    required = {"folder", "slug", "title", "date", "status"}
    folders, slugs = set(), set()
    for number, entry in enumerate(configuration["albums"], 1):
        if (not isinstance(entry, dict) or not required <= set(entry) or set(entry) - required - {"cover"}
                or any(not isinstance(value, str) for value in entry.values())):
            raise ValueError(f"第 {number} 本相簿的欄位或型別不合法。")
        if not single_filename(entry["folder"]):
            raise ValueError(f"第 {number} 本相簿的 folder 必須是一層資料夾名稱。")
        if not re.fullmatch(r"[a-z0-9-]+", entry["slug"]):
            raise ValueError(f"第 {number} 本相簿的 slug 不合法。")
        if not entry["title"].strip():
            raise ValueError(f"第 {number} 本相簿的 title 不得空白。")
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", entry["date"]):
            raise ValueError(f"第 {number} 本相簿的 date 必須使用 YYYY-MM-DD。")
        album_date = date.fromisoformat(entry["date"])
        if entry["status"] not in {"disabled", "public", "unlisted"}:
            raise ValueError(f"第 {number} 本相簿的 status 不合法。")
        if "cover" in entry and (not single_filename(entry["cover"]) or Path(entry["cover"]).suffix.lower() not in {".jpg", ".jpeg"}):
            raise ValueError(f"第 {number} 本相簿的 cover 必須是 JPG 檔名。")
        if entry["folder"] in folders or entry["slug"] in slugs:
            raise ValueError(f"第 {number} 本相簿的 folder 或 slug 重複。")
        folders.add(entry["folder"])
        slugs.add(entry["slug"])
        if entry["status"] == "disabled":
            continue
        source = root.resolve(strict=True) / entry["folder"] / "output"
        if source.parent.is_symlink() or source.is_symlink() or not source.is_dir():
            raise ValueError(f"第 {number} 本已發佈相簿的來源缺漏或包含資料夾連結。")
        filenames = []
        for path in source.iterdir():
            if path.suffix.lower() not in {".jpg", ".jpeg"}:
                continue
            if path.is_symlink() and (not path.is_file() or path.resolve(strict=True).parent != source):
                raise ValueError(f"第 {number} 本已發佈相簿的 JPG 連結超出來源範圍。")
            if path.is_file():
                filenames.append(path.name)
        photos = tuple(sorted(filenames, key=natural_key))
        if not photos:
            raise ValueError(f"第 {number} 本已發佈相簿沒有第一層 JPG。")
        cover = entry.get("cover", photos[0])
        if cover not in photos:
            raise ValueError(f"第 {number} 本已發佈相簿的封面不存在或不是合格 JPG。")
        catalog[entry["slug"]] = Album(
            folder=entry["folder"], slug=entry["slug"], title=entry["title"].strip(),
            date=album_date, status=entry["status"],
            source=source, photos=photos, cover=cover,
        )
    return catalog


def image_url(album: Album, filename: str) -> str:
    return f"/images/{album.slug}/{quote(filename, safe='')}"


@contextmanager
def open_photo(album: Album, filename: str):
    """每次交付都檢查白名單，以目錄描述符防止來源被連結替換。"""
    if not single_filename(filename) or filename not in album.photos:
        raise FileNotFoundError
    try:
        source = (album.source / filename).resolve(strict=True)
    except RuntimeError:
        raise FileNotFoundError from None
    if source.parent != album.source or source.name not in album.photos:
        raise FileNotFoundError
    with ExitStack() as stack:
        directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, directory)
        for component in album.source.parts[1:]:
            directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            stack.callback(os.close, directory)
        descriptor = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as photo:
            if not stat.S_ISREG(os.fstat(photo.fileno()).st_mode):
                raise FileNotFoundError
            yield photo


def display_jpg(album: Album, filename: str) -> bytes:
    with open_photo(album, filename) as source, Image.open(source) as original:
        photo = ImageOps.exif_transpose(original)
        photo.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
        result = BytesIO()
        photo.convert("RGB").save(result, "JPEG", quality=88, optimize=True, progressive=True)
        return result.getvalue()


def photo_markup(album: Album, filename: str, *, album_link: bool = False, loading: str = "eager") -> str:
    try:
        with open_photo(album, filename) as source, Image.open(source) as photo:
            width, height = photo.size
            if photo.getexif().get(274) in {5, 6, 7, 8}:
                width, height = height, width
    except (OSError, ValueError):
        width, height = 3, 2
    image = f'<img src="{escape(image_url(album, filename))}" width="{width}" height="{height}" loading="{loading}" alt="">'
    if album_link:
        image = (f'<a class="photo-link" href="/albums/{album.slug}" '
                 f'aria-labelledby="album-{album.slug}-title">{image}</a>')
    return (f'<div class="photo-frame" style="--photo-ratio:{width}/{height}">{image}'
            '<div class="image-error" role="status" hidden><span>相片載入失敗</span>'
            '<button type="button">重試</button></div></div>')


def homepage(albums: list[Album]) -> str:
    body = '<header class="site-header"><h1>Albums</h1></header><main>'
    if not albums:
        return body + '<p class="empty-state">目前尚無公開相簿</p></main>'
    body += '<ul class="album-list">'
    for album in albums:
        body += (f'<li class="album-card"><figure>{photo_markup(album, album.cover, album_link=True)}'
                 f'<figcaption class="album-caption"><h2 class="album-title" id="album-{album.slug}-title">'
                 f'<a href="/albums/{album.slug}">{escape(album.title)}</a></h2>'
                 f'<div class="album-meta"><time datetime="{album.date.isoformat()}">{album.date.isoformat()}</time>'
                 f'<span>{len(album.photos)} 張相片</span></div></figcaption></figure></li>')
    return body + '</ul></main>'


def album_page(album: Album) -> str:
    body = ('<header class="site-header album-header"><a class="brand" href="/">Albums</a>'
            '<a href="/">← 所有相簿</a></header><main class="album-page">'
            f'<div class="album-heading"><div><h1>{escape(album.title)}</h1>'
            f'<p><time datetime="{album.date.isoformat()}">{album.date.isoformat()}</time>'
            f' · {len(album.photos)} 張相片</p></div>'
            f'<div class="album-tools" data-archive-url="/archives/{album.slug}.zip">'
            '<button type="button" class="prepare-archive">下載整本相簿 ZIP</button>'
            '<p>包含整本相簿的原始 JPG。</p>'
            '<p role="status" class="archive-status"></p>'
            '<progress aria-label="相簿 ZIP 準備進度" hidden></progress>'
            '<button type="button" class="cancel-archive" hidden>取消準備</button>'
            f'<a class="download-archive" download="{album.slug}.zip" hidden>取得相簿 ZIP</a>'
            '</div></div><ol class="photo-list">')
    for filename in album.photos:
        body += (f'<li class="photo-card"><figure>{photo_markup(album, filename, loading="lazy")}'
                 f'<figcaption>{escape(filename)}</figcaption></figure></li>')
    return body + '</ol></main>'


def document(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title>'
            f'<link rel="stylesheet" href="/static/showcase.css"><script src="/static/showcase.js" defer></script>'
            '<script src="/static/archive.js" defer></script>'
            f'</head><body>{body}</body></html>')


class ShowcaseHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        static_types = {"/static/showcase.css": "text/css", "/static/showcase.js": "text/javascript", "/static/archive.js": "text/javascript"}
        if path in static_types:
            self.send_content(200, static_types[path] + "; charset=utf-8", Path(__file__).with_name("static").joinpath(path.rsplit("/", 1)[1]).read_bytes())
            return
        parts = path.split("/")
        if len(parts) == 3 and parts[1] == "archives" and parts[2].endswith(".zip"):
            try:
                album = self.server.catalog[parts[2][:-4]]
                with TemporaryFile() as result:
                    with ZipFile(result, "w") as archive:
                        for filename in album.photos:
                            with open_photo(album, filename) as source, archive.open(filename, "w", force_zip64=True) as destination:
                                copyfileobj(source, destination)
                    size = result.tell()
                    result.seek(0)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", f'attachment; filename="{album.slug}.zip"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(size))
                    self.end_headers()
                    copyfileobj(result, self.wfile)
                return
            except (BrokenPipeError, ConnectionResetError):
                return
            except (KeyError, OSError, ValueError):
                pass
        if len(parts) == 4 and parts[1] == "images":
            try:
                album = self.server.catalog[parts[2]]
                content = display_jpg(album, unquote(parts[3], errors="strict"))
                self.send_content(200, "image/jpeg", content)
                return
            except (KeyError, OSError, ValueError):
                pass
        if path == "/":
            albums = sorted((album for album in self.server.catalog.values() if album.status == "public"), key=lambda album: album.date, reverse=True)
            self.send_page(200, "Albums", homepage(albums))
        elif len(parts) == 3 and parts[1] == "albums" and parts[2] in self.server.catalog:
            album = self.server.catalog[parts[2]]
            self.send_page(200, album.title, album_page(album))
        else:
            self.send_page(404, "找不到內容", '<main><h1>找不到這個相簿或相片</h1><a href="/">返回相簿列表</a></main>')

    def send_page(self, status: int, title: str, body: str):
        content = document(title, body).encode("utf-8")
        self.send_content(status, "text/html; charset=utf-8", content)

    def send_content(self, status: int, content_type: str, content: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> int:
    parser = argparse.ArgumentParser(description="發佈 Lightroom 相簿；固定讀取工作目錄的 config/albums.json。")
    parser.add_argument("--albums-root", type=Path, required=True, help="唯讀相簿根目錄")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 監聽位置（預設僅本機）")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 連接埠")
    args = parser.parse_args()
    try:
        catalog = load_catalog(args.albums_root)
        with ThreadingHTTPServer((args.host, args.port), ShowcaseHandler) as server:
            server.catalog = catalog
            print(f"相簿已啟動：http://{args.host}:{server.server_port}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"無法啟動相簿：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
