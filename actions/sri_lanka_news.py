"""Fresh Sri Lankan headlines from news search, returned for spoken delivery."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from xml.etree import ElementTree


def _google_news_rss(limit: int) -> list[dict]:
    """Fetch recent Sri Lanka headlines without requiring a search API key."""
    query = quote_plus("Sri Lanka news when:1d")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 TUK-SriLanka-News/1.0",
        "Accept": "application/rss+xml, application/xml, text/xml,*/*;q=0.8",
    })
    with urlopen(request, timeout=8) as response:
        payload = response.read(2_000_000)
    root = ElementTree.fromstring(payload)
    items = []
    for node in root.findall("./channel/item"):
        raw_title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        snippet = (node.findtext("description") or "").strip()
        published = (node.findtext("pubDate") or "").strip()
        title, sep, source = raw_title.rpartition(" - ")
        if not sep:
            title, source = raw_title, "Google News"
        if title and link:
            items.append({"title": title.strip(), "source": source.strip(),
                          "snippet": snippet, "url": link, "published": published})
        if len(items) >= limit:
            break
    return items


def sri_lanka_news_action(parameters: dict, player=None, **_) -> str:
    limit = parameters.get("limit", 6)
    try:
        limit = max(3, min(int(limit), 8))
    except (TypeError, ValueError):
        limit = 6
    try:
        # Prefer fresh, keyless RSS results; fall back to the existing news search.
        try:
            results = _google_news_rss(limit + 3)
        except Exception as rss_exc:
            print(f"[SriLankaNews] Google News RSS unavailable: {rss_exc}")
            results = []
        if not results:
            from actions.web_search import _ddg_news
            query = "Sri Lanka latest local news Ada Derana Newsfirst Lankadeepa Daily Mirror ITN"
            results = _ddg_news(query, max_results=limit + 3)
        seen = set()
        items = []
        for item in results:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            key = title.casefold()
            if key in seen:
                continue
            seen.add(key)
            source = (item.get("source") or "").strip()
            snippet = (item.get("snippet") or item.get("body") or "").strip()
            url = (item.get("url") or item.get("href") or "").strip()
            items.append((title, source, snippet, url))
            if len(items) >= limit:
                break
        if not items:
            return "Sri Lankan headlines are unavailable right now. Please try again shortly."
        stamp = datetime.now(ZoneInfo("Asia/Colombo")).strftime("%I:%M %p, %d %B")
        lines = [f"Latest Sri Lanka headlines as of {stamp}, Sri Lanka time:"]
        for i, (title, source, snippet, url) in enumerate(items, 1):
            line = f"{i}. {title}"
            if source:
                line += f". Source: {source}"
            if snippet:
                line += f". {snippet[:240]}"
            lines.append(line)
            if url:
                lines.append(f"Source link: {url}")
        result = "\n".join(lines)
        if player:
            try:
                player.write_log("SYS: Sri Lanka news briefing fetched.")
            except Exception:
                pass
        return result
    except Exception as exc:
        print(f"[SriLankaNews] headline retrieval failed: {exc}")
        return "I couldn't retrieve the latest Sri Lankan headlines right now. Please try again shortly."


TOOL = {
    "name": "sri_lanka_news",
    "description": (
        "Fetches CURRENT Sri Lankan news headlines from online news search, including reports from "
        "major local outlets such as Newsfirst, Ada Derana, Lankadeepa, Daily Mirror and ITN where available. "
        "Use when asked for Sri Lanka news, today's headlines, local breaking news, or what is happening in Sri Lanka. "
        "Return the headlines for spoken delivery; do NOT open a browser tab. State that reports are attributed to their source, "
        "do not present unverified claims as confirmed facts, and keep the spoken summary concise."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "limit": {"type": "INTEGER", "description": "Number of headlines to return, 3 to 8; default 6"},
        },
        "required": [],
    },
    "handler": sri_lanka_news_action,
}
