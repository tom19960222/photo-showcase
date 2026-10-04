"""以明確白名單發佈 Lightroom 相簿。"""

import argparse
from dataclasses import dataclass
from datetime import date
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


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


def document(title: str, body: str) -> str:
    return f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title></head><body>{body}</body></html>'


class ShowcaseHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path == "/":
            albums = sorted((album for album in self.server.catalog.values() if album.status == "public"), key=lambda album: album.date, reverse=True)
            body = "<main><h1>Albums</h1>"
            if albums:
                body += "<ul>" + "".join(f'<li><h2>{escape(album.title)}</h2><time datetime="{album.date.isoformat()}">{album.date.isoformat()}</time> · {len(album.photos)} 張相片</li>' for album in albums) + "</ul>"
            else:
                body += "<p>目前尚無公開相簿</p>"
            self.send_page(200, "Albums", body + "</main>")
        else:
            self.send_page(404, "找不到內容", '<main><h1>找不到這個相簿或相片</h1><a href="/">返回相簿列表</a></main>')

    def send_page(self, status: int, title: str, body: str):
        content = document(title, body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
