import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from plataforma_receita.matcher import decide  # noqa: E402


def main():
    inputs = json.loads(Path("work/pilot_input.json").read_text(encoding="utf-8"))
    candidate_batches = json.loads(Path("work/pilot_candidates.json").read_text(encoding="utf-8"))
    candidates_by_row = {item["row_number"]: item for item in candidate_batches}
    results = []
    for item in inputs:
        batch = candidates_by_row.get(item["row_number"], {"candidates": [], "dataset_version": None})
        decision = decide(item, batch["candidates"])
        decision["dataset_version"] = batch.get("dataset_version")
        results.append(decision)
    Path("work/pilot_matches.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"confirmado": 0, "revisao": 0, "nao_encontrado": 0}
    for result in results:
        summary[result["status"]] += 1
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

