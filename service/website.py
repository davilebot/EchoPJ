import json
import re
import sqlite3
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from plataforma_receita.normalization import digits, valid_cnpj


CNPJ_PATTERN = re.compile(r"(?<!\d)\d{2}[.\s-]?\d{3}[.\s-]?\d{3}[/\.\s-]?\d{4}[-.\s]?\d{2}(?!\d)")
LEGAL_LINK_WORDS = ("contato", "contact", "sobre", "quem-somos", "privacidade", "privacy", "termos", "legal")
USER_AGENT = "PlataformaReceita/1.0 (+company-registration-validation)"


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "svg"}:
            self._skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.text_parts.append(data)


def extract_evidence(url: str, html: str) -> tuple[list[str], list[str], list[str]]:
    parser = PageParser()
    parser.feed(html)
    text = re.sub(r"\s+", " ", unescape(" ".join(parser.text_parts))).strip()
    cnpjs = []
    # Search visible content and structured data such as JSON-LD.  The checksum
    # validation prevents arbitrary 14-digit strings from becoming evidence.
    for source in (text, html):
        for match in CNPJ_PATTERN.findall(source):
            value = digits(match)
            if valid_cnpj(value) and value not in cnpjs:
                cnpjs.append(value)
    names = []
    for pattern in (
        r'"legalName"\s*:\s*"([^"\\]{3,180})"',
        r'"alternateName"\s*:\s*"([^"\\]{3,180})"',
        r'"name"\s*:\s*"([^"\\]{3,180})"',
    ):
        for value in re.findall(pattern, html, re.I):
            clean = unescape(value).strip()
            if clean and clean not in names:
                names.append(clean)
    return cnpjs, names[:12], [urljoin(url, link) for link in parser.links]


class WebsiteChecker:
    def __init__(self, cache_path: str, *, timeout: float = 3.0, workers: int = 10):
        self.cache_path = Path(cache_path)
        self.timeout = timeout
        self.workers = workers
        self._lock = threading.Lock()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.cache_path, check_same_thread=False)
        self._connection.execute("""
          CREATE TABLE IF NOT EXISTS website_cache (
            domain TEXT PRIMARY KEY,
            checked_at INTEGER NOT NULL,
            payload TEXT NOT NULL
          )
        """)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _domain(url: str) -> str:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        return parsed.netloc.casefold().removeprefix("www.")

    def _cached(self, domain: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT checked_at,payload FROM website_cache WHERE domain=?", (domain,)
            ).fetchone()
        if not row or row[0] < int(time.time()) - 30 * 86400:
            return None
        payload = json.loads(row[1])
        payload["cached"] = True
        return payload

    def _save(self, domain: str, payload: dict) -> None:
        stored = {**payload, "cached": False}
        with self._lock:
            self._connection.execute(
                "INSERT INTO website_cache(domain,checked_at,payload) VALUES(?,?,?) ON CONFLICT(domain) DO UPDATE SET checked_at=excluded.checked_at,payload=excluded.payload",
                (domain, int(time.time()), json.dumps(stored, ensure_ascii=False)),
            )
            self._connection.commit()

    @staticmethod
    def _decode(response, body: bytes) -> str:
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            return body.decode(charset, "replace")
        except LookupError:
            return body.decode("utf-8", "replace")

    def _fetch(self, url: str) -> tuple[str, str] | None:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        with urlopen(request, timeout=self.timeout, context=ssl.create_default_context()) as response:
            if "html" not in response.headers.get("Content-Type", "").casefold():
                return None
            return response.geturl(), self._decode(response, response.read(1_000_000))

    def _robots_allowed(self, base_url: str, target_url: str) -> bool:
        parsed = urlparse(base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        try:
            request = Request(robots_url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=min(1.5, self.timeout), context=ssl.create_default_context()) as response:
                parser.parse(self._decode(response, response.read(150_000)).splitlines())
            return parser.can_fetch(USER_AGENT, target_url)
        except Exception:
            return True

    def check(self, website: str) -> dict:
        website = (website or "").strip()
        if not website:
            return {"status": "sem_site", "cnpjs": [], "names": [], "pages_checked": [], "cached": False}
        initial = website if "://" in website else f"https://{website}"
        domain = self._domain(initial)
        cached = self._cached(domain)
        if cached:
            return cached
        payload = {"status": "ok", "cnpjs": [], "names": [], "pages_checked": [], "cached": False}
        try:
            if not self._robots_allowed(initial, initial):
                payload["status"] = "bloqueado_robots"
                self._save(domain, payload)
                return payload
            page = self._fetch(initial)
            if not page and initial.startswith("http://"):
                page = self._fetch("https://" + initial.removeprefix("http://"))
            if not page:
                raise RuntimeError("conteudo nao HTML")
            final_url, html = page
            if not self._robots_allowed(final_url, final_url):
                payload["status"] = "bloqueado_robots"
            else:
                cnpjs, names, links = extract_evidence(final_url, html)
                payload["cnpjs"] = cnpjs
                payload["names"] = names
                payload["pages_checked"].append(final_url)
                if not cnpjs:
                    base_domain = self._domain(final_url)
                    legal_links = []
                    for link in links:
                        if self._domain(link) == base_domain and any(word in link.casefold() for word in LEGAL_LINK_WORDS):
                            if link not in legal_links:
                                legal_links.append(link)
                    for link in legal_links[:1]:
                        if not self._robots_allowed(final_url, link):
                            continue
                        try:
                            extra = self._fetch(link)
                            if not extra:
                                continue
                            page_url, page_html = extra
                            extra_cnpjs, extra_names, _ = extract_evidence(page_url, page_html)
                            payload["cnpjs"].extend(value for value in extra_cnpjs if value not in payload["cnpjs"])
                            payload["names"].extend(value for value in extra_names if value not in payload["names"])
                            payload["pages_checked"].append(page_url)
                        except Exception:
                            pass
        except Exception as error:
            payload["status"] = "erro"
            payload["error"] = type(error).__name__
        self._save(domain, payload)
        return payload

    def check_many(self, items: list[dict]) -> dict[str, dict]:
        result = {}
        by_domain: dict[str, dict] = {}
        ids_by_domain: dict[str, list[str]] = {}
        for item in items:
            website = item.get("website")
            if not website:
                continue
            domain = self._domain(website)
            by_domain.setdefault(domain, item)
            ids_by_domain.setdefault(domain, []).append(item["local_id"])
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(self.check, item.get("website")): domain
                for domain, item in by_domain.items()
            }
            for future in as_completed(futures):
                domain = futures[future]
                evidence = future.result()
                for local_id in ids_by_domain[domain]:
                    result[local_id] = evidence
        return result
