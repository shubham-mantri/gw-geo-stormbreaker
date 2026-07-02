"""Anti-bot fingerprint material: realistic user-agents + humanized timing jitter.

Pure helpers used by `BrowserSession` (m1-design.md S3.1) to make capture traffic resemble an
ordinary browser session closely enough to avoid trivial bot detection. White-hat only (PRD
NG1): this is fingerprint *realism* -- picking from a small pool of real, unmodified browser
User-Agent strings and pacing actions with human-like jitter -- never cloaking, deceptive
headers, or content injection. Both functions accept an injectable `rng` (a `random.Random`)
so callers get deterministic output under a seed; production callers can omit it and get a
fresh, unseeded generator.
"""

import random

# A small pool of real, unmodified desktop-browser User-Agent strings per consumer surface.
# Rotating among a handful of common, legitimate UAs is fingerprint *realism* (looking like an
# ordinary visitor) -- never a fabricated or deceptive fingerprint.
_SURFACE_USER_AGENTS: dict[str, list[str]] = {
    "chatgpt": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like "
        "Gecko) Version/17.4 Safari/605.1.15",
    ],
    "grok": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
    ],
    "google_ai_overviews": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like "
        "Gecko) Version/17.4 Safari/605.1.15",
    ],
}

# Fallback pool for surfaces without a dedicated list above -- still a real, common UA.
_DEFAULT_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
]


def pick_user_agent(surface: str, *, rng: random.Random | None = None) -> str:
    """Return a realistic desktop-browser User-Agent string for `surface`.

    Unknown surfaces fall back to a generic (but still real) pool, so this never raises. Pass
    a seeded `rng` (`random.Random(seed)`) for deterministic tests.
    """
    generator = rng if rng is not None else random.Random()
    pool = _SURFACE_USER_AGENTS.get(surface, _DEFAULT_USER_AGENTS)
    return generator.choice(pool)


def jitter_delay(base_ms: int, *, rng: random.Random | None = None) -> float:
    """Return `base_ms` perturbed by human-like jitter (milliseconds), never negative.

    Spread is +/-30% of `base_ms` (floored at 10ms of noise so even a near-zero base still
    paces like a human rather than firing instantly), which models pacing between capture
    actions (typing, navigation) instead of perfectly uniform, bot-like timing. Pass a seeded
    `rng` (`random.Random(seed)`) for deterministic tests.
    """
    generator = rng if rng is not None else random.Random()
    spread = max(base_ms * 0.3, 10.0)
    return max(0.0, generator.uniform(base_ms - spread, base_ms + spread))
