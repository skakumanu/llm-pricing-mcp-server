"""Compliance audit check catalogue (v1.88.0).

Pure, mechanical checks: no LLM, no writes, no network of their own. Everything that touches the
outside world is injected through ``AuditContext`` (``stripe_list_subscriptions``,
``stripe_list_webhook_endpoints``, ``load_data_quality``), so every check is testable with fixtures
and the module can be audited for "reads only" by grep (tests/test_audit_service.py does).

A check function returns ``(status, evidence)``. ``run_all`` wraps it with the fixed id, title,
severity and launch-checklist ids from ``CHECK_SPECS`` and turns an exception into an ``n/a`` row
(``error:<ExceptionType>``) so a broken check can never park the run, and a pass never means
"did not check": every skipped check is shown as ``n/a`` with the reason.

Evidence rules (data minimisation): counts, file names, setting NAMES, opaque 8-character
prefixes. Never values, emails, keys, fingerprints or Stripe ids. The store redacts again.
"""
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

import aiosqlite

from src.services import launch_checklist, legal_pages
from src.services.tiers import checkout_disabled_reason, checkout_enabled

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATUSES = ("pass", "fail", "warn", "n/a")
SEVERITIES = ("critical", "high", "medium", "low", "info")

# The seven event types the Stripe webhook endpoint must be subscribed to (LC-06).
WEBHOOK_EVENTS = (
    "checkout.session.completed", "customer.subscription.updated", "customer.subscription.deleted",
    "invoice.paid", "invoice.payment_failed", "charge.refunded", "charge.dispute.created",
)
# Names of production secrets whose PRESENCE is audited (never their values). MCP_API_KEY is the
# one whose absence is a failure; the rest are expected to be unset before launch (warn).
SECRET_NAMES = (
    "MCP_API_KEY", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_ID_PRO", "ORCHESTRATOR_INTAKE_KEY",
    "PROVIDER_KEY_ENCRYPTION_KEY", "LITESTREAM_REPLICA_URL", "LITESTREAM_ACCESS_KEY_ID",
    "LITESTREAM_SECRET_ACCESS_KEY", "STRIPE_AUDIT_KEY",
)
VOLUME = "/app/data"
WEEKLY_AUDIT_MAX_AGE_DAYS = 8
CONFIRMED_PCT_DROP = 0.1

_KEY_PLACEHOLDER_PREFIX = "hashed:"  # mirrors billing_service._KEY_PLACEHOLDER_PREFIX (pinned by test)
_LIVE_STRIPE = frozenset({"active", "trialing", "past_due", "unpaid"})
_ENTITLED_STRIPE = frozenset({"active", "trialing", "past_due", "unpaid"})
_PAID_STRIPE = frozenset({"active", "trialing"})
_PERMISSION_ERRORS = frozenset({"PermissionError", "AuthenticationError"})
_MAX_LIST = 10  # evidence lists are bounded

StripeLister = Callable[[str], Awaitable[List[Dict[str, Any]]]]
DataQualityLoader = Callable[[], Awaitable[Dict[str, Any]]]


class NotApplicable(Exception):
    """Raised inside a check: it cannot run here. Becomes an ``n/a`` row with ``reason``."""

    def __init__(self, reason: str, **evidence: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence


@dataclass
class AuditContext:
    settings: Any
    now: float
    orchestrator: Optional[Any] = None
    billing_db_path: Optional[str] = None
    repo_checks: Optional[Dict[str, str]] = None
    repo_version: Optional[str] = None
    repo_sha: Optional[str] = None
    stripe_list_subscriptions: Optional[StripeLister] = None
    stripe_list_webhook_endpoints: Optional[StripeLister] = None
    load_data_quality: Optional[DataQualityLoader] = None
    previous_numbers: Optional[Dict[str, Any]] = None
    litestream_config_path: Path = Path("/etc/litestream.yml")
    legal_docs_dir: Optional[Path] = None
    checklist_path: Path = launch_checklist.CHECKLIST
    whats_new_path: Path = PROJECT_ROOT / "static" / "whats-new" / "index.html"
    disabled: frozenset = frozenset()
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    # filled by the checks; persisted by the service (the FR4 time series)
    numbers: Dict[str, Any] = field(default_factory=dict)
    _memo: Dict[str, Any] = field(default_factory=dict)


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else (value or ""))


def _present(value: Any) -> bool:
    return value is not None and _secret(value).strip() != ""


def _prefix(value: Any) -> str:
    return str(value or "")[:8]


# ---------------------------------------------------------------------------
# legal
# ---------------------------------------------------------------------------

async def check_legal_placeholders(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    found = legal_pages.scan_placeholders(ctx.legal_docs_dir)
    live = bool(getattr(ctx.settings, "legal_pages_live", False))
    evidence = {"files": {name: list(tokens)[:_MAX_LIST] for name, tokens in sorted(found.items())},
                "file_count": len(found), "legal_pages_live": live}
    if not found:
        return "pass", evidence
    # Placeholders are expected until launch: a fail only once the pages are claimed in force.
    return ("fail" if live else "warn"), evidence


async def check_legal_live_gate(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    live_flag = bool(getattr(ctx.settings, "legal_pages_live", False))
    remaining = legal_pages.scan_placeholders(ctx.legal_docs_dir)
    enabled = checkout_enabled(ctx.settings)
    evidence = {"legal_pages_live": live_flag, "placeholder_files": len(remaining),
                "checkout_enabled": enabled, "checkout_disabled_reason": checkout_disabled_reason(ctx.settings)}
    if live_flag and remaining:
        evidence["problem"] = "LEGAL_PAGES_LIVE is on while placeholders remain; pages stay draft"
        return "fail", evidence
    if enabled and (remaining or not live_flag):
        evidence["problem"] = "checkout is enabled although the legal pages are not in force"
        return "fail", evidence
    return "pass", evidence


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------

def _db_path_settings(settings: Any) -> Dict[str, str]:
    """Runtime values of every ``*_db_path`` setting that is a real path under the volume."""
    from src.config.settings import Settings
    out: Dict[str, str] = {}
    for name in sorted(Settings.model_fields):
        if not name.endswith("_db_path"):
            continue
        value = getattr(settings, name, None)
        if isinstance(value, str) and value.startswith(VOLUME + "/"):
            out[name] = value
    return out


async def check_litestream_config(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    path = Path(ctx.litestream_config_path)
    if not path.exists():
        raise NotApplicable("litestream config not present (local run)")
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    listed = {str(entry.get("path")) for entry in data.get("dbs", []) if isinstance(entry, dict)}
    runtime = _db_path_settings(ctx.settings)
    if not runtime:
        raise NotApplicable("no *_db_path setting points under /app/data (local run)", listed=len(listed))
    missing = sorted(name for name, value in runtime.items() if value not in listed)
    replica_set = _present(ctx.env.get("LITESTREAM_REPLICA_URL"))
    evidence = {"databases_checked": len(runtime), "listed_paths": len(listed), "missing_settings": missing,
                "LITESTREAM_REPLICA_URL": "set" if replica_set else "unset"}
    if missing:
        return "fail", evidence
    return ("pass" if replica_set else "warn"), evidence


# ---------------------------------------------------------------------------
# secrets and release
# ---------------------------------------------------------------------------

async def check_secrets_presence(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    states: Dict[str, str] = {}
    for name in SECRET_NAMES:
        value = ctx.env.get(name)
        if not _present(value):
            value = getattr(ctx.settings, name.lower(), None)
        states[name] = "set" if _present(value) else "unset"
    evidence = {"secrets": states, "unset_count": sum(1 for v in states.values() if v == "unset")}
    if states["MCP_API_KEY"] == "unset":
        return "fail", evidence
    return ("warn" if evidence["unset_count"] else "pass"), evidence


_BADGE_RE = re.compile(r'<span class="version-badge">v(\d+\.\d+\.\d+)</span>')


async def check_version_consistency(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    import src
    versions: Dict[str, str] = {"src.__version__": src.__version__}
    app_version = getattr(ctx.settings, "app_version", None)
    if app_version:
        versions["settings.app_version"] = str(app_version)
    try:
        import mcp
        versions["mcp.__version__"] = mcp.__version__
    except Exception:  # nosec B110
        pass
    try:
        import src.main as main_module
        versions["mcp serverInfo"] = main_module._MCP_SERVER_VERSION
    except Exception:  # nosec B110
        pass
    top = None
    try:
        match = _BADGE_RE.search(Path(ctx.whats_new_path).read_text(encoding="utf-8"))
        top = match.group(1) if match else None
    except OSError:
        pass
    if top:
        versions["whats-new top entry"] = top
    return ("pass" if len(set(versions.values())) == 1 else "fail"), {"versions": versions}


def _semver(value: str) -> Tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", value)[:3])


async def check_deployed_matches_repo(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    if not ctx.repo_version:
        raise NotApplicable("repo version not supplied (run via compliance-audit.yml)")
    import src
    deployed, repo = src.__version__, ctx.repo_version
    evidence = {"deployed": deployed, "repo": repo}
    try:
        a, b = _semver(deployed), _semver(repo)
    except ValueError:
        raise NotApplicable("repo version not comparable", deployed=deployed)
    if a == b:
        return "pass", evidence
    if a < b:
        evidence["note"] = "the repository is ahead of the deployment: a promotion is pending"
        return "warn", evidence
    evidence["note"] = "the deployment is newer than the repository"
    return "fail", evidence


# ---------------------------------------------------------------------------
# data quality
# ---------------------------------------------------------------------------

_NUMBER_KEYS = ("confirmed_pct", "withheld_for_drift", "never_priced", "stale_models", "stale_pct", "total_models")


async def _data_quality(ctx: AuditContext) -> Dict[str, Any]:
    if "dq" in ctx._memo:
        return ctx._memo["dq"]
    if ctx.load_data_quality is None:
        raise NotApplicable("data-quality source not supplied")
    report = await ctx.load_data_quality()
    if not isinstance(report, dict) or report.get("success") is False:
        raise NotApplicable("data quality unavailable")
    numbers = {k: report.get(k) for k in _NUMBER_KEYS if report.get(k) is not None}
    ctx.numbers.update(numbers)
    ctx._memo["dq"] = numbers
    return numbers


async def check_data_quality_regression(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    current = await _data_quality(ctx)
    prev = ctx.previous_numbers
    if not prev:
        raise NotApplicable("baseline recorded", current=current)
    problems: List[str] = []
    drop = float(prev.get("confirmed_pct", 0.0)) - float(current.get("confirmed_pct", 0.0))
    if round(drop, 6) >= CONFIRMED_PCT_DROP:
        problems.append("confirmed_pct dropped")
    for key in ("withheld_for_drift", "never_priced"):
        if int(current.get(key, 0)) > int(prev.get(key, 0)):
            problems.append(f"{key} rose")
    evidence = {"current": current, "previous": {k: prev.get(k) for k in _NUMBER_KEYS if k in prev},
                "problems": problems}
    return ("fail" if problems else "pass"), evidence


async def check_stale_prices(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    current = await _data_quality(ctx)
    threshold = float(getattr(ctx.settings, "audit_stale_price_pct_warn", 10.0))
    pct = float(current.get("stale_pct", 0.0))
    evidence = {"stale_models": current.get("stale_models"), "stale_pct": pct, "threshold_pct": threshold}
    return ("warn" if pct > threshold else "pass"), evidence


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------

def _orchestrator(ctx: AuditContext) -> Any:
    if ctx.orchestrator is None:
        raise NotApplicable("orchestrator not available")
    return ctx.orchestrator


async def check_weekly_audit_ran(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    orch = _orchestrator(ctx)
    runs = await orch.store.list_runs(workflow_id="weekly_price_audit", limit=1)
    if not runs:
        raise NotApplicable("no record yet")
    run = runs[0]
    age_days = round((ctx.now - float(run["created_at"])) / 86400, 1)
    evidence = {"run_id": run["run_id"], "run_status": run["status"], "age_days": age_days}
    if run["status"] == "parked":
        evidence["parked_reason"] = str(run.get("parked_reason") or "")[:200]
        return "fail", evidence
    return ("pass" if age_days <= WEEKLY_AUDIT_MAX_AGE_DAYS else "warn"), evidence


async def check_budget_vs_spend(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    view = await _orchestrator(ctx).budget_view()
    g = view.get("global", {})
    exhausted_wf = sorted(w["workflow_id"] for w in view.get("workflows", []) if w.get("exhausted"))
    evidence = {"period": view.get("period"), "global_pct": g.get("pct"), "global_exhausted": bool(g.get("exhausted")),
                "soft_alert_triggered": bool(g.get("soft_alert_triggered")), "exhausted_workflows": exhausted_wf}
    if g.get("exhausted"):
        return "fail", evidence
    if g.get("soft_alert_triggered") or exhausted_wf:
        return "warn", evidence
    return "pass", evidence


async def check_open_escalations_age(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    orch = _orchestrator(ctx)
    max_days = int(getattr(ctx.settings, "audit_escalation_max_age_days", 7))
    waiting = [a for a in await orch.store.list_approvals(status="waiting") if a.get("kind") == "owner_handoff"]
    old = [a for a in waiting if (ctx.now - float(a["requested_at"])) / 86400 > max_days]
    evidence = {"open_escalations": len(waiting), "older_than_days": max_days, "overdue": len(old),
                "oldest_age_days": round(max(((ctx.now - float(a["requested_at"])) / 86400 for a in old),
                                             default=0.0), 1),
                "run_ids": sorted(a["run_id"] for a in old)[:_MAX_LIST]}
    return ("warn" if old else "pass"), evidence


# ---------------------------------------------------------------------------
# billing (read-only)
# ---------------------------------------------------------------------------

def _billing_uri(path: str) -> str:
    return f"file:{quote(str(path))}?mode=ro"


async def _open_billing_ro(ctx: AuditContext) -> aiosqlite.Connection:
    """The billing database, opened READ-ONLY (mode=ro plus query_only). Never writable."""
    path = ctx.billing_db_path
    if not path or not Path(path).exists():
        raise NotApplicable("billing database not found")
    db = await aiosqlite.connect(_billing_uri(path), uri=True)
    await db.execute("PRAGMA query_only = ON")
    return db


async def check_key_hash_migration(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    db = await _open_billing_ro(ctx)
    try:
        async with db.execute(
            "SELECT COUNT(*) FROM customers WHERE api_key_hash IS NULL OR api_key NOT LIKE ?",
            (_KEY_PLACEHOLDER_PREFIX + "%",),
        ) as cur:
            unhashed = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM customers") as cur:
            total = (await cur.fetchone())[0]
    finally:
        await db.close()
    return ("fail" if unhashed else "pass"), {"unhashed": unhashed, "customers": total}


async def check_deleted_markers(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    db = await _open_billing_ro(ctx)
    try:
        async with db.execute("SELECT COUNT(*) FROM deleted_accounts") as cur:
            markers = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM deleted_accounts WHERE email_hash IS NULL OR email_hash = '' OR deleted_at IS NULL"
        ) as cur:
            malformed = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM billing_meta WHERE key = 'suppression_salt' AND value != ''") as cur:
            salt = (await cur.fetchone())[0]
    finally:
        await db.close()
    evidence = {"markers": markers, "malformed": malformed, "salt_present": bool(salt)}
    # The salt is created on the first deletion, so its absence only matters once markers exist.
    return ("fail" if malformed or (markers and not salt) else "pass"), evidence


# ---------------------------------------------------------------------------
# stripe (restricted read-only key)
# ---------------------------------------------------------------------------

def _stripe_key(ctx: AuditContext) -> str:
    key = _secret(getattr(ctx.settings, "stripe_audit_key", None)).strip()
    if not key:
        raise NotApplicable("not configured", setting="STRIPE_AUDIT_KEY")
    return key


def _permission_denied(exc: BaseException) -> bool:
    return type(exc).__name__ in _PERMISSION_ERRORS


async def _local_customers(ctx: AuditContext) -> List[Dict[str, Any]]:
    db = await _open_billing_ro(ctx)
    try:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, stripe_customer_id, stripe_subscription_id, tier, billing_status "
                              "FROM customers") as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def _stripe_state(ctx: AuditContext) -> Dict[str, Any]:
    """Subscriptions from Stripe joined with the local customers. Cached per run."""
    if "stripe" in ctx._memo:
        cached = ctx._memo["stripe"]
        if isinstance(cached, BaseException):
            raise cached
        return cached
    try:
        key = _stripe_key(ctx)
        if ctx.stripe_list_subscriptions is None:
            raise NotApplicable("stripe client not supplied")
        try:
            subs = await ctx.stripe_list_subscriptions(key)
        except Exception as exc:
            if _permission_denied(exc):
                raise NotApplicable("key lacks permission", needs="Subscriptions:read")
            raise
        customers = await _local_customers(ctx)
    except Exception as exc:  # one failure answers every stripe.* check; Stripe is not asked five times
        ctx._memo["stripe"] = exc
        raise
    state = {"subs": [s for s in subs if isinstance(s, dict)], "customers": customers}
    ctx._memo["stripe"] = state
    return state


def _price_tiers(settings: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for tier in ("pro", "enterprise"):
        price = getattr(settings, f"stripe_price_id_{tier}", None)
        if price:
            out[str(price)] = tier
    return out


def _match_subscription(customer: Dict[str, Any], by_id: Dict[str, Dict[str, Any]],
                        by_customer: Dict[str, List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    sub_id = customer.get("stripe_subscription_id")
    if sub_id and sub_id in by_id:
        return by_id[sub_id]
    candidates = by_customer.get(customer.get("stripe_customer_id") or "", [])
    for s in candidates:
        if s.get("status") in _LIVE_STRIPE:
            return s
    return candidates[0] if candidates else None


def _index(subs: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    by_id = {s["id"]: s for s in subs if s.get("id")}
    by_customer: Dict[str, List[Dict[str, Any]]] = {}
    for s in subs:
        if s.get("customer"):
            by_customer.setdefault(s["customer"], []).append(s)
    return by_id, by_customer


async def check_stripe_reconciliation(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    state = await _stripe_state(ctx)
    return "pass", {"stripe_subscriptions": len(state["subs"]), "local_customers": len(state["customers"])}


async def check_stripe_orphans(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    state = await _stripe_state(ctx)
    local_subs = {c["stripe_subscription_id"] for c in state["customers"] if c.get("stripe_subscription_id")}
    local_cust = {c["stripe_customer_id"] for c in state["customers"] if c.get("stripe_customer_id")}
    orphans = [s for s in state["subs"] if s.get("status") in _LIVE_STRIPE
               and s.get("id") not in local_subs and s.get("customer") not in local_cust]
    return ("fail" if orphans else "pass"), {"orphan_subscriptions": len(orphans)}


async def check_stripe_tier_mismatch(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    state = await _stripe_state(ctx)
    by_id, by_customer = _index(state["subs"])
    prices = _price_tiers(ctx.settings)
    mismatched: List[str] = []
    for c in state["customers"]:
        sub = _match_subscription(c, by_id, by_customer)
        tier = c.get("tier") or "free"
        status = (sub or {}).get("status")
        if tier != "free":
            bad = sub is None or status not in _ENTITLED_STRIPE
            if not bad and status in _PAID_STRIPE:
                expected = prices.get(str((sub or {}).get("price_id") or ""))
                bad = expected is not None and expected != tier
        else:
            bad = status in _PAID_STRIPE  # paying in Stripe, but free locally
        if bad:
            mismatched.append(_prefix(c["id"]))
    return ("fail" if mismatched else "pass"), {"tier_mismatches": len(mismatched),
                                                 "customers": sorted(mismatched)[:_MAX_LIST]}


async def check_stripe_past_due_mismatch(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    state = await _stripe_state(ctx)
    by_id, by_customer = _index(state["subs"])
    stripe_past_due_local_active: List[str] = []
    local_past_due_stripe_active: List[str] = []
    for c in state["customers"]:
        sub = _match_subscription(c, by_id, by_customer)
        if sub is None:
            continue
        local = c.get("billing_status") or "active"
        if sub.get("status") in ("past_due", "unpaid") and local == "active":
            stripe_past_due_local_active.append(_prefix(c["id"]))
        elif sub.get("status") == "active" and local == "past_due":
            local_past_due_stripe_active.append(_prefix(c["id"]))
    evidence = {"stripe_past_due_local_active": len(stripe_past_due_local_active),
                "local_past_due_stripe_active": len(local_past_due_stripe_active),
                "customers": sorted(stripe_past_due_local_active + local_past_due_stripe_active)[:_MAX_LIST]}
    if stripe_past_due_local_active:
        return "fail", evidence
    return ("warn" if local_past_due_stripe_active else "pass"), evidence


async def check_stripe_shared_ids(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    state = await _stripe_state(ctx)
    shared = 0
    for field_name in ("stripe_customer_id", "stripe_subscription_id"):
        seen: Dict[str, int] = {}
        for c in state["customers"]:
            value = c.get(field_name)
            if value:
                seen[value] = seen.get(value, 0) + 1
        shared += sum(1 for n in seen.values() if n > 1)
    return ("fail" if shared else "pass"), {"shared_ids": shared}


async def check_stripe_webhook_endpoint(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
    key = _stripe_key(ctx)
    if ctx.stripe_list_webhook_endpoints is None:
        raise NotApplicable("stripe client not supplied")
    try:
        endpoints = await ctx.stripe_list_webhook_endpoints(key)
    except Exception as exc:
        if _permission_denied(exc):
            raise NotApplicable("key lacks permission", needs="Webhook Endpoints:read")
        raise
    enabled = [e for e in endpoints if isinstance(e, dict) and e.get("status", "enabled") == "enabled"]
    best_missing: Optional[List[str]] = None
    for e in enabled:
        events = set(e.get("enabled_events") or [])
        missing = [] if "*" in events else [ev for ev in WEBHOOK_EVENTS if ev not in events]
        if best_missing is None or len(missing) < len(best_missing):
            best_missing = missing
    if best_missing is None:
        best_missing = list(WEBHOOK_EVENTS)
    evidence = {"endpoints": len(endpoints), "enabled_endpoints": len(enabled), "missing_events": best_missing}
    return ("pass" if enabled and not best_missing else "fail"), evidence


# ---------------------------------------------------------------------------
# repo-side (posted by the GitHub job; the production image has no .github/, scripts/ or tests/)
# ---------------------------------------------------------------------------

def _repo_check(check_id: str) -> Callable[[AuditContext], Awaitable[Tuple[str, Dict[str, Any]]]]:
    async def run(ctx: AuditContext) -> Tuple[str, Dict[str, Any]]:
        if not ctx.repo_checks or check_id not in ctx.repo_checks:
            raise NotApplicable("repo-side check not supplied (run via compliance-audit.yml)")
        result = str(ctx.repo_checks[check_id]).strip().lower()
        if result in ("pass", "success"):
            return "pass", {"reported": "pass"}
        if result in ("fail", "failure"):
            return "fail", {"reported": "fail"}
        raise NotApplicable("repo-side check result not recognised", reported=result[:30])
    run.__name__ = "check_" + check_id.replace(".", "_")
    return run


# ---------------------------------------------------------------------------
# launch checklist and coverage (these read the results of the others)
# ---------------------------------------------------------------------------

# A warn from these contradicts a "done" row as well: they warn (not fail) before launch only
# because the state is expected then; once the row claims it is done, the warning is the finding.
WARN_CONTRADICTS_DONE = frozenset({"legal.placeholders", "secrets.presence"})

CheckFn = Callable[..., Awaitable[Tuple[str, Dict[str, Any]]]]


@dataclass(frozen=True)
class CheckSpec:
    id: str
    title: str
    severity: str
    fn: CheckFn
    lc: Tuple[str, ...] = ()
    late: bool = False  # reads the results of the other checks


async def check_launch_checklist(ctx: AuditContext, results: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    path = Path(ctx.checklist_path)
    if not path.exists():
        raise NotApplicable("launch checklist not found")
    rows = launch_checklist.parse(path)
    structural = launch_checklist.problems(rows)
    by_check = {r["id"]: r["status"] for r in results}
    contradicted: List[Dict[str, Any]] = []
    ready: List[str] = []
    for row in rows:
        lc_id = row.get("ID", "")
        mapped = [c for c in LC_MAP.get(lc_id, []) if c != "launch.checklist_consistency"]
        if not mapped:
            continue
        statuses = [by_check.get(c, "n/a") for c in mapped]
        if row.get("Status") == "done":
            bad = [c for c in mapped if by_check.get(c) == "fail"
                   or (by_check.get(c) == "warn" and c in WARN_CONTRADICTS_DONE)]
            if bad:
                contradicted.append({"lc": lc_id, "checks": bad})
        elif row.get("Status") == "todo" and all(s == "pass" for s in statuses):
            ready.append(lc_id)
    evidence = {"rows": len(rows), "structural_problems": structural[:_MAX_LIST],
                "structural_problem_count": len(structural), "claimed_done_not_verified": contradicted,
                "ready_to_mark_done": ready}
    return ("fail" if structural or contradicted else "pass"), evidence


async def check_coverage(ctx: AuditContext, results: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    counts = {s: sum(1 for r in results if r["status"] == s) for s in STATUSES}
    reasons = {r["id"]: str((r.get("evidence") or {}).get("reason", ""))[:80] for r in results if r["status"] == "n/a"}
    evidence = {
        "counts": counts, "checks_run": len(results), "disabled": sorted(ctx.disabled),
        "na_reasons": reasons,
        "stripe_configured": _present(getattr(ctx.settings, "stripe_audit_key", None)),
        "repo_checks_supplied": bool(ctx.repo_checks),
        "llm_summary": bool(getattr(ctx.settings, "audit_llm_summary", False)),
        "summary_model": str(getattr(ctx.settings, "audit_summary_model", "")),
        "repo_version_supplied": bool(ctx.repo_version),
    }
    return "pass", evidence


CHECK_SPECS: Tuple[CheckSpec, ...] = (
    CheckSpec("legal.placeholders", "Legal pages contain no placeholders", "high", check_legal_placeholders, ("LC-12",)),
    CheckSpec("legal.live_gate", "Legal-live flag and checkout gate are consistent", "high",
              check_legal_live_gate, ("LC-13",)),
    CheckSpec("backup.litestream_config", "Litestream replicates every database", "high",
              check_litestream_config, ("LC-09", "LC-22")),
    CheckSpec("secrets.presence", "Production secrets are set (names only)", "high",
              check_secrets_presence, ("LC-07",)),
    CheckSpec("release.version_consistency", "Version is consistent across code, MCP and What's New", "medium",
              check_version_consistency),
    CheckSpec("release.deployed_matches_repo", "Deployed version matches the repository", "medium",
              check_deployed_matches_repo),
    CheckSpec("data.quality_regression", "Data quality has not regressed since the last report", "high",
              check_data_quality_regression),
    CheckSpec("data.stale_prices", "Stale confirmed prices are below the warning threshold", "low",
              check_stale_prices),
    CheckSpec("data.weekly_audit_ran", "Weekly price audit ran within 8 days and is not parked", "medium",
              check_weekly_audit_ran),
    CheckSpec("orch.budget_vs_spend", "Orchestrator spend is within budget", "medium", check_budget_vs_spend),
    CheckSpec("orch.open_escalations_age", "No owner escalation is waiting past the age limit", "low",
              check_open_escalations_age),
    CheckSpec("billing.key_hash_migration", "Every service key is stored hashed", "critical",
              check_key_hash_migration, ("LC-21",)),
    CheckSpec("billing.deleted_markers", "Deleted-account markers and salt are intact", "high",
              check_deleted_markers),
    CheckSpec("stripe.reconciliation", "Stripe reconciliation could run", "info", check_stripe_reconciliation),
    CheckSpec("stripe.orphan_subscriptions", "Every live Stripe subscription has a local customer", "high",
              check_stripe_orphans),
    CheckSpec("stripe.tier_mismatch", "Local tiers match Stripe subscriptions", "high", check_stripe_tier_mismatch),
    CheckSpec("stripe.past_due_mismatch", "Past-due state agrees between Stripe and billing", "medium",
              check_stripe_past_due_mismatch),
    CheckSpec("stripe.shared_ids", "No two customers share a Stripe id", "high", check_stripe_shared_ids),
    CheckSpec("stripe.webhook_endpoint", "Stripe webhook endpoint registers the seven event types", "high",
              check_stripe_webhook_endpoint, ("LC-06",)),
    CheckSpec("repo.dependabot", "Dependabot is configured (target-branch develop)", "medium",
              _repo_check("repo.dependabot"), ("LC-18",)),
    CheckSpec("repo.codeql", "CodeQL workflow is present", "medium", _repo_check("repo.codeql"), ("LC-19",)),
    CheckSpec("repo.forbidden_tracked_files", "No database, env or key file is tracked", "critical",
              _repo_check("repo.forbidden_tracked_files")),
    CheckSpec("repo.replication_check", "Replication completeness script passes", "high",
              _repo_check("repo.replication_check"), ("LC-09", "LC-22")),
    CheckSpec("repo.launch_checklist_valid", "Launch checklist script validates", "low",
              _repo_check("repo.launch_checklist_valid")),
    CheckSpec("repo.version_tests", "Version consistency tests pass", "medium", _repo_check("repo.version_tests")),
    CheckSpec("launch.checklist_consistency", "Launch checklist is valid and its done items hold", "medium",
              check_launch_checklist, ("LC-16",), late=True),
    CheckSpec("audit.coverage", "Audit coverage self-report", "info", check_coverage, late=True),
)

CHECK_IDS: Tuple[str, ...] = tuple(s.id for s in CHECK_SPECS)

# Launch-checklist id -> the checks that verify it (derived, so the two cannot drift).
LC_MAP: Dict[str, List[str]] = {}
for _spec in CHECK_SPECS:
    for _lc in _spec.lc:
        LC_MAP.setdefault(_lc, []).append(_spec.id)


def parse_disabled(value: Any) -> frozenset:
    return frozenset(p.strip() for p in str(value or "").split(",") if p.strip())


def _row(spec: CheckSpec, status: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": spec.id, "status": status, "severity": spec.severity, "title": spec.title,
            "evidence": evidence, "lc": list(spec.lc)}


def _na(spec: CheckSpec, reason: str, **evidence: Any) -> Dict[str, Any]:
    return _row(spec, "n/a", {"reason": reason, **evidence})


async def _run_one(spec: CheckSpec, ctx: AuditContext, results: List[Dict[str, Any]]) -> Dict[str, Any]:
    if spec.id in ctx.disabled:
        return _na(spec, "disabled", setting="AUDIT_DISABLED_CHECKS")
    try:
        status, evidence = await (spec.fn(ctx, results) if spec.late else spec.fn(ctx))
    except NotApplicable as na:
        return _na(spec, na.reason, **na.evidence)
    except Exception as exc:  # a broken check is an n/a row, never a parked run
        logger.warning("audit check %s could not run: %s", spec.id, type(exc).__name__)
        return _na(spec, f"error:{type(exc).__name__}")
    if status not in STATUSES:
        return _na(spec, "error:invalid_status")
    return _row(spec, status, evidence)


async def run_all(ctx: AuditContext) -> List[Dict[str, Any]]:
    """Run the whole catalogue in order. Never raises."""
    results: List[Dict[str, Any]] = []
    for spec in CHECK_SPECS:
        if not spec.late:
            results.append(await _run_one(spec, ctx, results))
    for spec in CHECK_SPECS:
        if spec.late:
            results.append(await _run_one(spec, ctx, results))
    order = {s.id: i for i, s in enumerate(CHECK_SPECS)}
    return sorted(results, key=lambda r: order[r["id"]])


def overall_status(results: Sequence[Dict[str, Any]]) -> str:
    statuses = {r["status"] for r in results}
    if "fail" in statuses:
        return "fail"
    return "warn" if "warn" in statuses else "pass"


def count_statuses(results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    return {"pass": sum(1 for r in results if r["status"] == "pass"),
            "warn": sum(1 for r in results if r["status"] == "warn"),
            "fail": sum(1 for r in results if r["status"] == "fail"),
            "n_a": sum(1 for r in results if r["status"] == "n/a")}
