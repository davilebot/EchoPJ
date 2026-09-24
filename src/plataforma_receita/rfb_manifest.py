"""Manifest validation for a complete Receita CNPJ auxiliary import."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import base64
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .rfb_layout import LAYOUTS


@dataclass(frozen=True)
class ManifestFile:
    kind: str
    url: str
    name: str
    size: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class Manifest:
    version: str
    source_url: str
    files: tuple[ManifestFile, ...]


def kind_from_filename(filename: str) -> str | None:
    normalized = filename.lower()
    rules = (
        (r"estabelecimento", "establishments"),
        (r"empresa", "companies"),
        (r"socio", "partners"),
        (r"simples", "simples"),
        (r"cnae", "reference_cnaes"),
        (r"pa[ií]s", "reference_countries"),
        (r"natureza", "reference_legal_natures"),
        (r"munic[ií]pio", "reference_municipalities"),
        (r"qualifica", "reference_qualifications"),
        (r"motivo", "reference_status_reasons"),
    )
    for pattern, kind in rules:
        if re.search(pattern, normalized):
            return kind
    return None


def parse_manifest(payload: dict[str, Any]) -> Manifest:
    version = str(payload.get("version") or "").strip()
    source_url = str(payload.get("source_url") or payload.get("source") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", version):
        raise ValueError("version deve usar o formato AAAA-MM")
    if not source_url:
        raise ValueError("manifesto sem source_url")

    files: list[ManifestFile] = []
    for raw in payload.get("files") or []:
        url = str(raw.get("url") or "").strip()
        name = str(raw.get("name") or url.rsplit("/", 1)[-1]).strip()
        kind = str(raw.get("kind") or raw.get("type") or kind_from_filename(name) or "").strip()
        if kind not in LAYOUTS:
            raise ValueError(f"tipo desconhecido para {name}: {kind or 'nao identificado'}")
        if not url.lower().startswith(("https://", "http://")):
            raise ValueError(f"URL invalida para {name}")
        size = raw.get("size")
        files.append(ManifestFile(
            kind=kind,
            url=url,
            name=name,
            size=int(size) if size is not None else None,
            sha256=str(raw.get("sha256") or "").strip() or None,
        ))

    if not files:
        raise ValueError("manifesto sem arquivos")
    names = [item.name for item in files]
    if len(names) != len(set(names)):
        raise ValueError("manifesto contem nomes de arquivo repetidos")

    required = {"companies", "establishments", "partners", "simples"}
    missing = required - {item.kind for item in files}
    if missing:
        raise ValueError(f"manifesto incompleto; faltam: {', '.join(sorted(missing))}")
    return Manifest(version=version, source_url=source_url, files=tuple(files))


def load_manifest(path: Path) -> Manifest:
    return parse_manifest(json.loads(path.read_text(encoding="utf-8")))


def manifest_from_links(version: str, directory_url: str, links: list[str]) -> Manifest:
    files = []
    seen: set[str] = set()
    for link in links:
        url = urllib.parse.urljoin(directory_url, link)
        name = urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1])
        kind = kind_from_filename(name)
        if not kind or not name.lower().endswith(".zip") or name in seen:
            continue
        seen.add(name)
        files.append({"kind": kind, "name": name, "url": url})
    files.sort(key=lambda item: (item["kind"], item["name"]))
    return parse_manifest({
        "version": version,
        "source_url": directory_url,
        "files": files,
    })


def discover_manifest(version: str, directory_url: str) -> Manifest:
    request = urllib.request.Request(
        directory_url,
        headers={"User-Agent": "PlataformaReceitaManifest/0.1"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        html = response.read().decode("utf-8", "replace")
    links = re.findall(r'href=["\']([^"\']+)', html, flags=re.IGNORECASE)
    return manifest_from_links(version, directory_url, links)


def official_webdav_directory(share_url: str, version: str) -> tuple[str, str]:
    """Return the public WebDAV directory and token for an official share URL."""
    parsed = urllib.parse.urlparse(share_url)
    match = re.search(r"/index\.php/s/([^/?#]+)", parsed.path)
    if not match:
        raise ValueError("URL oficial deve usar /index.php/s/TOKEN")
    token = match.group(1)
    base = f"{parsed.scheme}://{parsed.netloc}"
    directory = f"{base}/public.php/dav/files/{token}/{version}/"
    return directory, token


def discover_official_manifest(version: str, share_url: str) -> Manifest:
    """List one monthly folder from Receita's public Nextcloud share."""
    directory, token = official_webdav_directory(share_url, version)
    authorization = base64.b64encode(f"{token}:".encode()).decode()
    request = urllib.request.Request(
        directory,
        method="PROPFIND",
        headers={
            "Authorization": f"Basic {authorization}",
            "Depth": "1",
            "User-Agent": "PlataformaReceitaManifest/0.2",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        root = ET.fromstring(response.read())

    files: list[dict[str, Any]] = []
    for item in root.findall("{DAV:}response"):
        href_node = item.find("{DAV:}href")
        if href_node is None or not href_node.text:
            continue
        name = urllib.parse.unquote(href_node.text.rstrip("/").rsplit("/", 1)[-1])
        kind = kind_from_filename(name)
        if not kind or not name.lower().endswith(".zip"):
            continue
        size_node = item.find(".//{DAV:}getcontentlength")
        files.append({
            "kind": kind,
            "name": name,
            "url": urllib.parse.urljoin(directory, urllib.parse.quote(name)),
            "size": int(size_node.text) if size_node is not None and size_node.text else None,
        })
    files.sort(key=lambda item: (item["kind"], item["name"]))
    return parse_manifest({
        "version": version,
        "source_url": directory,
        "files": files,
    })


def manifest_payload(manifest: Manifest) -> dict[str, Any]:
    return {
        "version": manifest.version,
        "source_url": manifest.source_url,
        "files": [
            {
                key: value
                for key, value in {
                    "kind": item.kind,
                    "name": item.name,
                    "url": item.url,
                    "size": item.size,
                    "sha256": item.sha256,
                }.items()
                if value is not None
            }
            for item in manifest.files
        ],
    }


def import_plan(manifest: Manifest) -> dict[str, Any]:
    sizes = [item.size for item in manifest.files]
    return {
        "version": manifest.version,
        "source_url": manifest.source_url,
        "files": len(manifest.files),
        "known_download_bytes": sum(size for size in sizes if size is not None),
        "unknown_size_files": sum(size is None for size in sizes),
        "by_kind": {
            kind: sum(item.kind == kind for item in manifest.files)
            for kind in sorted({item.kind for item in manifest.files})
        },
    }
