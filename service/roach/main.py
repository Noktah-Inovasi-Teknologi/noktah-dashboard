"""CLI for listing, downloading, and analyzing Instagram/TikTok content.

Usage (inside the container):
    docker exec roach python main.py list <profile_url>
    docker exec roach python main.py download <content_url>
    docker exec roach python main.py analyze
"""
import argparse
import json
import sys
from pathlib import Path

import yt_dlp

import analyze as analyze_mod

DATA_DIR = Path(__file__).parent / "data"
COOKIES_FILE = Path(__file__).parent / "secrets" / "cookies.txt"


def _base_opts() -> dict:
    opts: dict = {"quiet": True, "no_warnings": True, "noprogress": True}
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    return opts


def list_urls(profile_url: str) -> list[str]:
    opts = {**_base_opts(), "extract_flat": True, "dump_single_json": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(profile_url, download=False)
    entries = info.get("entries", [info])
    return [e["url"] for e in entries if e and e.get("url")]


def download(content_url: str) -> str:
    DATA_DIR.mkdir(exist_ok=True)
    opts = {
        **_base_opts(),
        "outtmpl": str(DATA_DIR / "%(id)s.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(content_url, download=True)
    return ydl.prepare_filename(info)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List content URLs from a profile")
    list_p.add_argument("profile_url")

    dl_p = sub.add_parser("download", help="Download a single content URL")
    dl_p.add_argument("content_url")

    sub.add_parser("analyze", help="Analyze every downloaded video into one report.md")

    args = parser.parse_args()

    if args.command == "list":
        urls = list_urls(args.profile_url)
        print(json.dumps(urls, indent=2))
    elif args.command == "download":
        path = download(args.content_url)
        print(f"Saved to {path}")
    elif args.command == "analyze":
        report_path = analyze_mod.build_report(DATA_DIR)
        print(f"Report saved to {report_path}")


if __name__ == "__main__":
    sys.exit(main())
