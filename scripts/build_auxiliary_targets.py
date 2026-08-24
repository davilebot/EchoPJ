import json
from pathlib import Path


matches = json.loads(Path("work/pilot_matches.json").read_text(encoding="utf-8"))
targets = []
for result in matches:
    if result["status"] != "confirmado" or not result.get("selected"):
        continue
    targets.append({
        "row_number": result["row_number"],
        "cnpj": result["selected"]["cnpj"],
        "cnpj_root": result["selected"]["cnpj_root"],
    })
Path("work/auxiliary_targets.json").write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"confirmed_targets": len(targets)}, ensure_ascii=False))

