"""Question-authoring assistant (open item #5, Phase A).

Turn an interviewer's natural-language brief into a fully-formed, **validated**
`Question` — the same shape `loader.question_from_dict` accepts — that the
platform can store on human approval. This lives in the agent (not the platform)
for one reason: a test case's `expected` is only trustworthy when produced by
**executing** a reference solution, and execution + `validate_question` all live
here.

The split of labour is deliberate:

- **Claude drafts** the prose, constraints, a *reference (oracle) solution*, and
  the test **inputs** (stdin only). It never supplies expected outputs.
- **The deterministic runner** ([runner.py](runner.py)) executes the reference
  solution over those inputs and captures each `stdout` as the case's `expected`.
  The model never executes anything; the oracle is the executed reference. A case
  whose reference run crashes/times out is dropped (its `expected` would be
  untrustworthy) and a warning is recorded.

Like the judge and the adversarial probe, drafting needs the model: with no
`ANTHROPIC_API_KEY` an honest empty result with a warning is returned (there is
no meaningful offline heuristic for authoring a problem).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from .constants import CORRECTNESS, OFFLINE_ENGINE, PERFORMANCE
from .loader import question_from_dict
from .pricing import Usage
from .questions import TestCase
from .runner import run_submission

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent / "prompts" / "question_draft.md").read_text().strip()

# Weights mirror the built-in questions: the large performance case is worth more
# than the small correctness cases (see questions.py).
_CORRECTNESS_WEIGHT = 1.0
_PERFORMANCE_WEIGHT = 6.0

# The generator only synthesises an input (it isn't the graded solution), so it
# gets generous headroom rather than the candidate-facing per-case time limit.
_GEN_TIME_LIMIT_S = 15.0


class _DraftInput(BaseModel):
    name: str
    stdin: str


class DraftSpec(BaseModel):
    id: str
    title: str
    prompt: str
    constraints: str
    reference_solution: str
    reference_language: str
    # Small hand-written correctness inputs (stdin only — the reference computes
    # the expected output). The model emits these reliably.
    correctness_inputs: list[_DraftInput]
    # A program (in reference_language) that PRINTS one large, constraint-sized,
    # valid input to stdout. The agent executes it to build the single performance
    # case, so the model never hand-writes a huge literal (which it can't do
    # reliably — a declared count drifts from the actual values). Mirrors how the
    # built-in questions synthesise their perf cases with a generator.
    performance_generator: str
    time_limit_s: float = 2.0
    pass_threshold: float = 0.9
    required_complexity: str | None = None


_DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "prompt": {"type": "string"},
        "constraints": {"type": "string"},
        "reference_solution": {"type": "string"},
        "reference_language": {"type": "string"},
        "correctness_inputs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "stdin": {"type": "string"},
                },
                "required": ["name", "stdin"],
            },
        },
        "performance_generator": {"type": "string"},
        "time_limit_s": {"type": "number"},
        "pass_threshold": {"type": "number"},
        "required_complexity": {"type": ["string", "null"]},
    },
    "required": [
        "id",
        "title",
        "prompt",
        "constraints",
        "reference_solution",
        "reference_language",
        "correctness_inputs",
        "performance_generator",
    ],
}


@dataclass
class DraftResult:
    engine: str
    question: dict | None  # loader-shaped, validated Question JSON (None if unusable)
    warnings: list[str] = field(default_factory=list)
    reference_solution: str | None = None
    reference_language: str | None = None
    usage: Usage | None = None


@dataclass(frozen=True)
class DraftConfig:
    # Shares the judge's model/thinking/effort env so all three LLM calls track
    # the same "assessment model" choice.
    model: str = "claude-sonnet-4-6"
    thinking: str = "off"  # "off" | "adaptive"
    effort: str | None = None  # None | "low" | "medium" | "high" | "max"

    @classmethod
    def from_env(cls) -> DraftConfig:
        return cls(
            model=os.environ.get("ASSESSMENT_MODEL", cls.model),
            thinking=os.environ.get("ASSESSMENT_THINKING", cls.thinking).lower(),
            effort=os.environ.get("ASSESSMENT_EFFORT") or None,
        )

    @property
    def engine_label(self) -> str:
        bits = self.model
        if self.thinking == "adaptive":
            bits += "+thinking"
        if self.effort:
            bits += f"+effort:{self.effort}"
        return bits


def _offline_result() -> DraftResult:
    return DraftResult(
        engine=OFFLINE_ENGINE,
        question=None,
        warnings=[
            "Question drafting skipped — requires a live model (set ANTHROPIC_API_KEY). "
            "No offline heuristic exists for authoring a problem."
        ],
    )


# How many times to draft before giving up. Drafting is stochastic: a spec whose
# reference won't compile, or whose cases all die, is usually a one-off — asking
# again tends to produce a working draft. Bounded because each attempt is a paid
# model call plus reference execution. Retries are NOT a fix for a bad brief: a
# genuinely ambiguous ask fails every attempt and the warnings say why.
_DRAFT_ATTEMPTS = int(os.environ.get("ASSESS_DRAFT_ATTEMPTS", "2"))


def draft_question(
    brief: str,
    *,
    language: str,
    difficulty: str | None = None,
    target_complexity: str | None = None,
    attempts: int = _DRAFT_ATTEMPTS,
) -> DraftResult:
    """Draft a validated Question from a natural-language brief.

    Returns a DraftResult; `question` is a loader-shaped JSON dict when a usable,
    validated question was produced, else None with `warnings` explaining why.

    Retries up to `attempts` times while the draft comes back unusable — the model
    is non-deterministic, so a second ask often succeeds where the first produced
    (say) a reference that didn't compile. The last attempt's warnings are what
    the caller sees, prefixed with how many attempts were made.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _offline_result()
    config = DraftConfig.from_env()

    result = DraftResult(engine=config.engine_label, question=None)
    for attempt in range(1, max(1, attempts) + 1):
        result = _draft_once(config, brief, language, difficulty, target_complexity)
        if result.question is not None:
            return result
        logger.info(
            "draft attempt %d/%d produced no usable question: %s",
            attempt,
            attempts,
            "; ".join(result.warnings),
        )
    if attempts > 1:
        result.warnings.insert(0, f"Gave up after {attempts} drafting attempts.")
    return result


def _draft_once(
    config: DraftConfig,
    brief: str,
    language: str,
    difficulty: str | None,
    target_complexity: str | None,
) -> DraftResult:
    try:
        spec, usage = _draft_spec(config, brief, language, difficulty, target_complexity)
    except Exception as exc:
        return DraftResult(
            engine=config.engine_label,
            question=None,
            warnings=[f"Draft generation failed: {exc}"],
        )
    return build_from_spec(spec, engine=config.engine_label, usage=usage)


def build_from_spec(spec: DraftSpec, *, engine: str, usage: Usage | None = None) -> DraftResult:
    """Execute the reference solution to fill each case's `expected`, assemble a
    Question, and validate it. Pure and deterministic given the spec — no model
    call — so tests can drive it with a hand-built spec and no API key."""
    warnings: list[str] = []
    result = DraftResult(
        engine=engine,
        question=None,
        warnings=warnings,
        reference_solution=spec.reference_solution,
        reference_language=spec.reference_language,
        usage=usage,
    )

    if not spec.correctness_inputs:
        warnings.append("The draft produced no correctness inputs.")
        return result

    # 1. Small correctness cases: run the reference over the literal inputs and
    #    take its stdout as each case's expected output.
    probe_cases = tuple(
        TestCase(name=ci.name, stdin=ci.stdin, expected="", category=CORRECTNESS)
        for ci in spec.correctness_inputs
    )
    report = run_submission(
        spec.reference_solution,
        spec.reference_language,
        probe_cases,
        time_limit_s=spec.time_limit_s,
    )
    if report.infra_error:
        warnings.append(f"Reference solution could not run: {report.infra_error}")
        return result
    if report.compile_error:
        warnings.append(f"Reference solution failed to compile: {report.compile_error}")
        return result

    by_name = {o.name: o for o in report.outcomes}
    kept: list[TestCase] = []
    for ci in spec.correctness_inputs:
        o = by_name[ci.name]
        if o.timed_out:
            warnings.append(f"Dropped case {ci.name!r}: reference solution timed out on it.")
        elif o.error is not None:
            warnings.append(f"Dropped case {ci.name!r}: reference solution errored: {o.error}")
        elif o.actual == "":
            warnings.append(f"Dropped case {ci.name!r}: reference produced empty output.")
        else:
            kept.append(TestCase(ci.name, ci.stdin, o.actual, CORRECTNESS, _CORRECTNESS_WEIGHT))

    # 2. The single performance case: execute the generator to synthesise a large
    #    valid input, then run the reference on it for the expected output.
    perf = _build_performance_case(spec, warnings)
    if perf is not None:
        kept.append(perf)

    if not any(t.category == CORRECTNESS for t in kept):
        warnings.append("No correctness case survived the reference run.")
        return result
    if perf is None:
        # validate_question also enforces this, but a targeted message is clearer.
        warnings.append(
            "No performance case was produced; a question needs one constraint-sized "
            "performance case to gate too-slow solutions."
        )
        return result

    # Derive the worked example from the first surviving correctness case: its
    # input and its ORACLE-computed output. Never a model-hand-computed answer —
    # that is the one field a candidate reads closely, and it must carry the same
    # executed-reference guarantee as every other case.
    example_case = next(t for t in kept if t.category == CORRECTNESS)
    example = (example_case.stdin, example_case.expected)

    question_dict = _to_loader_dict(spec, kept, example)
    try:
        # Validate the same way the loader / API intake does, so the drafted
        # question is guaranteed to round-trip and grade.
        question_from_dict(question_dict)
    except Exception as exc:
        warnings.append(f"Drafted question failed validation: {exc}")
        return result

    result.question = question_dict
    return result


def _build_performance_case(spec: DraftSpec, warnings: list[str]) -> TestCase | None:
    """Run the generator to produce a large valid input, then run the reference on
    it to compute the expected output. Returns the performance TestCase, or None
    (with a warning) if either step failed — a perf case whose input or oracle is
    untrustworthy must not slip into the question."""
    gen_report = run_submission(
        spec.performance_generator,
        spec.reference_language,
        (TestCase("perf_gen", stdin="", expected="", category=CORRECTNESS),),
        time_limit_s=_GEN_TIME_LIMIT_S,
    )
    if gen_report.infra_error or gen_report.compile_error:
        warnings.append(
            f"Performance generator did not run: "
            f"{gen_report.infra_error or gen_report.compile_error}"
        )
        return None
    gen = gen_report.outcomes[0]
    if gen.timed_out or gen.error is not None or gen.actual == "":
        warnings.append(
            f"Performance generator failed to produce an input: {gen.error or 'timed out / empty'}"
        )
        return None
    perf_stdin = gen.actual + "\n"

    # The reference is the intended-optimal solution, so it must clear the case's
    # own time limit; if it can't, the limit is too tight or the input too large.
    ref_report = run_submission(
        spec.reference_solution,
        spec.reference_language,
        (TestCase("performance_large", stdin=perf_stdin, expected="", category=PERFORMANCE),),
        time_limit_s=spec.time_limit_s,
    )
    ref = ref_report.outcomes[0] if ref_report.outcomes else None
    if ref is None or ref.timed_out or ref.error is not None or ref.actual == "":
        reason = "no outcome"
        if ref is not None:
            reason = "timed out" if ref.timed_out else (ref.error or "empty output")
        warnings.append(f"Reference solution failed on the generated performance input: {reason}")
        return None
    return TestCase("performance_large", perf_stdin, ref.actual, PERFORMANCE, _PERFORMANCE_WEIGHT)


def _to_loader_dict(spec: DraftSpec, cases: list[TestCase], example: tuple[str, str]) -> dict:
    """Serialize to the exact JSON shape loader.question_from_dict accepts.

    `example` is the oracle-derived worked example (input, output). It is appended
    to the prompt as a clean Input/Output block — the model is instructed not to
    embed one — and also stored in the structured `example` field.
    """
    ex_in, ex_out = example
    prompt = (
        f"{spec.prompt.rstrip()}\n\n"
        f"Example:\nInput:\n{ex_in.rstrip()}\nOutput:\n{ex_out.rstrip()}\n"
    )
    data: dict = {
        "id": spec.id,
        "title": spec.title,
        "prompt": prompt,
        "constraints": spec.constraints,
        "test_cases": [
            {
                "name": t.name,
                "stdin": t.stdin,
                "expected": t.expected,
                "category": t.category,
                "weight": t.weight,
            }
            for t in cases
        ],
        "time_limit_s": spec.time_limit_s,
        "pass_threshold": spec.pass_threshold,
        "required_complexity": spec.required_complexity,
        "example": {"input": ex_in, "output": ex_out},
    }
    return data


def _draft_spec(
    config: DraftConfig,
    brief: str,
    language: str,
    difficulty: str | None,
    target_complexity: str | None,
) -> tuple[DraftSpec, Usage]:
    import anthropic

    client = anthropic.Anthropic()

    hints = [f"LANGUAGE (for the reference solution): {language}"]
    if difficulty:
        hints.append(f"DIFFICULTY: {difficulty}")
    if target_complexity:
        hints.append(f"TARGET COMPLEXITY: {target_complexity}")
    user_content = (
        f"INTERVIEWER BRIEF:\n{brief}\n\n" + "\n".join(hints) + "\n\n"
        "Draft the complete question following the required format."
    )

    output_config: dict = {"format": {"type": "json_schema", "schema": _DRAFT_SCHEMA}}
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
        max_tokens=8000,
        # Stable instruction prefix — cache it across briefs.
        system=[{"type": "text", "text": _PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
        output_config=output_config,
        **kwargs,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Draft generator refused: {response.stop_details}")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"Draft generator returned no text (stop_reason={response.stop_reason})")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        hint = (
            " (response truncated — raise max_tokens)"
            if response.stop_reason == "max_tokens"
            else ""
        )
        raise RuntimeError(f"Draft generator returned invalid JSON{hint}: {exc}") from exc

    spec = DraftSpec.model_validate(data)
    u = response.usage
    usage = Usage(
        model=config.model,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
    return spec, usage


def draft_to_dict(result: DraftResult) -> dict:
    return {
        "engine": result.engine,
        "question": result.question,
        "warnings": result.warnings,
        "reference_solution": result.reference_solution,
        "reference_language": result.reference_language,
        "cost_usd": (result.usage.cost_usd if result.usage and result.usage.priced else None),
    }
