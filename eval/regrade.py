"""Re-grade a stored run with the current grader, without calling the LLM.

    python -m eval.regrade eval/results/20261105T030657Z.json --questions <file>

Model SQL stored in the run is executed again on a fresh import of the sample data and
compared with the references. Answers that were routed to the retention engine and
clarify/reject answers keep their stored grade (the run before the Phase 7 review did not
store engine values; their grading rule did not change). Prints a before/after table.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from eval.run_eval import QUESTIONS, _prepare, _reference_result, load_questions, results_match


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-grade a stored evaluation run.")
    parser.add_argument("run", type=Path)
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    args = parser.parse_args(argv)

    from backend.core.config import Settings
    from backend.main import create_app

    stored = json.loads(args.run.read_text(encoding="utf-8"))
    questions = {q.id: q for q in load_questions(args.questions)}
    changed = 0
    with tempfile.TemporaryDirectory(prefix="insightsql-regrade-") as tmp:
        settings = Settings(_env_file=None, data_dir=Path(tmp), openai_api_key=None)
        app = create_app(settings)
        with TestClient(app) as client:
            dataset_id = _prepare(client)
            path = app.state.dataset_service.duckdb_path(dataset_id)
            print("| ID | Stored | Re-graded | Detail |\n|---|---|---|---|")
            for record in stored["records"]:
                q = questions[record["id"]]
                if q.expected.kind != "result" or record["status"] != "answered":
                    continue
                model = _reference_result(path, record["sql"])
                reference = _reference_result(path, q.expected.reference_sql)
                ok, detail = results_match(reference, model)
                changed += ok != record["correct"]
                print(f"| {record['id']} | {record['correct']} | {ok} | {detail} |")
    print(f"\nGrades changed: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
