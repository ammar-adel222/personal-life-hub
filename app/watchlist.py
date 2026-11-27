"""Watchlist URL validation, SSRF protection, and title/metadata extraction."""

from __future__ import annotations

import html
import ipaddress
import re
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"}


def is_safe_http_url(url: str) -> bool:
    """Allow only public http/https URLs and block local/private SSRF targets."""
    raw = (url or "").strip()
    if not raw or len(raw) > 2000:
        return False
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host in _BLOCKED_HOSTS or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    except ValueError:
        pass
    return True


def detect_url_info(url: str) -> dict[str, str]:
    """Infer platform source and content_type ('playlist'|'video'|'course'|'podcast'|'article')."""
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = (parsed.path or "").lower()
    qs = parse_qs(parsed.query or "")

    source = host.split(".")[0].title() if host else ""
    content_type = "article"

    if host in ("youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"):
        source = "YouTube"
        if "list" in qs or "/playlist" in path:
            content_type = "playlist"
        else:
            content_type = "video"
    elif "coursera.org" in host:
        source = "Coursera"
        content_type = "course"
    elif "udemy.com" in host:
        source = "Udemy"
        content_type = "course"
    elif "edx.org" in host or "stanford.edu" in host or "mit.edu" in host:
        source = "Course" if "stanford" not in host and "mit" not in host else host.split(".")[-2].upper()
        content_type = "course"
    elif "spotify.com" in host or "podcasts.apple.com" in host:
        source = "Spotify" if "spotify" in host else "Apple Podcasts"
        content_type = "playlist" if "/playlist" in path else "podcast"
    elif "github.com" in host:
        source = "GitHub"
        content_type = "article"
    elif "twitch.tv" in host or "vimeo.com" in host:
        source = "Twitch" if "twitch" in host else "Vimeo"
        content_type = "video"
    elif host:
        parts = [p for p in host.split(".") if p not in ("com", "org", "net", "io", "dev")]
        source = (parts[-1] if parts else host).title()

    return {"source": source, "content_type": content_type}


def _fallback_title_from_url(url: str, info: dict[str, str]) -> str:
    parsed = urlparse(url)
    path = (parsed.path or "").strip("/")
    if path:
        seg = path.split("/")[-1].replace("-", " ").replace("_", " ").strip()
        if seg and seg.lower() not in ("watch", "playlist", "video"):
            return f"{info['source']}: {seg}"[:100] if info["source"] else seg[:100]
    return f"{info['source']} {info['content_type'].title()}".strip() or url[:80]


def _extract_html_title(raw_html: str) -> str:
    if not raw_html:
        return ""
    og_match = re.search(
        r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        raw_html,
        flags=re.IGNORECASE,
    ) or re.search(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:title["\']',
        raw_html,
        flags=re.IGNORECASE,
    )
    if og_match:
        title = html.unescape(og_match.group(1)).strip()
        if title:
            return title
    t_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.IGNORECASE | re.DOTALL)
    if t_match:
        title = re.sub(r"\s+", " ", html.unescape(t_match.group(1))).strip()
        title = re.sub(r"\s*[-|]\s*YouTube$", "", title, flags=re.IGNORECASE).strip()
        if title and title.lower() != "youtube":
            return title
    return ""


async def fetch_url_metadata(url: str) -> dict[str, Any]:
    """Fetch page/playlist title safely without ever raising network failures as 500s."""
    clean_url = (url or "").strip()
    if not is_safe_http_url(clean_url):
        raise ValueError("Please enter a valid public http:// or https:// URL.")

    info = detect_url_info(clean_url)
    title = ""
    fetched = False

    try:
        async with httpx.AsyncClient(
            timeout=5.5,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PersonalLifeHub/1.0"},
        ) as client:
            if info["source"] == "YouTube":
                oembed_url = f"https://www.youtube.com/oembed?url={quote(clean_url, safe='')}&format=json"
                resp = await client.get(oembed_url)
                if resp.status_code == 200:
                    data = resp.json()
                    raw_t = str(data.get("title") or "").strip()
                    if raw_t:
                        title = raw_t
                        fetched = True
            if not title:
                resp = await client.get(clean_url)
                if resp.status_code == 200 and "html" in (resp.headers.get("content-type") or "").lower():
                    title = _extract_html_title(resp.text[:131072])
                    fetched = bool(title)
    except Exception:
        fetched = False

    if not title:
        title = _fallback_title_from_url(clean_url, info)

    return {
        "url": clean_url,
        "title": title[:160],
        "source": info["source"],
        "content_type": info["content_type"],
        "fetched": fetched,
    }
