import html
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from app.config import CONFIG
from app.tools.base import Tool
from app.tools.launch import spawn


class OpenWebsiteTool(Tool):

    name = "open_website"

    description = (
        "Open a website in the user's default browser, "
        "except YouTube, which must open in Brave."
    )

    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Website URL or domain name.",
            }
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    WEBSITE_ALIASES = {
        "youtube": "https://www.youtube.com",
        "google": "https://www.google.com",
        "gmail": "https://mail.google.com",
        "github": "https://github.com",
        "chatgpt": "https://chatgpt.com",
        "kalvium community": "https://kalvium.community",
    }

    def execute(self, url: str):
        normalized = " ".join(
            url.strip().lower().split()
        )

        url = self.WEBSITE_ALIASES.get(
            normalized,
            url.strip(),
        )

        if "://" not in url:
            url = f"https://{url}"

        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return {
                "success": False,
                "error": "Only HTTP and HTTPS websites are allowed.",
            }

        if not parsed.netloc:
            return {
                "success": False,
                "error": "Invalid website address.",
            }

        try:
            if self._is_youtube(url):
                opened = self._open_with_brave(url)
            else:
                opened = self._open_default_browser(url)

            if not opened:
                return {
                    "success": False,
                    "url": url,
                    "error": "The browser could not open the website.",
                }

            return {
                "success": True,
                "url": url,
                "browser": "Brave" if self._is_youtube(url) else "default",
            }

        except Exception as error:
            return {
                "success": False,
                "url": url,
                "error": str(error),
            }

    @staticmethod
    def _is_youtube(url: str) -> bool:
        hostname = urlparse(url).netloc.lower()
        return hostname.endswith("youtube.com") or hostname.endswith(
            "youtu.be"
        )

    @staticmethod
    def _open_default_browser(url: str) -> bool:
        # os.startfile hands the URL to the shell, so the browser is never a
        # child of EDITH and cannot write into our terminal.
        if hasattr(os, "startfile"):
            try:
                os.startfile(url)
                return True

            except OSError:
                return False

        import webbrowser

        return webbrowser.open(url)

    @staticmethod
    def _open_with_brave(url: str) -> bool:
        brave_paths = [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            str(
                Path.home()
                / r"AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"
            ),
        ]

        for brave_path in brave_paths:
            if not Path(brave_path).exists():
                continue

            try:
                spawn([brave_path, url])

            except OSError:
                continue

            # Previously this returned process.poll() is None, which is true
            # immediately after any spawn and so reported success even when
            # the path did not exist.
            return True

        return False

class PlayMediaTool(Tool):

    name = "play_media"

    description = (
        "Play a song or video on YouTube. Resolves the query to an actual "
        "video and opens it playing, rather than showing search results."
    )

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Song, artist or video to play.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def execute(self, query: str):
        query = (query or "").strip()

        if not query:
            return {
                "success": False,
                "error": "Tell me what you want me to play.",
            }

        video = self._resolve_video(query)

        # Falling back to a search page is a materially worse outcome than
        # playing the track, so say which one happened rather than reporting
        # a generic success.
        if video:
            url = f"https://www.youtube.com/watch?v={video['id']}"
        else:
            url = (
                "https://www.youtube.com/results?search_query="
                + quote_plus(query)
            )

        if not OpenWebsiteTool._open_with_brave(url):
            if not OpenWebsiteTool._open_default_browser(url):
                return {
                    "success": False,
                    "error": "No browser could open YouTube.",
                }

        return {
            "success": True,
            "query": query,
            "url": url,
            "playing": bool(video),
            "title": video["title"] if video else None,
        }

    @staticmethod
    def _resolve_video(query: str) -> dict | None:
        """
        Resolve a query to a video id without starting a browser.

        yt-dlp is optional: without it we degrade to a search page instead
        of failing outright.
        """
        try:
            from yt_dlp import YoutubeDL

        except ImportError:
            return None

        options = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "default_search": "ytsearch1",
            "socket_timeout": 8,
        }

        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)

        except Exception:
            return None

        entries = (info or {}).get("entries") or []

        if not entries:
            return None

        entry = entries[0]

        if not entry.get("id"):
            return None

        return {
            "id": entry["id"],
            "title": entry.get("title") or query,
        }


class WebSearchTool(Tool):

    name = "search_web"

    description = (
        "Search the internet and return the actual text of the pages found, "
        "so you can read it and answer the user's question. "
        "Use this for news, current information, latest versions, recent "
        "events, research questions, or anything that may have changed. "
        "Answer from the returned content; do not use this for stable facts "
        "you already know confidently."
    )

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The complete subject or question to search for."
                ),
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def execute(self, query: str):
        query = query.strip()

        if not query:
            return {
                "success": False,
                "error": "Search query cannot be empty.",
            }

        if CONFIG.search.api_key:
            result = self._tavily(query)

            if result is not None:
                return result

        # Scraping DuckDuckGo yields titles and links but no page text, so
        # the model can list sources without being able to answer from them.
        # Kept only as a fallback when the API is unavailable.
        return self._duckduckgo(query)

    # ------------------------------------------------------------------

    @staticmethod
    def _tavily(query: str):
        """Returns a result dict, or None to fall back."""
        settings = CONFIG.search

        payload = json.dumps({
            "query": query,
            "max_results": settings.max_results,
            "search_depth": settings.depth,
            "include_answer": True,
        }).encode("utf-8")

        request = Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.api_key}",
            },
        )

        try:
            with urlopen(request, timeout=settings.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))

        except HTTPError as error:
            # A bad key or exhausted quota is worth reporting rather than
            # silently degrading to a worse search.
            if error.code in (401, 403, 432):
                return {
                    "success": False,
                    "error": (
                        f"The search service rejected the request "
                        f"({error.code}). The Tavily API key may be invalid "
                        f"or out of credit."
                    ),
                }

            return None

        except (URLError, TimeoutError, json.JSONDecodeError, OSError):
            return None

        results = []

        for item in data.get("results", []):
            content = (item.get("content") or "").strip()

            if not content:
                continue

            results.append({
                "title": (item.get("title") or "").strip(),
                "url": item.get("url", ""),
                "content": content[:settings.chars_per_result],
            })

        if not results:
            return None

        answer = (data.get("answer") or "").strip()

        return {
            "success": True,
            "query": query,
            "source": "tavily",
            # Tavily's own one-line synthesis. Useful, but the model should
            # still verify it against the passages below.
            "answer": answer or None,
            "results": results,
        }

    @staticmethod
    def _duckduckgo(query: str):
        search_url = (
            "https://html.duckduckgo.com/html/?q="
            + quote_plus(query)
        )

        request = Request(
            search_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        )

        try:
            with urlopen(request, timeout=15) as response:
                page = response.read().decode(
                    "utf-8",
                    errors="replace",
                )

            results = WebSearchTool._extract_results(page)

            if not results:
                return {
                    "success": False,
                    "error": "No readable search results were found.",
                }

            return {
                "success": True,
                "query": query,
                "source": "duckduckgo",
                # No page text: the model can cite these but cannot answer
                # a factual question from them.
                "links_only": True,
                "results": results[:6],
            }

        except Exception as error:
            return {
                "success": False,
                "error": f"Web search failed: {error}",
            }

    @staticmethod
    def _extract_results(page: str) -> list[dict]:
        results = []

        pattern = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>'
            r"(.*?)"
            r"</a>",
            re.IGNORECASE | re.DOTALL,
        )

        for match in pattern.finditer(page):
            url = unquote(
                html.unescape(match.group(1))
            )

            title = re.sub(
                r"<[^>]+>",
                " ",
                match.group(2),
            )

            title = html.unescape(title)
            title = " ".join(title.split())

            if url.startswith("//"):
                url = f"https:{url}"

            parsed = urlparse(url)

            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or not title
            ):
                continue

            if any(
                item["url"] == url
                for item in results
            ):
                continue

            results.append({
                "title": title,
                "url": url,
            })

        return results