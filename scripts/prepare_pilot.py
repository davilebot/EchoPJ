import csv
import json
import os
import re
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from plataforma_receita.normalization import digits, valid_cnpj  # noqa: E402


SOURCE = Path("/Users/davibotelho/Downloads/apollo-accounts-export (89).csv")
OUTPUT = Path("work/pilot_input.json")
CACHE_DIR = Path("work/site_cache")
USER_AGENT = "PlataformaReceitaPilot/0.1 (+company-data-validation)"
CNPJ_PATTERN = re.compile(r"(?<!\d)\d{2}[.\s-]?\d{3}[.\s-]?\d{3}[/\.\s-]?\d{4}[-.\s]?\d{2}(?!\d)")
LEGAL_LINK_WORDS = ("contato", "contact", "sobre", "quem-somos", "privacidade", "privacy", "termos", "legal")


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


def decode_response(response, body: bytes) -> str:
    charset = response.headers.get_content_charset() or "utf-8"
    try:
        return body.decode(charset, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def fetch(url: str, timeout: int = 9) -> tuple[str, str] | None:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    context = ssl.create_default_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            return None
        body = response.read(1_200_000)
        return response.geturl(), decode_response(response, body)


def robots_allowed(base_url: str, target_url: str) -> bool:
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        request = Request(robots_url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain"})
        with urlopen(request, timeout=3, context=ssl.create_default_context()) as response:
            body = response.read(200_000)
            parser.parse(decode_response(response, body).splitlines())
        return parser.can_fetch(USER_AGENT, target_url)
    except Exception:
        return True


def extract_page(url: str, html: str) -> tuple[list[str], list[str], list[str]]:
    parser = PageParser()
    parser.feed(html)
    text = re.sub(r"\s+", " ", unescape(" ".join(parser.text_parts))).strip()
    cnpjs = []
    for match in CNPJ_PATTERN.findall(text):
        number = digits(match)
        if valid_cnpj(number) and number not in cnpjs:
            cnpjs.append(number)
    names = []
    for pattern in (
        r'"legalName"\s*:\s*"([^"\\]{3,180})"',
        r'"alternateName"\s*:\s*"([^"\\]{3,180})"',
        r'"name"\s*:\s*"([^"\\]{3,180})"',
    ):
        for value in re.findall(pattern, html, re.I):
            value = unescape(value).strip()
            if value and value not in names:
                names.append(value)
    links = [urljoin(url, link) for link in parser.links]
    return cnpjs, names[:10], links


def crawl_site(website: str) -> dict:
    website = (website or "").strip()
    if not website:
        return {"status": "sem_site", "cnpjs": [], "names": [], "pages": []}
    initial = website if "://" in website else f"https://{website}"
    domain = urlparse(initial).netloc.casefold().removeprefix("www.")
    cache_path = CACHE_DIR / f"{re.sub(r'[^a-z0-9.-]+', '_', domain)}.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("status") != "erro":
            return cached

    result = {"status": "ok", "cnpjs": [], "names": [], "pages": [], "error": None}
    try:
        fetched = fetch(initial)
        if not fetched and initial.startswith("http://"):
            fetched = fetch("https://" + initial.removeprefix("http://"))
        if not fetched:
            raise RuntimeError("conteudo nao HTML")
        final_url, html = fetched
        if not robots_allowed(final_url, final_url):
            result["status"] = "bloqueado_robots"
        else:
            cnpjs, names, links = extract_page(final_url, html)
            result["cnpjs"].extend(cnpjs)
            result["names"].extend(names)
            result["pages"].append(final_url)
            base_domain = urlparse(final_url).netloc.casefold().removeprefix("www.")
            legal_links = []
            for link in links:
                parsed = urlparse(link)
                if parsed.netloc.casefold().removeprefix("www.") != base_domain:
                    continue
                if any(word in link.casefold() for word in LEGAL_LINK_WORDS) and link not in legal_links:
                    legal_links.append(link)
            for link in legal_links[:2]:
                if not robots_allowed(final_url, link):
                    continue
                time.sleep(0.25)
                try:
                    extra = fetch(link, timeout=7)
                    if not extra:
                        continue
                    page_url, page_html = extra
                    more_cnpjs, more_names, _ = extract_page(page_url, page_html)
                    result["cnpjs"].extend(value for value in more_cnpjs if value not in result["cnpjs"])
                    result["names"].extend(value for value in more_names if value not in result["names"])
                    result["pages"].append(page_url)
                except Exception:
                    continue
    except Exception as error:
        result["status"] = "erro"
        result["error"] = type(error).__name__
    result["names"] = result["names"][:15]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    with SOURCE.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = []
        for index, row in enumerate(csv.DictReader(stream), start=1):
            if index > 200:
                break
            rows.append(row)

    websites = list(dict.fromkeys(row["Website"] for row in rows if row["Website"].strip()))
    crawled: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(crawl_site, website): website for website in websites}
        for future in as_completed(futures):
            website = futures[future]
            try:
                crawled[website] = future.result()
            except Exception as error:
                crawled[website] = {"status": "erro", "cnpjs": [], "names": [], "pages": [], "error": type(error).__name__}

    output = []
    for index, row in enumerate(rows, start=1):
        site = crawled.get(row["Website"], {"status": "sem_site", "cnpjs": [], "names": [], "pages": []})
        output.append({
            "row_number": index,
            "apollo_account_id": row["Apollo Account Id"],
            "company_name": row["Company Name"],
            "website": row["Website"],
            "street": row["Company Street"],
            "city": row["Company City"],
            "state": "SP",
            "postal_code": row["Company Postal Code"],
            "address": row["Company Address"],
            "site_status": site["status"],
            "site_cnpjs": site["cnpjs"],
            "site_names": site["names"],
            "site_pages": site["pages"],
        })
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {}
    for item in output:
        counts[item["site_status"]] = counts.get(item["site_status"], 0) + 1
    print(json.dumps({"rows": len(output), "site_status": counts, "rows_with_site_cnpj": sum(bool(item["site_cnpjs"]) for item in output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
