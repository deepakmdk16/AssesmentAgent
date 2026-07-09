"""Code-quality assessment.

When ANTHROPIC_API_KEY is set, Claude acts as an LLM judge and returns a
structured rubric score. The model, thinking mode, and effort are configurable
via environment variables so you can A/B a cheaper model (with the detailed
rubric doing the work) against Opus:

    ASSESSMENT_MODEL     default "claude-sonnet-4-6"
    ASSESSMENT_THINKING  "off" (default) | "adaptive"
    ASSESSMENT_EFFORT    unset (default) | low | medium | high | max

The idea: a detailed rubric + calibration examples (see rubric.py / prompts/)
replaces most of the runtime reasoning, so a smaller model with thinking off
approaches Opus-with-thinking at a fraction of the token cost.

When no API key is set, a deterministic offline heuristic runs so the whole
pipeline is testable with no key.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from pydantic import BaseModel, Field

from .pricing import Usage
from .rubric import SYSTEM_PROMPT


class CriterionScore(BaseModel):
    name: str
    score: int = Field(ge=1, le=5)
    comment: str


class QualityAssessment(BaseModel):
    criteria: list[CriterionScore]
    overall_score: float = Field(ge=1, le=5)
    strengths: list[str]
    weaknesses: list[str]
    summary: str


# Hand-written JSON schema for structured outputs. Kept explicit (rather than
# generated from the Pydantic model) because structured outputs reject numeric
# constraints like minimum/maximum; Pydantic still enforces the 1-5 bounds when
# we validate the returned JSON.
_QUALITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "score": {"type": "integer"},
                    "comment": {"type": "string"},
                },
                "required": ["name", "score", "comment"],
            },
        },
        "overall_score": {"type": "number"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["criteria", "overall_score", "strengths", "weaknesses", "summary"],
}


@dataclass(frozen=True)
class JudgeConfig:
    model: str = "claude-sonnet-4-6"
    thinking: str = "off"  # "off" | "adaptive"
    effort: str | None = None  # None | "low" | "medium" | "high" | "max"

    @classmethod
    def from_env(cls) -> "JudgeConfig":
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


def assess_quality(
    *, question_prompt: str, language: str, source: str, test_summary: str
) -> tuple[QualityAssessment, str, Usage | None]:
    """Return (assessment, engine, usage). usage is None on the offline path."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        config = JudgeConfig.from_env()
        assessment, usage = _assess_with_claude(config, question_prompt, language, source, test_summary)
        return assessment, config.engine_label, usage
    return _assess_offline(source, test_summary), "offline-heuristic", None


def _assess_with_claude(
    config: JudgeConfig,
    question_prompt: str,
    language: str,
    source: str,
    test_summary: str,
) -> tuple[QualityAssessment, Usage]:
    import anthropic

    client = anthropic.Anthropic()

    user_content = (
        f"PROBLEM STATEMENT:\n{question_prompt}\n\n"
        f"LANGUAGE: {language}\n\n"
        f"AUTOMATED TEST RESULTS (ground truth for correctness):\n{test_summary}\n\n"
        f"CANDIDATE SUBMISSION:\n```{language}\n{source}\n```"
    )

    output_config: dict = {"format": {"type": "json_schema", "schema": _QUALITY_SCHEMA}}
    if config.effort:
        output_config["effort"] = config.effort

    kwargs: dict = {}
    if config.thinking == "adaptive":
        kwargs["thinking"] = {"type": "adaptive"}

    response = client.messages.create(
        model=config.model,
        max_tokens=4000,
        # The rubric is a large, stable prefix — cache it across candidates.
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
        output_config=output_config,
        **kwargs,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Judge refused: {response.stop_details}")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"Judge returned no text (stop_reason={response.stop_reason})")

    assessment = _parse_assessment(text, response.stop_reason)
    u = response.usage
    usage = Usage(
        model=config.model,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
    return assessment, usage


def _parse_assessment(text: str, stop_reason: str | None) -> QualityAssessment:
    """Parse the judge's JSON, clamping scores into 1-5.

    Structured outputs can't enforce numeric bounds, so a stray score of 0 or 6
    would otherwise raise a ValidationError and abort the whole assessment.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        hint = " (response was truncated — raise max_tokens)" if stop_reason == "max_tokens" else ""
        raise RuntimeError(f"Judge returned invalid JSON{hint}: {exc}") from exc

    for c in data.get("criteria", []):
        if isinstance(c.get("score"), (int, float)):
            c["score"] = max(1, min(5, round(c["score"])))
    if isinstance(data.get("overall_score"), (int, float)):
        data["overall_score"] = max(1.0, min(5.0, float(data["overall_score"])))

    return QualityAssessment.model_validate(data)


def _assess_offline(source: str, test_summary: str) -> QualityAssessment:
    """A crude, deterministic stand-in for the LLM judge (no API key needed)."""
    lower = source.lower()
    nonblank = [ln for ln in source.splitlines() if ln.strip()]
    loc = len(nonblank)

    has_comments = any(tok in source for tok in ("#", "//", "/*"))
    has_error_handling = any(
        tok in lower for tok in ("try", "except", "catch", "raise", "throw", "err")
    )
    has_structure = any(
        tok in lower
        for tok in ("def ", "function ", "func ", "fn ", "class ", "int main", "public static")
    )
    reasonable_length = 2 <= loc <= 60

    def score(flag: bool, high: int = 4, low: int = 2) -> int:
        return high if flag else low

    criteria = [
        CriterionScore(name="robustness", score=score(has_error_handling),
                       comment="Explicit error/edge handling detected." if has_error_handling
                       else "No visible input validation or error handling."),
        CriterionScore(name="readability", score=score(has_comments and reasonable_length),
                       comment="Comments present and length is reasonable." if has_comments
                       else "No comments; readability judged on structure only."),
        CriterionScore(name="efficiency", score=3,
                       comment="Not statically analysable offline; assumed adequate for the input size."),
        CriterionScore(name="design", score=score(has_structure),
                       comment="Uses functions/structure." if has_structure
                       else "Written as a flat script."),
    ]
    overall = round(sum(c.score for c in criteria) / len(criteria), 1)

    strengths = []
    if has_structure:
        strengths.append("Code is organised into functions/structures.")
    if has_comments:
        strengths.append("Includes explanatory comments.")
    if not strengths:
        strengths.append("Compact solution.")

    weaknesses = []
    if not has_error_handling:
        weaknesses.append("No input validation or error handling.")
    if not has_comments:
        weaknesses.append("Lacks comments.")

    return QualityAssessment(
        criteria=criteria,
        overall_score=overall,
        strengths=strengths,
        weaknesses=weaknesses or ["None flagged by the offline heuristic."],
        summary=(
            "[offline heuristic — set ANTHROPIC_API_KEY for a real Claude review] "
            f"~{loc} lines of code. Test outcome: {test_summary.splitlines()[0] if test_summary else 'n/a'}."
        ),
    )
