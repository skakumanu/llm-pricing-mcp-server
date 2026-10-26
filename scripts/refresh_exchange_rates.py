#!/usr/bin/env python3
"""Refresh the vendored USD exchange-rate table ``data/exchange_rates.json``.

The server never fetches a rate during a request: it converts with this file,
flags it stale once ``as_of`` is more than 7 days old, and keeps serving the
last known rates until a fresher table is merged. This script is that refresh.
It runs weekly from ``.github/workflows/price-audit.yml`` (job
``refresh-exchange-rates``), which opens a PR a human merges; it is also usable
by hand:

    python scripts/refresh_exchange_rates.py                 # report only
    python scripts/refresh_exchange_rates.py --apply         # rewrite the table
    python scripts/refresh_exchange_rates.py --source ecb    # ECB reference rates (re-based to USD)
    python scripts/refresh_exchange_rates.py --from-file payload.json --apply   # offline (tests)

Sources: Frankfurter (``api.frankfurter.dev``, ECB data served as JSON with a
``base`` parameter) first; on failure the ECB daily XML (EUR-based, re-based to
USD here). Validation before anything is written: all 13 supported codes
present, positive and finite, USD == 1.0, ``as_of`` parses and is not in the
future, and no rate moved more than ``--max-move-pct`` against the previous
file unless ``--force``. On ANY failure the reason is printed, the old file is
left untouched, and the exit status is 0 — a failed refresh is a non-event the
next run retries, not something that should fail the audit workflow. A
successful refresh clears ``placeholder``.
"""
import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from typing import Dict, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.services.currency import SUPPORTED_CURRENCIES  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "data", "exchange_rates.json")
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
DEFAULT_MAX_MOVE_PCT = 15.0

SOURCE_LABELS = {
    "frankfurter": "Frankfurter (ECB reference rates, USD base) via scripts/refresh_exchange_rates.py",
    "ecb": "ECB euro foreign exchange reference rates, re-based to USD via scripts/refresh_exchange_rates.py",
}


class RefreshError(Exception):
    """Any reason the table must not be rewritten."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_frankfurter(payload: dict) -> Tuple[date, Dict[str, float]]:
    """``{"base": "USD", "date": "YYYY-MM-DD", "rates": {"EUR": 0.92, ...}}`` -> (as_of, USD->X rates)."""
    if not isinstance(payload, dict):
        raise RefreshError("Frankfurter payload is not an object")
    base = str(payload.get("base", "")).upper()
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        raise RefreshError("Frankfurter payload has no rates")
    try:
        as_of = date.fromisoformat(str(payload.get("date")))
    except (TypeError, ValueError):
        raise RefreshError(f"Frankfurter payload has an unparseable date: {payload.get('date')!r}")
    out = {str(k).upper(): float(v) for k, v in rates.items()}
    if base == "USD":
        out["USD"] = 1.0
        return as_of, out
    if base == "EUR":
        return as_of, rebase_eur_to_usd(out)
    raise RefreshError(f"Frankfurter payload has unexpected base {base!r}")


def parse_ecb_xml(text: str) -> Tuple[date, Dict[str, float]]:
    """ECB ``eurofxref-daily.xml`` (EUR base) -> (as_of, USD->X rates)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RefreshError(f"ECB XML did not parse: {exc}")
    as_of: Optional[date] = None
    eur_rates: Dict[str, float] = {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag != "Cube":
            continue
        if "time" in el.attrib:
            try:
                as_of = date.fromisoformat(el.attrib["time"])
            except ValueError:
                raise RefreshError(f"ECB XML has an unparseable time: {el.attrib['time']!r}")
        if "currency" in el.attrib and "rate" in el.attrib:
            eur_rates[el.attrib["currency"].upper()] = float(el.attrib["rate"])
    if as_of is None or not eur_rates:
        raise RefreshError("ECB XML carried no dated rates")
    return as_of, rebase_eur_to_usd(eur_rates)


def rebase_eur_to_usd(eur_rates: Dict[str, float]) -> Dict[str, float]:
    """EUR->X rates to USD->X: X/USD = (X/EUR) / (USD/EUR); EUR itself = 1 / (USD/EUR)."""
    usd_per_eur = eur_rates.get("USD")
    if not usd_per_eur or usd_per_eur <= 0:
        raise RefreshError("cannot re-base: no USD rate in the EUR table")
    out = {code: value / usd_per_eur for code, value in eur_rates.items() if code != "USD"}
    out["EUR"] = 1.0 / usd_per_eur
    out["USD"] = 1.0
    return out


def detect_and_parse(text: str) -> Tuple[date, Dict[str, float], str]:
    """Parse a saved payload of either kind (``--from-file``)."""
    stripped = text.lstrip()
    if stripped.startswith("<"):
        as_of, rates = parse_ecb_xml(text)
        return as_of, rates, "ecb"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RefreshError(f"payload is neither XML nor JSON: {exc}")
    as_of, rates = parse_frankfurter(payload)
    return as_of, rates, "frankfurter"


# ---------------------------------------------------------------------------
# Fetching (network; never on the request path)
# ---------------------------------------------------------------------------

def fetch(source: str, timeout: float = 30.0) -> Tuple[date, Dict[str, float], str]:
    import httpx  # local import: the offline paths above must not need it

    if source == "frankfurter":
        symbols = ",".join(c for c in SUPPORTED_CURRENCIES if c != "USD")
        resp = httpx.get(FRANKFURTER_URL, params={"base": "USD", "symbols": symbols}, timeout=timeout)
        resp.raise_for_status()
        as_of, rates = parse_frankfurter(resp.json())
        return as_of, rates, "frankfurter"
    if source == "ecb":
        resp = httpx.get(ECB_URL, timeout=timeout)
        resp.raise_for_status()
        as_of, rates = parse_ecb_xml(resp.text)
        return as_of, rates, "ecb"
    raise RefreshError(f"unknown source {source!r}")


def fetch_with_fallback(preferred: str) -> Tuple[date, Dict[str, float], str]:
    order = [preferred] + [s for s in ("frankfurter", "ecb") if s != preferred]
    errors = []
    for source in order:
        try:
            return fetch(source)
        except Exception as exc:  # network, HTTP, parse — try the next source
            errors.append(f"{source}: {type(exc).__name__}: {exc}")
    raise RefreshError("every source failed — " + "; ".join(errors))


# ---------------------------------------------------------------------------
# Validation + writing
# ---------------------------------------------------------------------------

def load_previous(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def validate(
    as_of: date,
    rates: Dict[str, float],
    previous: Optional[dict],
    *,
    max_move_pct: float = DEFAULT_MAX_MOVE_PCT,
    today: Optional[date] = None,
    force: bool = False,
) -> Dict[str, float]:
    """Return the trimmed 13-code table or raise RefreshError with every problem found."""
    today = today or datetime.now(UTC).date()
    problems = []
    if as_of > today:
        problems.append(f"as_of {as_of.isoformat()} is in the future (today {today.isoformat()})")
    trimmed: Dict[str, float] = {}
    for code in SUPPORTED_CURRENCIES:
        value = rates.get(code)
        if value is None:
            problems.append(f"missing rate for {code}")
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            problems.append(f"non-numeric rate for {code}: {value!r}")
            continue
        if not math.isfinite(value) or value <= 0:
            problems.append(f"rate for {code} is not a positive finite number: {value!r}")
            continue
        trimmed[code] = value
    if "USD" in trimmed and abs(trimmed["USD"] - 1.0) > 1e-9:
        problems.append(f"USD must be exactly 1.0, got {trimmed['USD']!r}")
    prev_rates = (previous or {}).get("rates") if isinstance(previous, dict) else None
    # The authored placeholder table was never a market rate, so it must not gate
    # the first real refresh: one hand-guessed value that is >15% off would make
    # the weekly job skip forever. The move check applies from the first real table on.
    prev_is_placeholder = isinstance(previous, dict) and bool(previous.get("placeholder"))
    if isinstance(prev_rates, dict) and not force and not prev_is_placeholder:
        for code, value in trimmed.items():
            old = prev_rates.get(code)
            if not old:
                continue
            try:
                move = abs(value - float(old)) / float(old) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if move > max_move_pct:
                problems.append(
                    f"{code} moved {move:.1f}% ({old} -> {value}), over the {max_move_pct:g}% limit; "
                    "pass --force if this is real"
                )
    if problems:
        raise RefreshError("; ".join(problems))
    return trimmed


def write_table(path: str, as_of: date, rates: Dict[str, float], source_label: str) -> None:
    table = {
        "base": "USD",
        "as_of": as_of.isoformat(),
        "source": source_label,
        "placeholder": False,
        "rates": {code: round(rates[code], 6) for code in SUPPORTED_CURRENCIES},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(table, fh, indent=2)
        fh.write("\n")


def describe(previous: Optional[dict], as_of: date, rates: Dict[str, float], source_label: str) -> str:
    lines = [f"as_of: {as_of.isoformat()}  source: {source_label}"]
    prev_rates = (previous or {}).get("rates", {}) if isinstance(previous, dict) else {}
    prev_as_of = (previous or {}).get("as_of") if isinstance(previous, dict) else None
    if prev_as_of:
        lines.append(f"previous as_of: {prev_as_of}" + (" (placeholder)" if previous.get("placeholder") else ""))
    lines.append("| Code | Previous | New | Move |")
    lines.append("|---|---|---|---|")
    for code in SUPPORTED_CURRENCIES:
        new = rates[code]
        old = prev_rates.get(code)
        if old:
            move = (new - float(old)) / float(old) * 100.0
            lines.append(f"| {code} | {float(old):.6g} | {new:.6g} | {move:+.2f}% |")
        else:
            lines.append(f"| {code} | - | {new:.6g} | new |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="rewrite the table (default: report only)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="table path (default: data/exchange_rates.json)")
    ap.add_argument("--source", choices=("frankfurter", "ecb"), default="frankfurter",
                    help="preferred source; the other is the fallback")
    ap.add_argument("--max-move-pct", type=float, default=DEFAULT_MAX_MOVE_PCT,
                    help="reject a refresh where any rate moved more than this vs the previous file")
    ap.add_argument("--date", default=None, help="treat this ISO date as today (validation of as_of)")
    ap.add_argument("--from-file", default=None,
                    help="parse a saved Frankfurter JSON / ECB XML payload instead of fetching")
    ap.add_argument("--force", action="store_true", help="accept rate moves over --max-move-pct")
    args = ap.parse_args(argv)

    try:
        today = date.fromisoformat(args.date) if args.date else None
    except ValueError:
        print(f"refresh skipped: --date {args.date!r} is not an ISO date")
        return 0

    previous = load_previous(args.out)
    try:
        if args.from_file:
            with open(args.from_file, encoding="utf-8") as fh:
                as_of, rates, source = detect_and_parse(fh.read())
        else:
            as_of, rates, source = fetch_with_fallback(args.source)
        trimmed = validate(as_of, rates, previous, max_move_pct=args.max_move_pct, today=today, force=args.force)
    except RefreshError as exc:
        print(f"refresh skipped: {exc}")
        print("the existing table was left untouched")
        return 0
    except OSError as exc:
        print(f"refresh skipped: {exc}")
        print("the existing table was left untouched")
        return 0

    label = SOURCE_LABELS[source]
    print(describe(previous, as_of, trimmed, label))
    if args.apply:
        write_table(args.out, as_of, trimmed, label)
        print(f"wrote {os.path.relpath(args.out, ROOT) if args.out.startswith(ROOT) else args.out}")
    else:
        print("report only — pass --apply to write the table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
