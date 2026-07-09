"""Token usage and cost estimation for the judge call.

Rates are USD per 1M tokens (input, output), from the Claude model catalog.
Cache reads bill at ~0.1x the input rate; 5-minute cache writes at ~1.25x.
"""

from __future__ import annotations

from dataclasses import dataclass

# $ per 1M tokens: (input, output)
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

_CACHE_READ_MULT = 0.1
_CACHE_WRITE_MULT = 1.25


@dataclass(frozen=True)
class Usage:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        rate = PRICING.get(self.model)
        if rate is None:
            return 0.0  # unknown model — cost not estimated
        in_rate, out_rate = rate
        return (
            self.input_tokens * in_rate
            + self.cache_read_input_tokens * in_rate * _CACHE_READ_MULT
            + self.cache_creation_input_tokens * in_rate * _CACHE_WRITE_MULT
            + self.output_tokens * out_rate
        ) / 1_000_000

    @property
    def priced(self) -> bool:
        return self.model in PRICING
