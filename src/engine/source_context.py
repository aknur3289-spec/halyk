"""Load the narrow, source-grounded context required by ledger derivation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.models import SourceEvidence

from .ledger_adapter import DocumentedLedgerInput, LedgerDerivationContext


_INACTIVE_MARKERS = (
    "не является окончательной",
    "рабочий документ",
    "заменена окончательным",
    "недействующая редакция",
    "not final",
    "draft",
    "working paper",
    "superseded",
)


@dataclass(frozen=True, slots=True)
class _CovenantRequirements:
    """Canonical document-context requirements of a scenario's covenants.

    This is intentionally an internal semantic interface: the loader decides
    whether a document input may be linked from the extracted calculation
    definition first, and only then considers its quoted source text.
    """

    adjusted_ebitda: bool = False
    employee_obligation: bool = False
    group_capex: bool = False


def build_ledger_derivation_context(
    parsed_documents_path: Path,
    covenant_rows: Sequence[Mapping[str, Any]],
    stage2_rows: Sequence[Mapping[str, Any]] = (),
) -> LedgerDerivationContext:
    """Build context only from exact KYC tables and final-auditor disclosures.

    A document is associated through its explicit account number, never a
    borrower-name substring.  Missing or incomplete source statements produce
    no value: the adapter will surface the covenant as unevaluable.
    """

    documents = json.loads(parsed_documents_path.read_text(encoding="utf-8"))
    if not isinstance(documents, list):
        raise ValueError("parsed documents must be a JSON array")

    scenario_by_account = {
        str(row["account_id"]): str(row["scenario_id"])
        for row in covenant_rows
        if row.get("account_id") and row.get("scenario_id")
    }
    related_parties: dict[str, tuple[str, ...]] = {}
    documented: dict[tuple[str, str], DocumentedLedgerInput] = {}
    covenant_text_by_scenario = _covenant_text_by_scenario(covenant_rows, documents)
    requirements_by_scenario = _covenant_requirements_by_scenario(
        covenant_rows, covenant_text_by_scenario
    )

    for document in documents:
        filename = str(document.get("filename", ""))
        pages = document.get("pages", [])
        if not filename or not isinstance(pages, list):
            continue
        page_text = {int(page["page"]): str(page.get("text") or "") for page in pages if page.get("page")}
        text = "\n".join(page_text.values())
        account_match = re.search(
            r"(?:Сч[её]т\s*|№\s*|Account\s*:?[ \t]*)(ACC-[A-Z0-9-]+)",
            text,
            flags=re.IGNORECASE,
        )
        if account_match is None:
            continue
        scenario_id = scenario_by_account.get(account_match.group(1).upper())
        if scenario_id is None:
            continue

        if "досье «знай своего клиента»" in text.casefold():
            parties = _parse_kyc_related_parties(text)
            if parties:
                related_parties[scenario_id] = tuple(parties)
            unrestricted = _parse_unrestricted_subsidiaries(text)
            if unrestricted:
                related_parties[f"{scenario_id}:unrestricted_subsidiaries"] = tuple(unrestricted)

        if not _is_final_auditor_document(text):
            continue
        requirements = requirements_by_scenario.get(scenario_id, _CovenantRequirements())
        if requirements.employee_obligation:
            item = _parse_p8_employee_obligation(filename, page_text)
            if item is not None:
                documented[(scenario_id, "final_auditor_employee_obligation")] = item
        if requirements.adjusted_ebitda:
            item = _parse_accepted_auditor_addbacks(filename, page_text)
            if item is not None:
                documented[(scenario_id, "accepted_auditor_addbacks")] = item

    borrowers_by_account = _borrowers_by_account(stage2_rows)
    for scenario_id, requirements in requirements_by_scenario.items():
        if not requirements.group_capex:
            continue
        borrower = _borrower_for_scenario(covenant_rows, scenario_id, borrowers_by_account)
        if borrower is None:
            continue
        group_capex = _parse_group_capex(documents, borrower)
        if group_capex is not None:
            documented[(scenario_id, "group_capital_expenditure")] = group_capex

    return LedgerDerivationContext(related_parties=related_parties, documented_inputs=documented)


def _covenant_requirements_by_scenario(
    covenant_rows: Sequence[Mapping[str, Any]], covenant_text_by_scenario: Mapping[str, str]
) -> dict[str, _CovenantRequirements]:
    """Canonicalize explicit calculation requirements before linking sources.

    Metric and ratio components are the primary interface.  Text markers are
    used only when they explicitly describe the same semantic calculation,
    allowing private English and Russian agreements to reach one canonical
    representation without treating plain EBITDA as adjusted EBITDA.
    """

    result: dict[str, _CovenantRequirements] = {}
    for row in covenant_rows:
        scenario_id = row.get("scenario_id")
        covenant = row.get("covenant")
        if scenario_id is None or not isinstance(covenant, Mapping):
            continue
        scenario = str(scenario_id)
        text = covenant_text_by_scenario.get(scenario, "")
        previous = result.get(scenario, _CovenantRequirements())
        labels = (
            str(covenant.get("metric") or ""),
            str(covenant.get("ratio_numerator") or ""),
            str(covenant.get("ratio_denominator") or ""),
        )
        result[scenario] = _CovenantRequirements(
            adjusted_ebitda=previous.adjusted_ebitda
            or _labels_require_adjusted_ebitda(labels)
            or _source_explicitly_requires_adjusted_ebitda(text),
            employee_obligation=previous.employee_obligation or _requires_employee_obligation(text),
            group_capex=previous.group_capex or _requires_group_capex(text),
        )
    return result


def _covenant_text_by_scenario(
    covenant_rows: Sequence[Mapping[str, Any]], documents: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    """Return active covenant text keyed by its scenario without name matching."""

    by_filename = {str(document.get("filename")): document for document in documents}
    result: dict[str, str] = {}
    for row in covenant_rows:
        scenario_id = row.get("scenario_id")
        covenant = row.get("covenant", {})
        document = by_filename.get(str(row.get("filename", "")), {})
        evidence = covenant.get("evidence", {}) if isinstance(covenant, Mapping) else {}
        page_number = evidence.get("page") if isinstance(evidence, Mapping) else None
        page_text = ""
        for page in document.get("pages", []) if isinstance(document, Mapping) else []:
            if page.get("page") == page_number:
                page_text = str(page.get("text") or "")
                break
        if scenario_id is not None:
            quote = str(evidence.get("quote") or "") if isinstance(evidence, Mapping) else ""
            result[str(scenario_id)] = result.get(str(scenario_id), "") + "\n" + page_text + "\n" + quote
    return result


def _borrowers_by_account(stage2_rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in stage2_rows:
        account = row.get("account_id")
        borrower = row.get("borrower_name")
        if not isinstance(account, str) or not isinstance(borrower, str):
            continue
        # A bank header is not the borrower; retain only legal entities found
        # by Stage 2 in a document associated with this account.
        if borrower.strip() and "bank of" not in borrower.casefold():
            result.setdefault(account.upper(), borrower.strip())
    return result


def _borrower_for_scenario(
    covenant_rows: Sequence[Mapping[str, Any]], scenario_id: str, borrowers_by_account: Mapping[str, str]
) -> str | None:
    """Use the Stage-2 exact legal entity when it has been propagated to a row."""

    for row in covenant_rows:
        if str(row.get("scenario_id")) == scenario_id:
            account = row.get("account_id")
            if isinstance(account, str) and account.upper() in borrowers_by_account:
                return borrowers_by_account[account.upper()]
    return None


def _requires_employee_obligation(text: str) -> bool:
    compact = text.casefold()
    return ("personnel" in compact or "персонал" in compact) and (
        "обязатель" in compact or "obligation" in compact
    )


def _labels_require_adjusted_ebitda(labels: Sequence[str]) -> bool:
    """Recognize canonical calculations that explicitly consume audit add-backs."""

    canonical_labels = {
        re.sub(r"[\s_-]+", "_", label.casefold()).strip("_") for label in labels
    }
    return bool(
        canonical_labels
        & {
            "adjusted_ebitda",
            "accepted_auditor_addbacks",
            "accepted_auditor_adjustments",
            "auditor_accepted_addbacks",
        }
    )


def _source_explicitly_requires_adjusted_ebitda(text: str) -> bool:
    """Derive adjusted EBITDA semantics from an explicit covenant definition.

    The output of this parser is consumed only through ``_CovenantRequirements``.
    It therefore cannot make an auditor add-back apply merely because a
    covenant happens to mention EBITDA.
    """

    compact = " ".join(text.casefold().split())
    has_ebitda = "ebitda" in compact
    if "adjusted ebitda" in compact or "скорректированная ebitda" in compact:
        return True
    has_adjustment = any(
        marker in compact
        for marker in (
            "adjusted",
            "adjustment",
            "add-back",
            "add back",
            "скорректирован",
            "корректировк",
            "обратному добавлению",
        )
    )
    has_auditor_acceptance = any(
        marker in compact
        for marker in ("auditor", "audit", "аудитор", "аудит")
    )
    return has_ebitda and has_adjustment and has_auditor_acceptance


def _requires_group_capex(text: str) -> bool:
    compact = text.casefold()
    return ("group" in compact or "групп" in compact) and (
        "capex" in compact or "capital expenditure" in compact or "капитальн" in compact
    )


def _parse_kyc_related_parties(text: str) -> list[str]:
    """Parse only the KYC ownership table plus its explicit threshold rule."""

    match = re.search(
        r"(\d+(?:\.\d+)?)%\s+и\s+более\s+голосующих\s+прав\s*,?\s*признаются\s+связанными",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return []
    threshold = float(match.group(1))
    table = text[: match.start()]
    parties: list[str] = []
    for name, ownership in re.findall(r"(?m)^(.+?)\s+(\d+(?:\.\d+)?)%\s*$", table):
        if float(ownership) >= threshold:
            parties.append(name.strip())
    return parties


def _is_final_auditor_document(text: str) -> bool:
    compact = text.casefold()
    is_auditor_document = "аудиторское дело" in compact or "final auditor report" in compact
    return is_auditor_document and not any(marker in compact for marker in _INACTIVE_MARKERS)


def _parse_unrestricted_subsidiaries(text: str) -> list[str]:
    """Accept only explicit KYC status entries, never ownership as a proxy."""

    match = re.search(
        r"неограниченн(?:ая|ые)\s+дочерн\w*\s+(?:организац\w*|компан\w*)\s*:\s*([^\n]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return []
    return [name.strip() for name in match.group(1).split(",") if name.strip()]


def _parse_accepted_auditor_addbacks(
    filename: str, page_text: Mapping[int, str]
) -> DocumentedLedgerInput | None:
    for page, text in page_text.items():
        match = re.search(
            r"(?:признан\w*\s+аудитор\w*\s+подлежащ\w*\s+обратному добавлению|"
            r"принят\w*\s+аудитор\w*\s+к обратному добавлению|"
            r"auditor[- ]approved\s+add[- ]backs?|"
            r"accepted\s+audit\s+adjustments?)[^$]{0,160}\$([\d,]+(?:\.\d+)?)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return DocumentedLedgerInput(
                value=float(match.group(1).replace(",", "")),
                evidence=(SourceEvidence(document_id=filename, page=page, quote=match.group(0)),),
            )
    return None


def _parse_p8_employee_obligation(
    filename: str, page_text: Mapping[int, str]
) -> DocumentedLedgerInput | None:
    for page, text in page_text.items():
        match = re.search(
            r"совокупное обязательство по программе\s+выходных пособий\s+в размере\s+\$([\d,]+(?:\.\d+)?)"
            r"\s+раскрывается и не отражается отдельной операцией",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            quote = match.group(0)
            return DocumentedLedgerInput(
                value=float(match.group(1).replace(",", "")),
                evidence=(SourceEvidence(document_id=filename, page=page, quote=quote),),
            )
    return None


def _parse_group_capex(
    documents: Sequence[Mapping[str, Any]], borrower: str
) -> DocumentedLedgerInput | None:
    """Derive group capex only when the covenant explicitly permits group scope."""

    for document in documents:
        filename = str(document.get("filename", ""))
        pages = document.get("pages", [])
        if not isinstance(pages, list):
            continue
        joined = "\n".join(str(page.get("text") or "") for page in pages)
        if not (
            "consolidated annual report" in joined.casefold()
            and re.search(re.escape(" ".join(borrower.split())).replace(r"\ ", r"\s+"), joined)
            and re.search(r"financial results are\s+consolidated within the Group statements", joined)
        ):
            continue
        # The covenant has already established the group-scope exception.
        # Require the audited report's no-disposals statement before using its
        # PPE roll-forward.
        no_disposals_page = next(
            (page for page in pages if "There were no disposals of property" in str(page.get("text") or "")),
            None,
        )
        values_page = next(
            (page for page in pages if "Net book value at the beginning" in str(page.get("text") or "")),
            None,
        )
        if no_disposals_page is None or values_page is None:
            continue
        values_text = str(values_page.get("text") or "")
        opening = _dollar_value("Net book value at the beginning of the year", values_text)
        depreciation = _dollar_value("Depreciation charge for the year", values_text)
        closing = _dollar_value("Net book value at the end of the year", values_text)
        if opening is None or depreciation is None or closing is None:
            continue
        quote = (
            "Net book value at the beginning of the year "
            f"${opening:,.2f}\nDepreciation charge for the year ${depreciation:,.2f}\n"
            f"Net book value at the end of the year ${closing:,.2f}"
        )
        return DocumentedLedgerInput(
            value=round(closing - opening + depreciation, 2),
            evidence=(
                SourceEvidence(
                    document_id=filename,
                    page=int(no_disposals_page["page"]),
                    quote="There were no disposals of property, plant and equipment during the year.",
                ),
                SourceEvidence(document_id=filename, page=int(values_page["page"]), quote=quote),
            ),
        )
    return None


def _dollar_value(label: str, text: str) -> float | None:
    match = re.search(re.escape(label) + r"\s+\$([\d,]+(?:\.\d+)?)", text)
    return float(match.group(1).replace(",", "")) if match else None
