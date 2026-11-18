#!/usr/bin/env python3
"""Check rendered site output without executing any post's commands."""

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "https://matthetherington.github.io"
POST = "/posts/build-asahi-trackpad-driver/"
SCRIPT = "/downloads/build-magicmouse-palm.py"
GISTS = "https://gist.github.com/matthetherington"


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.canonical = []
        self.links = []
        self.text_parts = []
        self.code_blocks = []
        self.pre = None
        self.in_gutter = False
        self.feed(text)
        self.close()

    @property
    def text(self):
        return "".join(self.text_parts)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and "canonical" in attrs.get("rel", "").split():
            self.canonical.append(attrs.get("href"))
        if "href" in attrs:
            self.links.append(attrs["href"])
        if tag == "td":
            self.in_gutter = "rouge-gutter" in attrs.get("class", "").split()
        if tag == "pre" and not self.in_gutter:
            self.pre = []

    def handle_endtag(self, tag):
        if tag == "pre" and self.pre is not None:
            self.code_blocks.append("".join(self.pre))
            self.pre = None
        if tag == "td":
            self.in_gutter = False

    def handle_data(self, data):
        self.text_parts.append(data)
        if self.pre is not None:
            self.pre.append(data)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_site(site):
    require(site.is_dir(), f"Build the site first; output directory not found: {site}")
    documents = {}
    for path in sorted(site.rglob("*.html")):
        relative = path.relative_to(site).as_posix()
        url_path = "/" + (relative[:-10] if relative.endswith("index.html") else relative)
        html = path.read_text()
        doc = Document(html)
        require(doc.canonical == [CANONICAL + url_path],
                f"{relative}: wrong or missing canonical URL: {doc.canonical}")
        for href in doc.links:
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
                continue
            parts = Path(unquote(parsed.path).lstrip("/")).parts
            require(".." not in parts, f"{relative}: link escapes the site: {href}")
            target = site.joinpath(*parts)
            if parsed.path.endswith("/"):
                target /= "index.html"
            require(target.is_file(), f"{relative}: broken internal link: {href}")
        require("UA-58906383-1" not in html, f"{relative}: old analytics tracker remains")
        require('id="mode-toggle"' in html and 'data-theme-mode="light"' in html,
                f"{relative}: the visitor's theme selector is missing")
        require('id="default-theme"' in html, f"{relative}: first-visit dark default is missing")
        # Chirpy's production compressor omits the optional </head> tag.
        require(0 <= html.find('src="/assets/js/dist/theme.min.js"') <
                html.index('id="default-theme"') < html.find("<body"),
                f"{relative}: dark default must run after Chirpy initializes and before the body")
        documents[url_path] = doc

    require(all(path in documents for path in (
        "/", "/gists/", "/404.html", "/categories/", "/tags/", "/archives/", "/about/",
    )),
            "A required page is missing")
    require(GISTS in documents["/"].links and GISTS in documents["/gists/"].links,
            "Homepage/gists page must link to the real Gist profile")
    require(POST in documents, "The trackpad walkthrough was not rendered")
    require(POST in documents["/"].links, "Homepage does not list the walkthrough")

    post = documents[POST]
    require(SCRIPT in post.links, "Walkthrough does not link to the hosted script")
    require(post.text.index("1. Matching kernel development headers") <
            post.text.index("2. Build tools") < post.text.index("Download the build script"),
            "Prerequisite instructions must come before the download link")
    commands = "\n".join(post.code_blocks)
    for command in (
        'sudo dnf install -y "kernel-devel-uname-r = $(uname -r)"',
        "sudo dnf install -y python3 gcc make git rpm cpio binutils kmod bubblewrap",
        CANONICAL + SCRIPT,
        "python3 build-magicmouse-palm.py --check",
        "python3 build-magicmouse-palm.py --output ./build",
        "sha256sum --check SHA256SUMS",
    ):
        require(command in commands, f"Missing copy-and-paste instruction: {command}")
    require("{{" not in commands and "YOUR_" not in commands, "Unexpanded placeholder in published commands")

    download = site / SCRIPT.lstrip("/")
    source = ROOT / SCRIPT.lstrip("/")
    require(download.is_file() and download.read_bytes() == source.read_bytes(), "Published script differs from source")
    digest = hashlib.sha256(download.read_bytes()).hexdigest()
    require(digest in commands, "Walkthrough's script checksum is stale or missing")
    require((site / "downloads/LICENSE").read_bytes() == (ROOT / "downloads/LICENSE").read_bytes(),
            "Download license is missing or changed")
    require("Nothing has been activated." in post.text, "Build-only limitation is missing")
    require('id="sidebar"' in (site / "index.html").read_text(), "Chirpy sidebar was not rendered")
    require((site / "assets/css/jekyll-theme-chirpy.css").is_file(), "Chirpy stylesheet is missing")
    require((site / "assets/js/dist/home.min.js").is_file(), "Chirpy home JavaScript is missing")
    search = json.loads((site / "assets/js/data/search.json").read_text())
    require(any(entry.get("url") == POST for entry in search), "Walkthrough is missing from Chirpy search")
    require(all(entry.get("url") in documents for entry in search), "Search index contains an unpublished page")
    require("Creative Commons" not in post.text and "CC BY" not in post.text,
            "The theme added a content license that was not selected for the blog")

    # Parse, but never run, the actual shell commands rendered into the article.
    for index, block in enumerate(post.code_blocks):
        if block.lstrip().startswith("~/"):  # Display-only output path.
            continue
        result = subprocess.run(["bash", "-n"], input=block, text=True, capture_output=True)
        require(result.returncode == 0, f"Invalid shell syntax in code block {index}: {result.stderr}")

    atom = {"a": "http://www.w3.org/2005/Atom"}
    feed = ET.parse(site / "feed.xml").getroot()
    require(any(link.get("href") == CANONICAL + "/feed.xml" for link in feed.findall("a:link", atom)),
            "Feed self URL does not use the canonical host")
    require(any(link.get("href") == CANONICAL + POST for link in feed.findall("a:entry/a:link", atom)),
            "Walkthrough is missing from the feed")
    sitemap = ET.parse(site / "sitemap.xml")
    locations = [node.text for node in sitemap.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    require(CANONICAL + POST in locations and CANONICAL + "/gists/" in locations,
            "Sitemap is missing published content")
    require(all(url and url.startswith(CANONICAL + "/") for url in locations),
            "Sitemap contains a noncanonical host")
    require("Sitemap: " + CANONICAL + "/sitemap.xml" in (site / "robots.txt").read_text(),
            "robots.txt has the wrong sitemap URL")

    for excluded in ("README.md", "NOTICE.md", "Gemfile", "Gemfile.lock", "scripts", "tests", "_drafts", "vendor"):
        require(not (site / excluded).exists(), f"Development-only content was published: {excluded}")
    print(f"Passed: {len(documents)} pages, canonical URLs, internal links, post commands, "
          "download checksum, Chirpy search, feed, sitemap, robots.txt and publication exclusions.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", nargs="?", type=Path, default=ROOT / "_site")
    args = parser.parse_args()
    check_site(args.site.resolve())
