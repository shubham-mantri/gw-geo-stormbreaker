"""White-hat compliance rules engine (`docs/prd.md` NG1, `docs/m4-design.md` S2.4) -- HARD GATE.

The keystone of M4's off-site seeding subsystem: a deterministic, pure evaluator that checks a
proposed off-site placement (`PlacementProposal`) against **global white-hat invariants** (no
astroturf / hidden-text / cloaking / prompt-injection / missing-disclosure -- PRD NG1) and
**per-platform ToS rules**, returning a `ComplianceReport` whose `passed` flag is the hard
precondition the seeding workflow (M4-T10) keys off of before a task may ever reach `placed`:

    passed = no block-severity violation

This is a *gate*, not an advisory linter -- there is no override and no partial credit. `warn`
severity violations are recorded (visible to the human reviewer) but never flip `passed`; only
`block` severity does. `evaluate` performs **no network I/O**: it is pure evaluation over the
`PlacementProposal` fields already supplied by the caller, so it is exercised by hermetic unit
tests with no live posting whatsoever (`docs/trd.md` S12, m4-design.md conventions).

Rules (`ComplianceRule`) are deliberately separated from checks (`CheckFn`): a rule is **data**
(code, channel, severity, description, and a `check` key), while a check is **code** (a pure
predicate function) registered once in `default_checks()`. `evaluate` always re-stamps the
emitted `ComplianceViolation.rule_code`/`.severity` from the matched *rule*, not from whatever the
check function happens to set on the `ComplianceViolation` it returns -- so the rule's severity is
authoritative even for a check reused by another rule under different data (a rule carrying
`severity="warn"` yields a warn violation even if its check hardcoded `"block"`).

**The authoritative runtime ruleset is the code-defined `ComplianceEngine.default_ruleset()`** the
caller passes to `ComplianceEngine(...)`; that -- not any database table -- is what the gate
evaluates. `default_ruleset()` is *also* seeded into the `compliance_rule` table
(`seeding.channels.seed_compliance_rules`, M4-T04), but that table is currently only a **mirror
for future ops-tooling**: nothing reads it back to build the engine, so editing a row's
`severity`/`active` there does **not** (yet) change runtime gate behavior. There is deliberately
no `load_ruleset` DB reader -- building a white-hat hard gate from a possibly-empty/partial table
could silently weaken it (fail-open), which this gate must never risk. Changing what the gate
enforces today is therefore a code change to `default_ruleset()`, reviewed like any other.

A rule that names a `check` key absent from the merged registry raises `ComplianceError` rather
than silently skipping that rule -- a typo'd or stale `check_key` in a seeded `compliance_rule`
row must fail loudly, never quietly disable a white-hat gate.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field


class ComplianceRule(BaseModel):
    """One compliance rule: data, not code (seedable into `compliance_rule`, M4-T04)."""

    code: str
    channel: str  # channel name (e.g. "reddit"), or "*" for every channel
    description: str
    severity: Literal["block", "warn"]
    check: str  # key into the (default ∪ injected) check registry


class ComplianceViolation(BaseModel):
    rule_code: str
    severity: str
    message: str


class ComplianceReport(BaseModel):
    channel: str
    passed: bool  # False iff ANY block-severity violation
    violations: list[ComplianceViolation] = Field(default_factory=list)


class PlacementProposal(BaseModel):
    """A proposed off-site placement, as it would be submitted for compliance review.

    `served_to_bots`, when set, is what a crawler/bot would be served -- compared against `body`
    (what a human would be served) by the `no_cloaking` check.
    """

    channel: str
    body: str
    disclosure_text: str = ""
    author_is_real: bool = True  # attests a real, disclosed human actor
    is_paid: bool = False
    served_to_bots: str | None = None  # if set, must equal body (cloaking check)


CheckFn = Callable[[PlacementProposal], ComplianceViolation | None]


class ComplianceError(Exception):
    """Raised when the compliance gate cannot be evaluated or enforced as configured.

    `ComplianceEngine.evaluate` raises this when a `ComplianceRule.check` key does not resolve in
    the merged check registry (a misconfigured/stale rule must fail loudly, not silently no-op).
    Compliance-gated callers outside this module (e.g. the seeding workflow's `mark_placed`,
    M4-T10) also raise this to hard-block a transition unless the stored report passed -- this
    class lives here so every "the white-hat gate was not satisfied" failure across the seeding
    subsystem is the same exception type.
    """


# ---------------------------------------------------------------------------------------------
# Global (NG1) checks -- apply to every channel via ComplianceRule(channel="*", ...).
# ---------------------------------------------------------------------------------------------

# Zero opacity only: the trailing negative-lookahead stops `opacity:0.5`/`0.99` (visible) from
# matching just the leading `0`. `opacity:0`, `opacity:0.0`, `opacity:0.000` all still match.
_HIDDEN_OPACITY_ZERO_RE = re.compile(r"opacity\s*:\s*0(?:\.0+)?(?![.\d])", re.IGNORECASE)
_HIDDEN_DISPLAY_NONE_RE = re.compile(r"display\s*:\s*none\b", re.IGNORECASE)
_HIDDEN_VISIBILITY_RE = re.compile(r"visibility\s*:\s*hidden\b", re.IGNORECASE)
# Zero font-size (with or without a unit), same zero-only lookahead as opacity above.
_HIDDEN_FONT_SIZE_ZERO_RE = re.compile(
    r"font-size\s*:\s*0(?:\.0+)?\s*(?:px|em|rem|pt|%)?(?![.\d])", re.IGNORECASE
)
# Any negative text-indent is the classic off-screen text-hiding trick.
_HIDDEN_TEXT_INDENT_RE = re.compile(r"text-indent\s*:\s*-\s*\d", re.IGNORECASE)
# A large negative left/top offset (>= 3 digits, e.g. `left:-9999px`) pushes content off-screen.
_HIDDEN_OFFSCREEN_RE = re.compile(r"(?:left|top)\s*:\s*-\s*\d{3,}", re.IGNORECASE)
# White/near-white text is suspicious on its own (no white-background declaration required):
# near-white hex (#eee..#fff / #eeeeee..#ffffff incl. #fefefe), literal `white`, or an rgb() with
# every channel in 240..255.
_WHITE_TEXT_RE = re.compile(
    r"(?<![-\w])color\s*:\s*(?:"
    r"#(?:[ef]{3}|[ef]{6})\b"
    r"|white\b"
    r"|rgb\(\s*2(?:4\d|5[0-5])\s*,\s*2(?:4\d|5[0-5])\s*,\s*2(?:4\d|5[0-5])\s*\)"
    r")",
    re.IGNORECASE,
)
_STUFFING_WORD_RE = re.compile(r"[a-zA-Z]{3,}")
_STUFFING_MIN_COUNT = 5
_STUFFING_MIN_RATIO = 0.3

# LLM-crawler prompt-injection phrasings. Verbose mode for readability; every alternative pins a
# concrete target (an instruction/override verb + a "previous/above"-type anchor, an explicit
# "new instructions:" block, a system-prompt leak, a role reassignment, or an AI-persona lead-in)
# so benign prose is not swept up.
_PROMPT_INJECTION_RE = re.compile(
    r"""
      # ignore/disregard/forget/override/discard [all|the|your|...] previous/above/earlier ...
      (?:ignore|disregard|forget|override|discard)\s+
        (?:(?:all|everything|any|the|these|those|your|of)\s+){0,3}
        (?:previous|prior|earlier|preceding|foregoing|above)\b
    | # same verbs targeting the instructions/prompt/context directly (no "previous" needed)
      (?:ignore|disregard|override|discard)\s+
        (?:(?:all|everything|any|the|these|those|your|of)\s+){0,3}
        (?:instructions?|prompts?|context|directives?|rules?)\b
    | forget\s+everything\b                       # "forget everything (you were told/above)"
    | \bnew\s+instructions?\s*:                    # explicit injected instruction block
    | \bupdated\s+instructions?\s*:
    | system\s*prompt\s*:                          # system-prompt leak / override
    | you\s+are\s+now\s+(?:a|an)\b                  # role reassignment
    | as\s+an?\s+(?:ai\b|a\.i\.|artificial\s+intelligence|language\s+model
        |large\s+language\s+model|llm\b)           # "As an AI assistant, you should ..."
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DISCLOSURE_REQUIRED_CHANNELS = frozenset(
    {"reddit", "quora", "g2", "capterra", "listicle", "wikipedia", "expert_byline"}
)

_PROMO_CTA_RE = re.compile(
    r"buy\s+now|sign\s+up\s+(now|today)|limited[\s-]time|\d+%\s*off|shop\s+now"
    r"|click\s+here|discount\s+code|use\s+code\b",
    re.IGNORECASE,
)
_REDDIT_SELF_PROMO_MIN_HITS = 2

_INCENTIVE_DISCLOSURE_KEYWORDS = ("incentiv", "compensat", "sponsor", "free product", "in exchange")


def _has_keyword_stuffing(text: str) -> bool:
    """Flag a single word repeated so heavily it reads as stuffing rather than prose."""
    words = _STUFFING_WORD_RE.findall(text.lower())
    if not words:
        return False
    _word, count = Counter(words).most_common(1)[0]
    return count >= _STUFFING_MIN_COUNT and (count / len(words)) >= _STUFFING_MIN_RATIO


def _no_astroturf(proposal: PlacementProposal) -> ComplianceViolation | None:
    if proposal.author_is_real is False:
        return ComplianceViolation(
            rule_code="no_astroturf",
            severity="block",
            message="author_is_real is False: placement would use a fake/undisclosed identity "
            "(astroturfing / sockpuppeting), which PRD NG1 forbids outright.",
        )
    return None


def _no_hidden_text(proposal: PlacementProposal) -> ComplianceViolation | None:
    body = proposal.body
    reasons = []
    if _HIDDEN_OPACITY_ZERO_RE.search(body):
        reasons.append("zero-opacity styling")
    if _WHITE_TEXT_RE.search(body):
        # White/near-white text is flagged on its own -- it need not be paired with an explicit
        # white background to be an invisible-text technique.
        reasons.append("white/near-white text")
    if _HIDDEN_DISPLAY_NONE_RE.search(body):
        reasons.append("display:none styling")
    if _HIDDEN_VISIBILITY_RE.search(body):
        reasons.append("visibility:hidden styling")
    if _HIDDEN_FONT_SIZE_ZERO_RE.search(body):
        reasons.append("zero font-size styling")
    if _HIDDEN_TEXT_INDENT_RE.search(body):
        reasons.append("negative text-indent (off-screen) styling")
    if _HIDDEN_OFFSCREEN_RE.search(body):
        reasons.append("off-screen positioning")
    if _has_keyword_stuffing(body):
        reasons.append("excessive keyword stuffing")
    if not reasons:
        return None
    return ComplianceViolation(
        rule_code="no_hidden_text",
        severity="block",
        message="body contains hidden/stuffed text (" + ", ".join(reasons) + ").",
    )


def _no_cloaking(proposal: PlacementProposal) -> ComplianceViolation | None:
    if proposal.served_to_bots is not None and proposal.served_to_bots != proposal.body:
        return ComplianceViolation(
            rule_code="no_cloaking",
            severity="block",
            message="served_to_bots differs from the human-visible body -- bots and humans must "
            "be served identical content.",
        )
    return None


def _no_prompt_injection(proposal: PlacementProposal) -> ComplianceViolation | None:
    if _PROMPT_INJECTION_RE.search(proposal.body):
        return ComplianceViolation(
            rule_code="no_prompt_injection",
            severity="block",
            message="body contains an apparent hidden instruction targeting LLM crawlers "
            "(prompt injection).",
        )
    return None


def _disclosure_required(proposal: PlacementProposal) -> ComplianceViolation | None:
    if proposal.channel in _DISCLOSURE_REQUIRED_CHANNELS and not proposal.disclosure_text.strip():
        return ComplianceViolation(
            rule_code="disclosure_required",
            severity="block",
            message=f"channel {proposal.channel!r} requires an affiliation/COI/sponsored "
            "disclosure, but disclosure_text is empty.",
        )
    return None


# ---------------------------------------------------------------------------------------------
# Per-platform checks (representative set; the full catalog is extended by M4-T04).
# ---------------------------------------------------------------------------------------------


def _reddit_self_promo_ratio(proposal: PlacementProposal) -> ComplianceViolation | None:
    hits = len(_PROMO_CTA_RE.findall(proposal.body))
    if hits >= _REDDIT_SELF_PROMO_MIN_HITS:
        return ComplianceViolation(
            rule_code="reddit_self_promo_ratio",
            severity="block",
            message="body reads as pure self-promotion (multiple promotional CTAs); Reddit "
            "requires genuine participation, not an overt sales pitch (9:1 rule).",
        )
    return None


def _wikipedia_no_paid_self_edit(proposal: PlacementProposal) -> ComplianceViolation | None:
    if proposal.channel == "wikipedia" and proposal.is_paid:
        return ComplianceViolation(
            rule_code="wikipedia_no_paid_self_edit",
            severity="block",
            message="paid/COI editors must not directly self-edit Wikipedia articles -- route "
            "through Talk-page/Articles-for-Creation review instead.",
        )
    return None


def _g2_genuine_review(proposal: PlacementProposal) -> ComplianceViolation | None:
    if proposal.channel == "g2" and proposal.is_paid:
        disclosure = proposal.disclosure_text.lower()
        if not any(keyword in disclosure for keyword in _INCENTIVE_DISCLOSURE_KEYWORDS):
            return ComplianceViolation(
                rule_code="g2_genuine_review",
                severity="block",
                message="an incentivized review must disclose the incentive in disclosure_text; "
                "G2 requires genuine, non-incentivized-undisclosed reviews only.",
            )
    return None


class ComplianceEngine:
    """Evaluates a `PlacementProposal` against a set of `ComplianceRule`s. Pure, no I/O."""

    def __init__(self, rules: list[ComplianceRule], checks: dict[str, CheckFn] | None = None) -> None:
        self._rules = list(rules)
        self._checks: dict[str, CheckFn] = {**self.default_checks(), **(checks or {})}

    def evaluate(self, proposal: PlacementProposal) -> ComplianceReport:
        """Run every rule whose `channel` is `"*"` or `proposal.channel` and collect violations.

        Each matching rule's `check` is resolved in the merged (default ∪ injected) check
        registry; a violation's `rule_code`/`severity` in the returned report always come from
        the *rule*, not from whatever the check function set, so rule severity is authoritative
        even for a check reused by a data-edited rule. `passed` is `True` iff no violation has
        `severity == "block"` -- `warn` violations are reported but never block.

        Raises:
            ComplianceError: a matching rule's `check` key is not in the merged registry.
        """
        violations: list[ComplianceViolation] = []
        for rule in self._rules:
            if rule.channel != "*" and rule.channel != proposal.channel:
                continue
            try:
                check = self._checks[rule.check]
            except KeyError as exc:
                raise ComplianceError(
                    f"compliance rule {rule.code!r} references unknown check {rule.check!r}; "
                    f"registered checks: {sorted(self._checks)}"
                ) from exc
            result = check(proposal)
            if result is None:
                continue
            violations.append(
                ComplianceViolation(rule_code=rule.code, severity=rule.severity, message=result.message)
            )
        passed = not any(v.severity == "block" for v in violations)
        return ComplianceReport(channel=proposal.channel, passed=passed, violations=violations)

    @staticmethod
    def default_ruleset() -> list[ComplianceRule]:
        """The global (NG1) + representative per-platform ruleset -- the **authoritative** gate.

        This is what `ComplianceEngine(...)` callers evaluate against at runtime. It is also seeded
        verbatim into `compliance_rule` (M4-T04), but that table is only a mirror for future
        ops-tooling -- nothing reads it back to build the engine yet, so tuning a row there does
        not change runtime behavior (see this module's docstring). Changing the gate today means
        editing this method.
        """
        return [
            # --- Global white-hat invariants (PRD NG1) -- every channel. ---
            ComplianceRule(
                code="no_astroturf",
                channel="*",
                severity="block",
                check="no_astroturf",
                description="No fake/undisclosed identities, sockpuppets, or coordinated "
                "inauthentic activity.",
            ),
            ComplianceRule(
                code="no_hidden_text",
                channel="*",
                severity="block",
                check="no_hidden_text",
                description="No hidden/white-on-white/zero-opacity text or keyword stuffing.",
            ),
            ComplianceRule(
                code="no_cloaking",
                channel="*",
                severity="block",
                check="no_cloaking",
                description="Content shown to bots must equal content shown to humans.",
            ),
            ComplianceRule(
                code="no_prompt_injection",
                channel="*",
                severity="block",
                check="no_prompt_injection",
                description="No hidden instructions targeting LLM crawlers.",
            ),
            ComplianceRule(
                code="disclosure_required",
                channel="*",
                severity="block",
                check="disclosure_required",
                description="Required affiliation/COI/sponsored disclosure must be present where "
                "the channel demands it.",
            ),
            # --- Per-platform ToS rules (representative; extended by the channel catalog, T04). ---
            ComplianceRule(
                code="reddit_self_promo_ratio",
                channel="reddit",
                severity="block",
                check="reddit_self_promo_ratio",
                description="Reddit self-promotion norms: genuine participation, not an overt "
                "sales pitch (9:1 rule).",
            ),
            ComplianceRule(
                code="wikipedia_no_paid_self_edit",
                channel="wikipedia",
                severity="block",
                check="wikipedia_no_paid_self_edit",
                description="Paid/COI editors must not directly self-edit Wikipedia articles.",
            ),
            ComplianceRule(
                code="g2_genuine_review",
                channel="g2",
                severity="block",
                check="g2_genuine_review",
                description="Only genuine reviews; incentivized reviews must disclose the "
                "incentive.",
            ),
        ]

    @staticmethod
    def default_checks() -> dict[str, CheckFn]:
        return {
            "no_astroturf": _no_astroturf,
            "no_hidden_text": _no_hidden_text,
            "no_cloaking": _no_cloaking,
            "no_prompt_injection": _no_prompt_injection,
            "disclosure_required": _disclosure_required,
            "reddit_self_promo_ratio": _reddit_self_promo_ratio,
            "wikipedia_no_paid_self_edit": _wikipedia_no_paid_self_edit,
            "g2_genuine_review": _g2_genuine_review,
        }
