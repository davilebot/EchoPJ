#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from plataforma_receita.rfb_manifest import discover_manifest, manifest_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Lista os ZIPs sem baixa-los")
    parser.add_argument("--version", required=True, help="competencia AAAA-MM")
    parser.add_argument("--directory", required=True, help="diretorio da competencia")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = discover_manifest(args.version, args.directory)
    args.output.write_text(
        json.dumps(manifest_payload(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"version": manifest.version, "files": len(manifest.files)}))


if __name__ == "__main__":
    main()
