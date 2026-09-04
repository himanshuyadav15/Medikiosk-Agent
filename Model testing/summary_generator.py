"""
MediKiosk — Clinical summary generator.

Parses the raw SUMMARY_READY JSON from the interview LLM and produces
a formatted clinical note matching the Doctor Dashboard's exact layout.
"""

import json
import re
from typing import Optional


# ── Formatting constants ────────────────────────────────────────────────
SEPARATOR = "──────────────────────────────────"


def parse_summary_ready(raw_text: str) -> dict:
    """
    Extract the JSON object from a SUMMARY_READY: response.

    The LLM returns something like:
        SUMMARY_READY: {"chief_complaint": "cough", ...}

    This function strips the prefix and parses the JSON.
    """
    # Remove the SUMMARY_READY: prefix (case-insensitive, optional whitespace)
    cleaned = re.sub(r"(?i)^SUMMARY_READY:\s*", "", raw_text.strip())

    # Sometimes the LLM wraps JSON in markdown code fences — strip those too
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: try to find the first { ... } block in the text
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
        # If all parsing fails, return a minimal dict with what we have
        return {"chief_complaint": cleaned, "raw_parse_failed": True}


def generate_clinical_summary(
    parsed_data: dict,
    patient_name: str = "Unknown",
    red_flag: Optional[str] = None,
    source: str = "Text Interview",
) -> tuple[str, dict]:
    """
    Produce the formatted clinical summary and a structured dict.

    Parameters
    ----------
    parsed_data : dict
        The JSON extracted by parse_summary_ready().
    patient_name : str
        Patient's name (passed from the frontend / test script).
    red_flag : str | None
        If a RED_FLAG was triggered during the interview, its text.
    source : str
        "Text Interview" or "Voice Interview" — tagged next to each field.

    Returns
    -------
    (summary_text, summary_dict)
        summary_text  – the formatted string for display
        summary_dict  – structured dict for JSON storage
    """

    # ── Pull fields with safe defaults ──────────────────────────────────
    chief_complaint = parsed_data.get("chief_complaint", "Not reported")
    onset = parsed_data.get("onset", "Not reported")
    duration = parsed_data.get("duration", "Not reported")
    severity = parsed_data.get("severity", "Not reported")
    associated = parsed_data.get("associated_symptoms", "None reported")
    past_history = parsed_data.get("past_medical_history", "None reported")
    medications = parsed_data.get("medications", "None reported")
    allergies = parsed_data.get("allergies", "None reported")

    # Normalise associated_symptoms — LLM might return a list or a string
    if isinstance(associated, list):
        associated_display = ", ".join(associated)
    else:
        associated_display = str(associated)

    # Same for past_history
    if isinstance(past_history, list):
        past_history_display = ", ".join(past_history)
    else:
        past_history_display = str(past_history)

    # Same for medications
    if isinstance(medications, list):
        medications_display = ", ".join(medications)
    else:
        medications_display = str(medications)

    # Source tag shorthand
    tag = f"[{source}]"

    # ── Build the formatted text ────────────────────────────────────────
    lines = []

    # Header
    lines.append(f"PATIENT: {patient_name}")
    lines.append(f"CURRENT COMPLAINT: {chief_complaint} — {duration}")
    lines.append("")
    lines.append(SEPARATOR)

    # Red flag section
    lines.append("")
    if red_flag:
        lines.append("⚠️ RED FLAG")
        lines.append(f"{red_flag}")
        lines.append(f"Source: {source}")
    else:
        lines.append("✅ No red flags detected")
    lines.append("")
    lines.append(SEPARATOR)

    # Current history
    lines.append("")
    lines.append("CURRENT HISTORY")
    lines.append(f"Onset: {onset:<24}{tag}")
    lines.append(f"Duration: {duration:<21}{tag}")
    lines.append(f"Severity: {severity:<20}{tag}")
    lines.append(f"Associated symptoms: {associated_display}  {tag}")
    lines.append("")
    lines.append(SEPARATOR)

    # Previous history
    lines.append("")
    lines.append("PREVIOUS HISTORY")
    lines.append(f"Past medical history: {past_history_display}  {tag}")
    lines.append(f"Medications: {medications_display}  {tag}")
    lines.append(f"Allergies: {allergies}  {tag}")
    lines.append("")
    lines.append(SEPARATOR)

    # Labs — placeholder for future stage
    lines.append("")
    lines.append("LABS")
    lines.append("(Pending — no lab reports uploaded)")
    lines.append("")
    lines.append(SEPARATOR)

    # Needs verification — placeholder for future OCR stage
    lines.append("")
    lines.append("⚠️ NEEDS VERIFICATION")
    lines.append("(No OCR data available yet)")
    lines.append("")
    lines.append(SEPARATOR)

    summary_text = "\n".join(lines)

    # ── Build the structured dict ───────────────────────────────────────
    summary_dict = {
        "patient_name": patient_name,
        "chief_complaint": chief_complaint,
        "onset": onset,
        "duration": duration,
        "severity": severity,
        "associated_symptoms": associated_display,
        "past_medical_history": past_history_display,
        "medications": medications_display,
        "allergies": allergies,
        "red_flag": red_flag,
        "source": source,
        # Placeholder sections for future stages
        "labs": None,
        "ocr_verification": None,
    }

    return summary_text, summary_dict
