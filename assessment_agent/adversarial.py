"""Advisory adversarial test generation (open item #4a).

An *agentic* probe: Claude reads the problem and the candidate's code and
proposes edge-case **inputs** designed to break the submission (minimum/maximum
sizes, boundary values, degenerate structures, ...). Those inputs are then run
through the same deterministic runner the graded pipeline uses — **the model
never executes anything** — and we report which inputs made the candidate
**crash** or **time out**.

This is strictly **advisory**. For an arbitrary interviewer-supplied question the
interviewer is the only oracle for expected outputs, so we deliberately report
only *oracle-independent* failures: a runtime exception / non-zero exit, or a
timeout. A crash or hang on a *valid* input is a real robustness defect
regardless of the expected answer; correctness on generated inputs is **not**
judged here. Nothing in this module feeds the score or the verdict (see
`agent.assess`) — the findings live in their own report section, like the quality
judge.

Enabled opt-in (CLI `--adversarial`, API `adversarial: true`) and only when
``ANTHROPIC_API_KEY`` is set and the submission actually executed. With no key an
offline placeholder is returned so the pipeline stays testable — meaningful
adversarial generation genuinely needs the model, so the offline path produces no
probes (unlike the quality judge's crude source-text heuristic).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from .constants import OFFLINE_ENGINE, PERFORMANCE
from .pricing import Usage
from .questions import Question, TestCase
from .runner import run_submission

_PROMPT = (Path(__file__).parent / "prompts" / "adversarial_gen.md").read_text().strip()

# Guardrail on model-supplied inputs: skip anything absurdly large so a runaway
# generation can't blow up the worker's memory before the runner's limits bite.
_MAX_STDIN_CHARS = 2_000_000
_DETAIL_CHARS = 300


class GeneratedCase(BaseModel):
    name: str
    stdin: str
    rationale: str


_GEN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "stdin": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["name", "stdin", "rationale"],
            },
        }
    },
    "required": ["cases"],
}


@dataclass(frozen=True)
class AdversarialFinding:
    name: str
    stdin: str
    rationale: str
    kind: str  # "crash" | "timeout"
    detail: str


@dataclass
class AdversarialReport:
    engine: str
    probed: int  # how many generated inputs were actually run
    findings: list[AdversarialFinding]
    summary: str
    usage: Usage | None = None


@dataclass(frozen=True)
class AdversarialConfig:
    # Shares the judge's model/thinking/effort env vars so both LLM calls track
    # the same "assessment model" choice.
    model: str = "claude-sonnet-4-6"
    thinking: str = "off"  # "off" | "adaptive"
    effort: str | None = None  # None | "low" | "medium" | "high" | "max"
    num_cases: int = 8

    @classmethod
    def from_env(cls) -> AdversarialConfig:
        return cls(
            model=os.environ.get("ASSESSMENT_MODEL", cls.model),
            thinking=os.environ.get("ASSESSMENT_THINKING", cls.thinking).lower(),
            effort=os.environ.get("ASSESSMENT_EFFORT") or None,
            num_cases=int(os.environ.get("ASSESS_ADVERSARIAL_CASES", cls.num_cases)),
        )

    @property
    def engine_label(self) -> str:
        bits = self.model
        if self.thinking == "adaptive":
            bits += "+thinking"
        if self.effort:
            bits += f"+effort:{self.effort}"
        return bits


def _offline_report() -> AdversarialReport:
    """No API key: return an empty, deterministic placeholder. Unlike the quality
    judge, there is no meaningful offline heuristic — generating adversarial
    inputs requires understanding the problem's input format, which needs the
    model — so we honestly report that nothing was probed."""
    return AdversarialReport(
        engine=OFFLINE_ENGINE,
        probed=0,
        findings=[],
        summary=(
            "Adversarial probing skipped — requires a live model "
            "(set ANTHROPIC_API_KEY). No offline heuristic exists for this."
        ),
    )


def probe_adversarial(*, question: Question, language: str, source: str) -> AdversarialReport:
    """Generate adversarial inputs with Claude, run them through the deterministic
    runner, and report crashes/timeouts. Advisory only — the caller must not let
    the result affect the score or verdict."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _offline_report()
    config = AdversarialConfig.from_env()
    # This is advisory: a generation failure (truncated/invalid JSON, network,
    # refusal, ...) must NEVER abort the assessment. Degrade to an empty report
    # with the reason noted, so the verdict — already decided from execution —
    # stands untouched.
    try:
        cases, usage = _generate_cases(config, question, language, source)
    except Exception as exc:
        return AdversarialReport(
            engine=config.engine_label,
            probed=0,
            findings=[],
            summary=f"Adversarial probing failed (advisory — verdict unaffected): {exc}",
        )
    return _run_and_classify(cases, language, source, question, config.engine_label, usage)


def _generate_cases(
    config: AdversarialConfig, question: Question, language: str, source: str
) -> tuple[list[GeneratedCase], Usage]:
    import anthropic

    client = anthropic.Anthropic()

    example = ""
    if question.example_input is not None or question.example_output is not None:
        example = (
            f"\n\nWORKED EXAMPLE:\nInput:\n{question.example_input or ''}\n"
            f"Output:\n{question.example_output or ''}"
        )
    user_content = (
        f"PROBLEM STATEMENT:\n{question.prompt}\n\n"
        f"CONSTRAINTS:\n{question.constraints}{example}\n\n"
        f"LANGUAGE: {language}\n\n"
        f"CANDIDATE SUBMISSION:\n```{language}\n{source}\n```\n\n"
        f"Propose up to {config.num_cases} adversarial input cases."
    )

    output_config: dict = {"format": {"type": "json_schema", "schema": _GEN_SCHEMA}}
    if config.effort:
        output_config["effort"] = config.effort

    kwargs: dict = {}
    if config.thinking == "adaptive":
        kwargs["thinking"] = {"type": "adaptive"}

    # The SDK's typed overloads don't cover structured `output_config`, the
    # dynamic thinking kwarg, or a runtime (non-Literal) model id — all valid at
    # runtime, so this call is deliberately outside mypy's view (mirrors judge.py).
    response = client.messages.create(  # type: ignore[call-overload]
        model=config.model,
        # Headroom for ~8 compact JSON cases; the prompt forbids huge literal
        # inputs, so this is comfortably enough (a truncation now degrades
        # gracefully in probe_adversarial rather than aborting the assessment).
        max_tokens=8000,
        # Stable instruction prefix — cache it across candidates.
        system=[{"type": "text", "text": _PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
        output_config=output_config,
        **kwargs,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Adversarial generator refused: {response.stop_details}")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(
            f"Adversarial generator returned no text (stop_reason={response.stop_reason})"
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        hint = (
            " (response truncated — raise max_tokens)"
            if response.stop_reason == "max_tokens"
            else ""
        )
        raise RuntimeError(f"Adversarial generator returned invalid JSON{hint}: {exc}") from exc

    cases = [GeneratedCase.model_validate(c) for c in data.get("cases", [])]
    u = response.usage
    usage = Usage(
        model=config.model,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
    return cases, usage


def _run_and_classify(
    cases: list[GeneratedCase],
    language: str,
    source: str,
    question: Question,
    engine: str,
    usage: Usage | None,
) -> AdversarialReport:
    """Run the generated inputs through the SAME deterministic runner as the graded
    pipeline (a separate invocation — these outcomes never touch the graded
    ExecutionReport) and keep only oracle-independent failures.

    Cases run as PERFORMANCE-category so the runner executes them isolated: their
    timing is uncontended and a 'timeout' here means the same thing as the graded
    TLE gate. `expected` is left empty — correctness is not judged, only whether
    the candidate crashed or hung.
    """
    runnable = [c for c in cases if 0 < len(c.stdin) <= _MAX_STDIN_CHARS]
    if not runnable:
        return AdversarialReport(
            engine=engine,
            probed=0,
            findings=[],
            summary="The adversarial generator produced no usable inputs.",
            usage=usage,
        )

    by_name = {f"adv_{i}_{c.name}": c for i, c in enumerate(runnable)}
    test_cases = tuple(
        TestCase(name=name, stdin=c.stdin, expected="", category=PERFORMANCE)
        for name, c in by_name.items()
    )
    report = run_submission(source, language, test_cases, time_limit_s=question.time_limit_s)

    if report.infra_error or report.compile_error:
        # Should not happen (the graded run already executed), but stay honest.
        reason = report.infra_error or report.compile_error
        return AdversarialReport(
            engine=engine,
            probed=0,
            findings=[],
            summary=f"Adversarial probes could not run: {reason}",
            usage=usage,
        )

    findings: list[AdversarialFinding] = []
    for o in report.outcomes:
        src = by_name[o.name]
        if o.timed_out:
            findings.append(
                AdversarialFinding(
                    o.name, src.stdin, src.rationale, "timeout", o.error or "timed out"
                )
            )
        elif o.error is not None:
            findings.append(
                AdversarialFinding(
                    o.name, src.stdin, src.rationale, "crash", o.error[:_DETAIL_CHARS]
                )
            )

    probed = len(report.outcomes)
    if findings:
        summary = (
            f"Probed {probed} generated edge case(s); {len(findings)} caused a crash or "
            f"timeout (advisory — not scored, does not affect the verdict)."
        )
    else:
        summary = f"Probed {probed} generated edge case(s); none crashed or timed out."
    return AdversarialReport(
        engine=engine, probed=probed, findings=findings, summary=summary, usage=usage
    )


def adversarial_to_dict(report: AdversarialReport) -> dict:
    return {
        "engine": report.engine,
        "probed": report.probed,
        "findings": [
            {
                "name": f.name,
                "kind": f.kind,
                "rationale": f.rationale,
                "input": f.stdin,
                "detail": f.detail,
            }
            for f in report.findings
        ],
        "summary": report.summary,
        "cost_usd": (report.usage.cost_usd if report.usage and report.usage.priced else None),
    }
