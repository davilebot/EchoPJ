#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from plataforma_receita.rfb_manifest import import_plan, load_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Valida o manifesto sem importar nada")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    print(json.dumps(import_plan(load_manifest(args.manifest)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

