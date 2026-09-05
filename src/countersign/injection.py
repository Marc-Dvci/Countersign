"""Detecting instructions hidden inside evidence.

A control reads the documents the company wrote about itself. Anyone who can
edit one of those documents can write a sentence addressed to whatever reads it
next, and once a model is doing the reading, that sentence is an attack.

Two things defend against it here, and only one of them is this file.

The structural defence is that a control's outcome is counted from the
population before any model sees any document, so text asking for a different
outcome has nothing to act on. This module is the second defence: it finds the
attempt and puts it in the report, because a policy document that contains an
instruction to an automated reviewer is itself a finding, whether or not it
worked.

The detectors are deliberately plain regular expressions. They run over evidence
whether the model mode is ``demo`` or ``bedrock``, so containment does not
depend on a model noticing.
"""

from __future__ import annotations

import re

from countersign.domain import InjectionSignal

# Each detector: a name, what it is looking for, and why that shape matters.
DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|override|set\s+aside)\b[^.\n]{0,60}\b"
            r"(previous|prior|earlier|above|any|all|conflicting)\b[^.\n]{0,40}\b"
            r"(instruction|instructions|guidance|prompt|rule|rules|entry)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "conclusion_forcing",
        re.compile(
            # Inflections matter here: "has been assessed as effective" is the
            # most natural way to write this attack, and a pattern that only
            # matched the bare stem would miss it.
            r"\b(report|mark|record|assess|conclude|rate|deem|treat|sign\s*off)"
            r"(s|ed|ing)?\b[^.\n]{0,50}\b"
            r"(as\s+)?(effective|compliant|passed|satisfactory|no\s+exceptions?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "omission_demand",
        re.compile(
            r"\b(do\s+not|don't|never|omit|exclude|suppress|refrain\s+from)\b[^.\n]{0,50}\b"
            r"(list|report|mention|include|disclose|show|state)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "authority_claim",
        re.compile(
            r"\b(system\s+note|admin(istrator)?\s+note|internal\s+note|note)\b[^.\n]{0,40}\b"
            r"(automated|machine|ai|agent|reviewer|assistant)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "testing_waiver",
        re.compile(
            r"\bno\s+(further\s+)?(testing|review|verification|assessment)\b[^.\n]{0,30}"
            r"\b(is\s+)?(authoris|authoriz|required|necessary|needed)",
            re.IGNORECASE,
        ),
    ),
    (
        "output_constraint",
        re.compile(
            r"\b(reply|respond|answer|output|return)\b[^.\n]{0,20}\bonly\b[^.\n]{0,30}"
            r"\b(with|the\s+line|the\s+following|:)",
            re.IGNORECASE,
        ),
    ),
    (
        "credential_request",
        re.compile(
            r"\b(api\s*key|access\s*token|secret|credential|password|private\s*key)\b"
            r"[^.\n]{0,40}\b(send|share|provide|include|return|post|forward)\b",
            re.IGNORECASE,
        ),
    ),
)


def scan(text: str, locator: str, context: int = 110) -> list[InjectionSignal]:
    """Every detector that fires on ``text``, with the excerpt that fired it.

    Whitespace is collapsed before matching. Line breaks are free to insert and
    a detector that a newline can slip through is not a detector.
    """
    if not text:
        return []
    text = " ".join(text.split())
    signals: list[InjectionSignal] = []
    for name, pattern in DETECTORS:
        match = pattern.search(text)
        if match is None:
            continue
        start = max(match.start() - 20, 0)
        end = min(match.end() + context, len(text))
        excerpt = " ".join(text[start:end].split())
        signals.append(InjectionSignal(detector=name, locator=locator, excerpt=excerpt))
    return signals


def scan_documents(documents: list[dict]) -> list[InjectionSignal]:
    """Run every detector over a document set's content."""
    signals: list[InjectionSignal] = []
    for document in documents:
        signals.extend(
            scan(str(document.get("content", "")), str(document.get("id", "unknown")))
        )
    return signals


def contained_statement(signals: list[InjectionSignal]) -> str:
    """One sentence a report can carry about what was found and what it changed."""
    if not signals:
        return ""
    locators = sorted({signal.locator for signal in signals})
    detectors = sorted({signal.detector for signal in signals})
    return (
        f"Evidence drawn from {', '.join(locators)} contains text addressed to an automated "
        f"reviewer ({', '.join(detectors)}). It was reported and not followed. The outcome above "
        f"was counted from the population before any model read this document, so the instruction "
        f"had nothing to act on."
    )
