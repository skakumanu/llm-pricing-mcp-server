#!/usr/bin/env python
"""
Audit a codebase for LLM API call sites and recommend token-usage optimizations.

Statically finds LLM SDK call sites (OpenAI, Anthropic, Google, LangChain) in a
Python codebase, estimates the token cost of each call using the same pricing
data and cost math as the predict_cost MCP tool, and recommends cheaper models
or prompt caching where the underlying call site makes that possible to detect.

Recommendation types: model downgrade, prompt caching, Batch API (for calls in a
loop or an offline-looking path), task-based right-sizing (flagship model on a
simple task, even when the $ savings alone wouldn't clear the downgrade bar), and
duplicate-prompt detection across the whole repo. Python-only extraction.

Static analysis cannot know real call frequency — cost totals below are
per-call estimates multiplied by an assumed --calls-per-month, not observed
usage. Pair this with POST /usage (this server's actual usage tracking) once
the code is deployed, for real numbers.

Usage:
    python scripts/audit_repo.py /path/to/customer/repo
    python scripts/audit_repo.py /path/to/customer/repo --format json --output report.json
    python scripts/audit_repo.py /path/to/customer/repo --calls-per-month 5000
"""
import argparse
import ast
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.pricing import PricingMetrics  # noqa: E402
from src.services.pricing_aggregator import PricingAggregatorService  # noqa: E402
from src.services.task_profiles import estimate_output_tokens, infer_task_type  # noqa: E402
from src.services.token_counter import compute_cache_savings, count_tokens, providers_with_caching  # noqa: E402

# ---------------------------------------------------------------------------
# Known SDK call patterns: trailing attribute chain -> sdk label.
# Checked longest suffix first so e.g. ("chat","completions","create") wins
# over the generic ("create",) before it's even tried.
# ---------------------------------------------------------------------------
KNOWN_CALL_SUFFIXES: Dict[Tuple[str, ...], str] = {
    ("chat", "completions", "create"): "openai",
    ("completions", "create"): "openai",
    ("messages", "create"): "anthropic",
    ("generate_content",): "google",
}

MODEL_KWARGS = ("model",)
PROMPT_KWARGS = ("messages", "system", "prompt", "input", "contents")
CACHE_KWARGS = ("cache_control", "cached_content")

DEFAULT_EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    "build", "dist", ".mypy_cache", ".pytest_cache", ".tox",
}

# Task types with low, fairly fixed output — a flagship model on one of these
# is flagged as likely overkill regardless of the $ savings threshold.
_SIMPLE_TASK_TYPES = {"classification", "extraction", "summarization", "translation", "rewrite"}

# A confirmed-price model this good is almost certainly a flagship/reasoning
# model — pricey by nature, not by mistake, but still worth a second look on a
# task from _SIMPLE_TASK_TYPES.
_HIGH_QUALITY_THRESHOLD = 85.0

# Path components suggesting a call site runs offline/batch rather than
# in a live request path — a reasonable place to suggest the Batch API.
_OFFLINE_PATH_HINTS = ("batch", "job", "cron", "pipeline", "worker", "etl", "offline")


def _looks_offline_path(file: str) -> bool:
    lower = file.lower().replace("\\", "/")
    parts = lower.replace(".py", "").split("/")
    return any(hint in part for part in parts for hint in _OFFLINE_PATH_HINTS)


@dataclass
class CallSite:
    file: str
    line: int
    sdk_hint: str
    model_literal: Optional[str]
    prompt_text: Optional[str]
    prompt_dynamic: bool
    cache_kwarg_present: bool
    in_loop: bool = False


@dataclass
class Finding:
    call_site: CallSite
    prompt_tokens: int
    task_type: str
    model_in_catalogue: bool
    cost_per_call_usd: float
    recommendations: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AST extraction
# ---------------------------------------------------------------------------

def _attr_chain_suffix(node: ast.expr, depth: int) -> Tuple[str, ...]:
    """Trailing attribute names of a Call's func, e.g. x.chat.completions.create -> ('chat','completions','create')."""
    names: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute) and len(names) < depth:
        names.insert(0, cur.attr)
        cur = cur.value
    return tuple(names)


def _literal_str(node: ast.expr) -> Optional[str]:
    """Return the string value of a literal or fully-static f-string, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                return None  # has an interpolated (dynamic) part
        return "".join(parts)
    return None


def _collect_local_str_vars(tree: ast.Module) -> Dict[str, str]:
    """Collect module/function-local `NAME = "literal"` assignments (e.g. SYSTEM_PROMPT = "...")."""
    found: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            s = _literal_str(node.value)
            if s is not None:
                found[node.targets[0].id] = s
    return found


class _CallSiteVisitor(ast.NodeVisitor):
    def __init__(self, filename: str, local_str_vars: Dict[str, str]):
        self.filename = filename
        self.local_str_vars = local_str_vars
        self.sites: List[CallSite] = []
        self._loop_depth = 0

    def visit_For(self, node: ast.For) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            sdk_hint = (
                KNOWN_CALL_SUFFIXES.get(_attr_chain_suffix(node.func, 3))
                or KNOWN_CALL_SUFFIXES.get(_attr_chain_suffix(node.func, 1))
            )
            if sdk_hint:
                self._record(node, sdk_hint)
        self.generic_visit(node)

    def _resolve_str(self, node: ast.expr) -> Optional[str]:
        s = _literal_str(node)
        if s is not None:
            return s
        if isinstance(node, ast.Name) and node.id in self.local_str_vars:
            return self.local_str_vars[node.id]
        return None

    def _resolve_messages_list(self, node: ast.expr) -> Optional[str]:
        """Best-effort: concatenate literal `"content": "..."` values inside a messages=[{...}, ...] list."""
        texts = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Dict):
                for key_node, value_node in zip(sub.keys, sub.values):
                    if isinstance(key_node, ast.Constant) and key_node.value == "content":
                        s = self._resolve_str(value_node)
                        if s:
                            texts.append(s)
            elif isinstance(sub, ast.keyword) and sub.arg == "content":
                s = self._resolve_str(sub.value)
                if s:
                    texts.append(s)
        return "\n".join(texts) if texts else None

    def _record(self, node: ast.Call, sdk_hint: str) -> None:
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}

        model_literal = None
        for k in MODEL_KWARGS:
            if k in kwargs:
                model_literal = self._resolve_str(kwargs[k])
                break

        # Combine every prompt-bearing kwarg present (e.g. Anthropic calls commonly
        # pass both `system=` and `messages=`) rather than stopping at the first match.
        prompt_parts: List[str] = []
        any_prompt_kwarg_present = False
        for k in PROMPT_KWARGS:
            if k not in kwargs:
                continue
            any_prompt_kwarg_present = True
            val = kwargs[k]
            resolved = self._resolve_str(val)
            if resolved is None and isinstance(val, (ast.List, ast.Dict)):
                resolved = self._resolve_messages_list(val)
            if resolved:
                prompt_parts.append(resolved)
        prompt_text = "\n".join(prompt_parts) if prompt_parts else None
        prompt_dynamic = any_prompt_kwarg_present and prompt_text is None

        self.sites.append(CallSite(
            file=self.filename,
            line=node.lineno,
            sdk_hint=sdk_hint,
            model_literal=model_literal,
            prompt_text=prompt_text,
            prompt_dynamic=prompt_dynamic,
            cache_kwarg_present=any(k in kwargs for k in CACHE_KWARGS),
            in_loop=self._loop_depth > 0,
        ))


def _iter_python_files(root: Path, exclude_dirs=DEFAULT_EXCLUDE_DIRS):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def extract_call_sites(root: Path) -> List[CallSite]:
    """Walk `root` and return every detected LLM SDK call site."""
    sites: List[CallSite] = []
    for path in _iter_python_files(root):
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        local_vars = _collect_local_str_vars(tree)
        rel = str(path.relative_to(root))
        visitor = _CallSiteVisitor(rel, local_vars)
        visitor.visit(tree)
        sites.extend(visitor.sites)
    return sites


# ---------------------------------------------------------------------------
# Cost analysis + recommendations
# ---------------------------------------------------------------------------

def _find_model(all_pricing: List[PricingMetrics], model_literal: str) -> Optional[PricingMetrics]:
    lower = model_literal.lower()
    for m in all_pricing:
        if m.model_name.lower() == lower:
            return m
    return None


def _rank_by_cost(
    all_pricing: List[PricingMetrics], input_tokens: int, output_tokens: int
) -> List[Tuple[PricingMetrics, float]]:
    """Return (model, total_cost_usd) for every priced model, cheapest first."""
    candidates = [m for m in all_pricing if m.pricing_model == "per_token" and m.price_confirmed]
    ranked = []
    for m in candidates:
        cost = (m.cost_per_input_token / 1000) * input_tokens + (m.cost_per_output_token / 1000) * output_tokens
        ranked.append((m, cost))
    ranked.sort(key=lambda pair: pair[1])
    return ranked


def analyze_call_site(
    site: CallSite, all_pricing: List[PricingMetrics], calls_per_month: int
) -> Optional[Finding]:
    """Estimate cost and recommendations for one call site. None if not analyzable."""
    if site.prompt_dynamic or not site.prompt_text:
        return None

    input_tokens = count_tokens(site.prompt_text)
    task_type = infer_task_type(site.prompt_text)
    output_tokens = estimate_output_tokens(task_type, input_tokens)

    ranked = _rank_by_cost(all_pricing, input_tokens, output_tokens)
    if not ranked:
        return None

    current_model = _find_model(all_pricing, site.model_literal) if site.model_literal else None
    model_in_catalogue = current_model is not None
    if current_model is not None:
        current_cost = next((cost for m, cost in ranked if m.model_name == current_model.model_name), None)
    else:
        current_cost = None

    # If we don't know the model actually used, report against the median-cost
    # model as a neutral baseline rather than silently picking the cheapest.
    if current_cost is None:
        current_model, current_cost = ranked[len(ranked) // 2]

    recommendations: List[Dict[str, Any]] = []

    cheapest_model, cheapest_cost = ranked[0]
    if cheapest_model.model_name != current_model.model_name and current_cost > 0:
        savings_per_call = current_cost - cheapest_cost
        # Only worth flagging if the cheaper model isn't a meaningful quality
        # downgrade and the saving is more than noise.
        quality_ok = (
            cheapest_model.quality_score is None
            or current_model.quality_score is None
            or cheapest_model.quality_score >= current_model.quality_score - 10
        )
        downgrade_recommended = quality_ok and savings_per_call / current_cost >= 0.15
        if downgrade_recommended:
            message = f"Switch from {current_model.model_name} to {cheapest_model.model_name}"
            if task_type in _SIMPLE_TASK_TYPES:
                message += f" — this is a {task_type} call, unlikely to need a flagship model"
            recommendations.append({
                "type": "model_downgrade",
                "message": message,
                "current_model": current_model.model_name,
                "suggested_model": cheapest_model.model_name,
                "estimated_savings_per_call_usd": round(savings_per_call, 8),
                "estimated_savings_monthly_usd": round(savings_per_call * calls_per_month, 4),
            })
    else:
        downgrade_recommended = False

    # Right-sizing: a flagship-quality model on a structurally simple task is worth a
    # second look even when it already cleared the model_downgrade $ bar above (or
    # there was no cheaper confirmed-price alternative at all) — this is about
    # whether the task needs this much model, not just about today's cheapest price.
    if (
        not downgrade_recommended
        and task_type in _SIMPLE_TASK_TYPES
        and current_model.quality_score is not None
        and current_model.quality_score >= _HIGH_QUALITY_THRESHOLD
    ):
        recommendations.append({
            "type": "right_size_for_task",
            "message": (
                f"{current_model.model_name} is a high-quality (score {current_model.quality_score:.0f}) "
                f"model for a {task_type} call — this task type has small, fairly fixed output and rarely "
                f"needs flagship-level reasoning. Worth testing a smaller model even though this pick "
                f"already looks cost-efficient by our savings threshold."
            ),
        })

    if current_model.batch_available and (site.in_loop or _looks_offline_path(site.file)):
        batch_savings = current_cost * 0.5  # typical Batch API discount across providers; not per-model data yet
        recommendations.append({
            "type": "batch_api",
            "message": (
                f"{current_model.model_name} has a Batch API and this call "
                + ("runs inside a loop" if site.in_loop else "looks like an offline/batch job")
                + " — if it isn't latency-sensitive, Batch APIs are typically ~50% cheaper"
            ),
            "estimated_savings_per_call_usd": round(batch_savings, 8),
            "estimated_savings_monthly_usd": round(batch_savings * calls_per_month, 4),
            "assumed_discount_pct": 50,
        })

    if not site.cache_kwarg_present and current_model.provider in providers_with_caching():
        cache_savings = compute_cache_savings(
            current_model.provider, input_tokens, current_model.cost_per_input_token, cache_hit_ratio=0.8
        )
        if cache_savings > 0:
            recommendations.append({
                "type": "enable_prompt_caching",
                "message": (
                    f"No cache_control found on this call — {current_model.provider} supports prompt "
                    f"caching and this prompt is large enough to benefit"
                ),
                "estimated_savings_per_call_usd": round(cache_savings, 8),
                "estimated_savings_monthly_usd": round(cache_savings * calls_per_month, 4),
                "assumed_cache_hit_ratio": 0.8,
            })

    return Finding(
        call_site=site,
        prompt_tokens=input_tokens,
        task_type=task_type,
        model_in_catalogue=model_in_catalogue,
        cost_per_call_usd=round(current_cost, 8),
        recommendations=recommendations,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def find_duplicate_prompts(findings: List[Finding]) -> List[Dict[str, Any]]:
    """Group findings whose call sites share the exact same resolved prompt text.

    Exact-match only for step 2 — near-duplicates (e.g. a template with one
    interpolated word) aren't caught, since prompt_text here is already
    post-resolution and doesn't preserve the original template structure.
    """
    by_prompt: Dict[str, List[Finding]] = {}
    for f in findings:
        text = f.call_site.prompt_text
        if text:
            by_prompt.setdefault(text, []).append(f)

    groups = []
    for text, group in by_prompt.items():
        if len(group) < 2:
            continue
        snippet = text if len(text) <= 100 else text[:97] + "..."
        groups.append({
            "prompt_snippet": snippet,
            "occurrences": len(group),
            "sites": [{"file": f.call_site.file, "line": f.call_site.line} for f in group],
            "message": (
                f"The exact same prompt appears at {len(group)} call sites — consider extracting it to "
                "a shared constant, and make sure prompt caching (if applicable) is enabled consistently "
                "across all of them"
            ),
        })
    groups.sort(key=lambda g: g["occurrences"], reverse=True)
    return groups


def build_report(
    repo_path: Path, sites: List[CallSite], findings: List[Finding], calls_per_month: int
) -> Dict[str, Any]:
    skipped = [
        {"file": s.file, "line": s.line, "reason": "dynamic model/prompt — could not analyze statically"}
        for s in sites if s.prompt_dynamic or not s.prompt_text
    ]
    total_monthly = sum(f.cost_per_call_usd * calls_per_month for f in findings)
    total_savings = sum(
        r.get("estimated_savings_monthly_usd", 0.0) for f in findings for r in f.recommendations
    )
    duplicate_prompts = find_duplicate_prompts(findings)
    return {
        "repo_path": str(repo_path),
        "scanned_at": time.time(),
        "assumed_calls_per_month_per_site": calls_per_month,
        "call_sites_found": len(sites),
        "call_sites_analyzed": len(findings),
        "call_sites_skipped": len(skipped),
        "findings": [
            {
                "file": f.call_site.file,
                "line": f.call_site.line,
                "sdk": f.call_site.sdk_hint,
                "model": f.call_site.model_literal,
                "model_in_catalogue": f.model_in_catalogue,
                "task_type": f.task_type,
                "prompt_tokens": f.prompt_tokens,
                "estimated_cost_per_call_usd": f.cost_per_call_usd,
                "estimated_monthly_cost_usd": round(f.cost_per_call_usd * calls_per_month, 4),
                "recommendations": f.recommendations,
            }
            for f in findings
        ],
        "skipped": skipped,
        "duplicate_prompts": duplicate_prompts,
        "summary": {
            "total_estimated_monthly_cost_usd": round(total_monthly, 2),
            "total_potential_savings_monthly_usd": round(total_savings, 2),
            "note": (
                f"Cost totals assume {calls_per_month} calls/month per site — static analysis "
                "cannot know real call frequency. Pass --calls-per-month to adjust, or cross-check "
                "against POST /usage's actual recorded spend once deployed."
            ),
        },
    }


def format_markdown(report: Dict[str, Any]) -> str:
    lines = [
        f"# LLM Usage Audit — {report['repo_path']}",
        "",
        f"- Call sites found: **{report['call_sites_found']}**",
        f"- Analyzed: **{report['call_sites_analyzed']}** · Skipped (dynamic): **{report['call_sites_skipped']}**",
        f"- Estimated monthly cost: **${report['summary']['total_estimated_monthly_cost_usd']:,.2f}** "
        f"(assumes {report['assumed_calls_per_month_per_site']} calls/month per site)",
        f"- Potential monthly savings if all recommendations applied: "
        f"**${report['summary']['total_potential_savings_monthly_usd']:,.2f}**",
        "",
        "> " + report["summary"]["note"],
        "",
        "## Findings (sorted by estimated monthly cost)",
        "",
    ]
    findings = sorted(report["findings"], key=lambda f: f["estimated_monthly_cost_usd"], reverse=True)
    for f in findings:
        lines.append(f"### `{f['file']}:{f['line']}` — {f['model'] or '(unknown model)'}")
        lines.append(
            f"- Task: {f['task_type']} · {f['prompt_tokens']} prompt tokens · "
            f"${f['estimated_cost_per_call_usd']:.6f}/call · ${f['estimated_monthly_cost_usd']:,.2f}/mo"
        )
        if not f["model_in_catalogue"]:
            lines.append("- ⚠️ Model not found in pricing catalogue — cost estimated against a median-cost model")
        for r in f["recommendations"]:
            savings = r.get("estimated_savings_monthly_usd")
            suffix = f" (save ~${savings:,.2f}/mo)" if savings is not None else ""
            lines.append(f"- 💡 **{r['type']}**: {r['message']}{suffix}")
        lines.append("")
    if report["duplicate_prompts"]:
        lines.append(f"## Duplicate Prompts ({len(report['duplicate_prompts'])})")
        lines.append("")
        for g in report["duplicate_prompts"]:
            lines.append(f"- **\"{g['prompt_snippet']}\"** — {g['message']}")
            for s in g["sites"]:
                lines.append(f"  - `{s['file']}:{s['line']}`")
        lines.append("")
    if report["skipped"]:
        lines.append(f"## Skipped ({len(report['skipped'])})")
        lines.append("")
        for s in report["skipped"][:20]:
            lines.append(f"- `{s['file']}:{s['line']}` — {s['reason']}")
        if len(report["skipped"]) > 20:
            lines.append(f"- ... and {len(report['skipped']) - 20} more")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def run_audit(repo_path: Path, calls_per_month: int) -> Dict[str, Any]:
    sites = extract_call_sites(repo_path)
    aggregator = PricingAggregatorService()
    all_pricing, _ = await aggregator.get_all_pricing_async()

    findings = []
    for site in sites:
        finding = analyze_call_site(site, all_pricing, calls_per_month)
        if finding is not None:
            findings.append(finding)

    return build_report(repo_path, sites, findings, calls_per_month)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo_path", type=Path, help="Path to the codebase to audit")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--output", type=Path, default=None, help="Write report to a file instead of stdout")
    parser.add_argument(
        "--calls-per-month", type=int, default=1000,
        help="Assumed call volume per site for monthly cost estimates (default: 1000)",
    )
    args = parser.parse_args()

    if not args.repo_path.is_dir():
        parser.error(f"{args.repo_path} is not a directory")

    report = asyncio.run(run_audit(args.repo_path, args.calls_per_month))

    if args.format == "json":
        import json
        output = json.dumps(report, indent=2)
    else:
        output = format_markdown(report)

    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
