"""Gathers every case file into two lists and checks they are well formed.

    from eval.attacks import ATTACKS, BENIGN

Fails loudly at import time on a duplicate id, a missing key, an unknown success
rule or a badly shaped poison, so a typo can never silently drop a case.
"""
from .cases import benign, data_leak, direct_attack, indirect_attack, indirect_injection, tool_abuse
from .scoring import RULES

ATTACK_FILES = [direct_attack, indirect_injection, indirect_attack, data_leak, tool_abuse]
ATTACK_KEYS = {"id", "family", "message", "poison", "success"}


def _check(attacks, benigns):
    seen = set()
    for c in attacks + benigns:
        if c["id"] in seen:
            raise ValueError(f"duplicate case id: {c['id']}")
        seen.add(c["id"])
        if not isinstance(c.get("message"), str) or not c["message"].strip():
            raise ValueError(f"{c['id']}: message must be non-empty text")
    for c in attacks:
        missing = ATTACK_KEYS - c.keys()
        if missing:
            raise ValueError(f"{c['id']}: missing keys {sorted(missing)}")
        if c["success"] not in RULES:
            raise ValueError(f"{c['id']}: unknown success rule {c['success']!r}")
        p = c["poison"]
        if p is not None and not (isinstance(p, dict) and {"name", "text"} <= p.keys()
                                  and "## " in p["text"]):
            raise ValueError(f"{c['id']}: poison needs name, text, and a '## ' heading")
    for c in benigns:
        if not ({"expect", "expect_refund", "expect_no_refund"} & c.keys()):
            raise ValueError(f"{c['id']}: benign case has nothing to check")


ATTACKS = [case for f in ATTACK_FILES for case in f.ATTACKS]
BENIGN = list(benign.BENIGN)
_check(ATTACKS, BENIGN)
