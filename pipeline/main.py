"""
Orchestrates extract -> validate -> load, and doubles as the entrypoint for:
  - local/manual runs:      python -m pipeline.main
  - the scheduled GH Action: .github/workflows/run-pipeline.yml
  - AWS Lambda:              aws/lambda_handler.py imports run_pipeline()

Exits non-zero on hard failures (unreachable DB, zero valid records) so a
CI/CD or cron wrapper can alert on failure.
"""
import logging
import sys
from typing import Any, Dict

from pipeline.db import get_engine, init_db
from pipeline.extract import extract
from pipeline.load import load_records
from pipeline.validate import validate_records
from pipeline.warehouse import load_warehouse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def run_pipeline() -> Dict[str, Any]:
    logger.info("Starting pipeline run.")

    raw_records = extract()
    logger.info("Extracted %d raw record(s).", len(raw_records))

    result = validate_records(raw_records)
    logger.info("Validated: %d valid, %d rejected.", len(result.valid), len(result.errors))
    for err in result.errors:
        logger.warning("Rejected record: %s", err["error"])

    engine = init_db(get_engine())
    loaded = load_records(result.valid, engine)
    warehouse = load_warehouse(engine)

    summary = {
        "extracted": len(raw_records),
        "valid": len(result.valid),
        "rejected": len(result.errors),
        "loaded": loaded,
        "warehouse_inserted": warehouse["inserted"],
        "warehouse_updated": warehouse["updated"],
    }
    logger.info("Pipeline run complete: %s", summary)
    return summary


def main() -> int:
    try:
        summary = run_pipeline()
    except Exception:
        logger.exception("Pipeline run failed with an unhandled exception.")
        return 1

    if summary["extracted"] > 0 and summary["valid"] == 0:
        logger.error("All extracted records were rejected by validation.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
