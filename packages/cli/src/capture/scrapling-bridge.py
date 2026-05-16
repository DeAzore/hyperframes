#!/usr/bin/env python3
"""
Scrapling bridge for hyperframes capture.

Fetches a URL using Scrapling and writes JSON to stdout:
  { "html": "...", "title": "...", "text": "...", "stealthy": bool }
On error:
  { "error": "..." }

Usage:
  python3 scrapling-bridge.py <url> [--stealthy]

Requires:
  pip install scrapling
  # For --stealthy (Playwright-based bypass):
  pip install "scrapling[fetchers]" && scrapling install
"""
import sys
import json


def get_html(page) -> str:
    """Extract full HTML string from a Scrapling page object."""
    # Scrapling Adaptor wraps an lxml tree — serialize via lxml when available.
    try:
        from lxml import etree
        root = getattr(page, "root", None)
        if root is not None:
            return etree.tostring(root, encoding="unicode", method="html")
    except Exception:
        pass
    # Fallback: CSS selector to outer HTML of the html element
    try:
        result = page.css("html").get()
        if result:
            return result
    except Exception:
        pass
    # Last resort
    return str(page) if page else ""


def get_text(page) -> str:
    """Extract readable text from the page, skipping scripts and styles."""
    try:
        parts = []
        for tag in ("h1", "h2", "h3", "h4", "p", "li", "a", "span", "td", "th"):
            for el in page.css(tag):
                chunks = el.css("::text").getall()
                line = " ".join(c.strip() for c in chunks if c.strip())
                if line:
                    parts.append(line)
        return "\n".join(parts)
    except Exception:
        return ""


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: scrapling-bridge.py <url> [--stealthy]"}))
        sys.exit(1)

    url = sys.argv[1]
    stealthy = "--stealthy" in sys.argv[2:]

    try:
        if stealthy:
            from scrapling.fetchers import StealthyFetcher
            page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
        else:
            from scrapling.fetchers import Fetcher
            page = Fetcher.get(url, stealthy_headers=True)

        html = get_html(page)
        title = ""
        try:
            title = page.css("title::text").get() or ""
        except Exception:
            pass
        text = get_text(page)

        print(json.dumps({
            "html": html,
            "title": title,
            "text": text,
            "stealthy": stealthy,
        }))

    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
