"""Instrument list extraction — manufacturer anchor method + calibration validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, date

from utils.logger import get_logger

logger = get_logger(__name__)

from services.raw_records_filename_parser import RawRecordMeta

MANUFACTURERS = sorted([
    "苏州泰思特电子科技有限公司", "苏州泰思特",
    "ROHDE&SCHWARZ", "SCHWARZBECK", "EMTEST", "Tektronix",
    "R&S", "KIKUSUI", "SOLAR", "AMETEK", "JD", "SIGLENT",
    "3ctest", "COM-MW", "GJ",
], key=len, reverse=True)

_SOFTWARE_NAMES = {"EMC32", "EMI测试系统", "EMI Test System"}

_INST_LINE = re.compile(
    r"(.+?)\s+([A-Za-z0-9#\-]+)\s+([\d]{4}\s*-\s*[\d]{2}\s*-\s*[\d]{2}|/)"
)


@dataclass
class InstrumentRecord:
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    serial_no: str = ""
    calibration_end: str = ""
    source_file: str = ""


@dataclass
class CalibrationIssue:
    instrument_name: str = ""
    serial_no: str = ""
    test_date: str = ""
    calibration_end: str = ""
    days_expired: int = 0
    severity: str = "CRITICAL"
    error_description: str = ""


def parse_instrument_section(proc_text: str) -> list[dict]:
    sec = re.search(
        r'测试使用仪器列表.*?仪器名称\s+制造商\s+型号\s+系列号\s+校准有效期\s*(.+?)(?=检测人员|$)',
        proc_text,
        flags=re.S,
    )
    if not sec:
        return []

    section = sec.group(1)
    instruments: list[dict] = []
    skipped_software = 0

    # Prefer row-by-row parsing. PDF text extraction preserves the instrument
    # table rows well enough, while flattening the whole table into one line
    # makes repeated manufacturers such as EMTEST/3ctest collapse together.
    for raw_line in section.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        parsed = _parse_instrument_line(line)
        if not parsed:
            continue
        if parsed["name"] in _SOFTWARE_NAMES or parsed["model"] in _SOFTWARE_NAMES:
            skipped_software += 1
            continue
        if parsed["serial_no"] == "/" or parsed["calibration_end"] == "/":
            logger.warning("serial_parse_failed", line=line[:120])
            continue
        instruments.append(parsed)

    logger.info("instruments_extracted", count=len(instruments), skipped_software=skipped_software)
    return instruments


def _parse_instrument_line(line: str) -> dict | None:
    cal_match = re.search(r"(\d{4}\s*-\s*\d{2}\s*-\s*\d{2}|/)\s*$", line)
    if not cal_match:
        return None

    cal = cal_match.group(1).replace(" ", "")
    body = line[:cal_match.start()].strip()
    manufacturer = ""
    mfr_idx = -1
    for mfr in MANUFACTURERS:
        idx = body.find(mfr)
        if idx >= 0 and (mfr_idx < 0 or idx < mfr_idx):
            manufacturer = mfr
            mfr_idx = idx
    if not manufacturer:
        return None

    name = body[:mfr_idx].strip()
    rest = body[mfr_idx + len(manufacturer):].strip()
    mm = re.match(r"(.+?)\s+([A-Za-z0-9]+(?:/[A-Za-z0-9]+)*)$", rest)
    if not name or not mm:
        logger.warning("serial_parse_failed", line=line[:120])
        return None

    return {
        "name": name,
        "manufacturer": manufacturer,
        "model": mm.group(1).strip(),
        "serial_no": mm.group(2).strip(),
        "calibration_end": cal,
    }


def extract_instruments(meta: RawRecordMeta) -> list[InstrumentRecord]:
    proc = meta.raw_text.replace("\r\n", "\n").replace("\r", "\n")
    return [
        InstrumentRecord(**r, source_file=meta.filename)
        for r in parse_instrument_section(proc)
    ]


def extract_all_instruments(metas: list[RawRecordMeta]) -> list[InstrumentRecord]:
    all_inst: list[InstrumentRecord] = []
    for meta in metas:
        if meta.raw_text:
            all_inst.extend(extract_instruments(meta))
    return all_inst


def deduplicate_instruments(instruments: list[InstrumentRecord]) -> list[InstrumentRecord]:
    key_map: dict[tuple, InstrumentRecord] = {}
    for inst in instruments:
        key = (inst.manufacturer, inst.model, inst.serial_no)
        if key in key_map:
            if inst.calibration_end > key_map[key].calibration_end:
                key_map[key] = inst
        else:
            key_map[key] = inst
    return list(key_map.values())


def validate_calibration(
    instruments: list[InstrumentRecord], test_date_str: str,
) -> list[CalibrationIssue]:
    td = _parse_date(test_date_str)
    if not td:
        return []

    issues: list[CalibrationIssue] = []
    for inst in instruments:
        cal = _parse_date(inst.calibration_end)
        if not cal:
            continue
        if td > cal:
            days = (td - cal).days
            issues.append(CalibrationIssue(
                instrument_name=inst.name, serial_no=inst.serial_no,
                test_date=str(td), calibration_end=inst.calibration_end,
                days_expired=days, severity="CRITICAL",
                error_description=(
                    f"仪器 {inst.name}({inst.serial_no}) 校准已过期 {days} 天: "
                    f"测试日期 {td} > 校准有效期 {inst.calibration_end}"
                ),
            ))
    return issues


def validate_all_calibrations(
    instruments: list[InstrumentRecord],
    metas: list[RawRecordMeta],
) -> list[CalibrationIssue]:
    dates = [_parse_date(m.test_date_str) for m in metas if m.test_date_str]
    dates = [d for d in dates if d]
    if not dates:
        return []
    return validate_calibration(instruments, min(dates).strftime("%Y/%m/%d"))


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    for fmt in ["%Y/%m/%d", "%Y-%m-%d"]:
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", s.strip())
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None
