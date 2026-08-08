"""Create a submission from ``submission_template.json``."""

from pathlib import Path

from ledger.submission_assembler import SubmissionAssembler


assembler = SubmissionAssembler(
    template_path=Path("submission_template.json"),
    output_path=Path("submission.json"),
)
assembler.load_template()
assembler.set_metadata(run_id="run-2026-08-07")
assembler.update_answer(
    scenario_id="scenario-001",
    clause="DSCR",
    status="BREACH",
    actual=0.987,
    evidence_txn_id="txn-456",
)
assembler.save()
