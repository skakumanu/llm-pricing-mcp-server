"""Document writer verification (v1.89.0): the never-fabricate guarantee and structural validation.

``verify_prose`` cross-checks every number in model-written text against the provenance manifest and
removes the sentence of any number that cannot be traced; the count of removed numbers is the
``corrections`` figure reported in the response and stored in the manifest. ``validate_document`` is the
structural gate that runs before anything is stored: required sections, speaker notes on every slide,
every number in a table or paragraph traceable, no email-, key- or Stripe-id-shaped strings.
"""
import re
from typing import Any, Dict, Iterable, List, Set, Tuple

from src.services.audit_store import redact_deep
from src.services.docs_catalogue import Manifest, required_section_ids
from src.services.orchestrator_workflows import redact_untrusted_text

MAX_TOKEN_DECIMALS = 6

_ISO_DATETIME = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z?\b")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_VERSION = re.compile(r"\bv?\d+\.\d+\.\d+\b")
_NUMBER = re.compile(
    r"(?<![\w.])(?<![A-Za-z]-)\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?(%|[kKmMbB]\b)?(?!\w)(?!-[A-Za-z])")
_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_PII_SHAPES = re.compile(r"@|\bsk-|\bpk-|\bwhsec_|\bcus_|\bsub_|\bevt_|\blpa_|\bAKIA[A-Z0-9]{8,}")


def scrub(text: str) -> str:
    """Redaction (emails, keys, Stripe ids) plus removal of any remaining '@'."""
    return str(redact_untrusted_text(text)).replace("@", " at ")


def scrub_deep(value: Any) -> Any:
    value = redact_deep(value)
    if isinstance(value, str):
        return value.replace("@", " at ")
    if isinstance(value, dict):
        return {k: scrub_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_deep(v) for v in value]
    return value


def _numbers(text: str) -> List[Tuple[float, int, str]]:
    """[(value, decimals, token)] for every number in ``text`` (suffixes k/M/B and % normalised)."""
    out = []
    for m in _NUMBER.finditer(text):
        whole, frac, suf = m.group(1), m.group(2) or "", m.group(3) or ""
        value = float(whole.replace(",", "") + frac)
        if suf and suf != "%":
            value *= _SUFFIX[suf.lower()]
        out.append((value, max(0, len(frac) - 1), m.group(0).strip()))
    return out


def _strip_structural(text: str, names: Iterable[str]) -> str:
    """Drop identifiers whose digits are not claims: model/provider names, timestamps, clock times."""
    out = _ISO_DATETIME.sub(" ", text)
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        out = re.sub(re.escape(name), " ", out, flags=re.I)
    return _CLOCK.sub(" ", out)


class Allowed:
    """The numbers, versions and dates a text may legitimately contain, derived from a manifest."""

    def __init__(self, manifest: Manifest) -> None:
        self.names = list(manifest.names)
        self.dates: Set[str] = set(manifest.dates)
        self.versions: Set[str] = set()
        self.numbers: List[Tuple[float, int]] = []
        for f in manifest.figures:
            self.numbers.append((float(f.value), 6))
            if f.price_as_of:
                self.dates.add(f.price_as_of[:10])
        version = str(manifest.meta.get("app_version") or "")
        if version:
            self.versions.add(version)
        for text in manifest.source_texts:
            self._absorb(text)

    def _absorb(self, text: str) -> None:
        text = _ISO_DATETIME.sub(" ", text)
        self.dates.update(_ISO_DATE.findall(text))
        for v in _VERSION.findall(text):
            self.versions.add(v.lstrip("v"))
        text = _ISO_DATE.sub(" ", _VERSION.sub(" ", text))
        for value, _d, _t in _numbers(_strip_structural(text, ())):
            self.numbers.append((value, 6))

    def number_ok(self, value: float, decimals: int) -> bool:
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        for known, _ in self.numbers:
            if abs(known - value) <= tol:
                return True
            # a figure shown rounded to the token's precision, or the token shown to more decimals
            # (only for tokens that carry decimals: a bare integer must match a figure exactly, otherwise
            # an invented "3" would trace to any 2.7 or 3.2 in the manifest)
            if decimals > 0 and round(known, decimals) == round(value, decimals):
                return True
        return False


def untraceable(text: str, allowed: Allowed) -> List[str]:
    """Tokens in ``text`` that do not trace to the manifest (empty list = fully traceable)."""
    bad: List[str] = []
    for d in _ISO_DATE.findall(_ISO_DATETIME.sub(" ", text)):
        if d not in allowed.dates:
            bad.append(d)
    body = _ISO_DATE.sub(" ", _ISO_DATETIME.sub(" ", text))
    for v in _VERSION.findall(body):
        if v.lstrip("v") not in allowed.versions:
            bad.append(v)
    body = _VERSION.sub(" ", body)
    for value, decimals, token in _numbers(_strip_structural(body, allowed.names)):
        if not allowed.number_ok(value, decimals):
            bad.append(token)
    return bad


def verify_prose(text: str, manifest: Manifest) -> Tuple[str, int]:
    """Remove every sentence that contains a number absent from the manifest. Returns (clean text, corrections)
    where corrections counts the untraceable numbers removed."""
    allowed = Allowed(manifest)
    kept: List[str] = []
    corrections = 0
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        bad = untraceable(sentence, allowed)
        if bad:
            corrections += len(bad)
        elif sentence.strip():
            kept.append(sentence.strip())
    return " ".join(kept), corrections


def verify_prose_block(prose: Dict[str, Any], manifest: Manifest) -> Tuple[Dict[str, Any], int]:
    """Verify the model's JSON prose ({'summary': str, 'notes': {section_id: str}})."""
    total = 0
    clean: Dict[str, Any] = {"summary": "", "notes": {}}
    if isinstance(prose.get("summary"), str):
        clean["summary"], n = verify_prose(scrub(prose["summary"]), manifest)
        total += n
    notes = prose.get("notes")
    if isinstance(notes, dict):
        for sid, text in notes.items():
            if isinstance(text, str):
                clean["notes"][str(sid)[:60]], n = verify_prose(scrub(text), manifest)
                total += n
    return clean, total


class ValidationError(ValueError):
    """The document failed structural validation; nothing is stored."""


def _texts(doc: Dict[str, Any]) -> Iterable[Tuple[str, str]]:
    yield "title", doc.get("title", "")
    yield "subtitle", doc.get("subtitle", "")
    for s in doc.get("sections", []):
        sid = s.get("id", "?")
        for p in s.get("paragraphs", []):
            yield f"{sid}.paragraph", p
        for b in s.get("bullets", []):
            yield f"{sid}.bullet", b
        yield f"{sid}.notes", s.get("notes", "")
        for t in [s.get("table")] + list(s.get("extra_tables", [])):
            if t:
                for row in t["rows"]:
                    for cell in row:
                        yield f"{sid}.cell", str(cell)
    for f in doc.get("footnotes", []):
        yield "footnote", f
    yield "freshness", doc.get("freshness", "")
    yield "footer", doc.get("footer", "")


def validate_document(doc: Dict[str, Any], manifest: Manifest) -> None:
    """Raise ValidationError unless the document is complete, traceable and free of PII-shaped strings."""
    ids = [s.get("id") for s in doc.get("sections", [])]
    for required in required_section_ids(doc.get("doc_type", "")):
        if required not in ids:
            raise ValidationError(f"missing required section: {required}")
    by_id = {s["id"]: s for s in doc["sections"]}
    summary = by_id.get("executive_summary")
    if not summary or not any(p.strip() for p in summary.get("paragraphs", [])):
        raise ValidationError("executive summary is empty")
    if doc.get("kind") == "deck":
        for s in doc["sections"]:
            if not s.get("notes", "").strip():
                raise ValidationError(f"slide {s['id']} has no speaker notes")
    if doc.get("doc_type") == "release_notes_brief" and not any(i.startswith("release_") for i in ids):
        raise ValidationError("no release section")
    allowed = Allowed(manifest)
    for where, text in _texts(doc):
        if _PII_SHAPES.search(text) or scrub(text) != text:
            raise ValidationError(f"{where}: contains an email-, key- or Stripe-id-shaped string")
        if where.endswith(".notes") or where in ("footer",):
            continue  # speaker notes are verified as prose; the footer carries only the version and date
        bad = untraceable(text, allowed)
        if bad:
            raise ValidationError(f"{where}: number not in the manifest: {bad[0]}")
