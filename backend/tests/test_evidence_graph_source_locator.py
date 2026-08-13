from services.evidence_graph_extraction import EvidenceGraphDocumentUnit
from services.evidence_graph_source_locator import locate_execution_verdict


def _unit(page_number: int, text: str):
    return EvidenceGraphDocumentUnit(
        unit_id=f"page-{page_number}",
        graph_id="graph-1",
        doc_id="report-1",
        doc_type="final_report",
        filename="report.docx",
        native_text=text,
        page_number=page_number,
    )


def test_execution_verdict_does_not_cross_sample_or_mode_boundaries():
    units = [_unit(57, """
Page 56 of 83
Test Mode Mode 2
Sample No. E202508277046-0016
Reversed voltage 14V 60s A C
不
Pass
Test Mode Mode 2
Sample No. E202508277046-0017
Reversed voltage 14V 60s C C
Pass
""")]

    assert locate_execution_verdict(
        units, sample_id="E202508277046-0016", mode="Mode 2", verdict="Pass",
    ) is None
    failed = locate_execution_verdict(
        units, sample_id="E202508277046-0016", mode="Mode 2", verdict="不Pass",
    )
    passed = locate_execution_verdict(
        units, sample_id="E202508277046-0017", mode="Mode 2", verdict="Pass",
    )
    assert failed and failed[0].page_number == 57
    assert passed and passed[0].page_number == 57


def test_execution_matching_ignores_difference_between_file_and_printed_page_numbers():
    unit = _unit(57, """
Page 56 of 83
Test Mode Mode 2
Sample No. E202508277046-0016
Result 不 Pass
""")

    located = locate_execution_verdict(
        [unit], sample_id="E202508277046-0016", mode="Mode 2", verdict="不Pass",
    )

    assert located and located[0].page_number == 57
