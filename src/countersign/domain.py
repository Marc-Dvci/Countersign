"""Canonical domain types.

Three ideas carry the whole model.

1. A *proposal* is what a model may produce. It is inert until a named person
   accepts it, and every proposal carries the evidence it was drawn from.
2. A *test kind* is a deterministic Python function in the registry. A control
   binds to one. A model may choose which one and parameterise it; a model may
   not invent one, because a control whose ``test_kind`` is not in the registry
   cannot be scheduled.
3. An *outcome* is counted, never written. ``effective`` means the whole
   population was tested and the exception count was within the control's
   tolerance. A run that could not test part of its population is
   ``inconclusive``, never ``effective``: untested is not passing. No sentence a
   model produces can move either of those.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------

SourceKind = Literal[
    "github", "jira", "identity", "hris", "documents", "obligations", "ledger", "vendors"
]
SourceMode = Literal["corpus", "live"]

Sector = Literal["financial_services", "software", "industrial"]

Periodicity = Literal["daily", "weekly", "monthly", "quarterly", "semiannual", "annual"]

PERIOD_DAYS: dict[str, int] = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "quarterly": 91,
    "semiannual": 182,
    "annual": 365,
}

ControlNature = Literal["preventive", "detective", "directive"]
Severity = Literal["low", "medium", "high", "critical"]
RunOutcome = Literal["effective", "ineffective", "inconclusive", "not_run"]
ControlStatus = Literal["proposed", "scheduled", "suspended", "retired"]
FindingStatus = Literal["draft", "open", "risk_accepted", "remediation_agreed", "closed"]

# Who wrote a finding. Set by the pipeline, never by the thing that produced it:
# `deterministic` means the finding was composed from the test result by code,
# which is what happens in demo mode and whenever a model left an exception
# unrepresented.
FindingOrigin = Literal["agent", "deterministic"]

# Severity ordering, used wherever two severities are compared.
SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


class EvidenceRef(BaseModel):
    """A pointer to one thing a connector actually returned.

    Nothing in Countersign asserts a fact without one of these beside it. The
    ``digest`` is taken over the payload as the connector returned it, so a
    report can be re-checked against its source months later.
    """

    source: SourceKind
    locator: str = Field(description="Stable identifier within the source system.")
    label: str = Field(description="Human-readable name for the thing.")
    digest: str = Field(default="", description="SHA-256 of the canonical payload.")
    url: str | None = None
    observed_at: datetime | None = None


class DiscoveredAsset(BaseModel):
    """One object a connector inventoried: a repository, a project, a group, a vendor."""

    source: SourceKind
    kind: str
    external_id: str
    name: str
    attributes: dict[str, Any] = Field(default_factory=dict)

    def ref(self) -> EvidenceRef:
        return EvidenceRef(source=self.source, locator=self.external_id, label=self.name)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


class RegulatoryObligation(BaseModel):
    """A row of the tenant's obligation register.

    Deliberately *data*, not code. Countersign never hardcodes a regulatory
    threshold; it reads the register the tenant maintains and tests the rest of
    the estate against it. When the register and an internal policy disagree,
    that disagreement is the finding.
    """

    reference: str
    regime: str
    requirement: str
    applies_to: list[str] = Field(default_factory=list)
    quantitative_limit: float | None = None
    limit_unit: str | None = None
    source_document: str | None = None


class EnterpriseProfile(BaseModel):
    """What the discovery agent concluded about the company, and why.

    Every field is a proposal. The rationale and the refs are what a person
    reads before agreeing that the taxonomy built on top of it is the right one.
    """

    sector: Sector
    sector_rationale: str
    legal_entities: list[str] = Field(default_factory=list)
    jurisdictions: list[str] = Field(default_factory=list)
    headcount_band: str = ""
    critical_systems: list[str] = Field(default_factory=list)
    key_processes: list[str] = Field(default_factory=list)
    regulatory_perimeter: list[str] = Field(default_factory=list)
    outsourcing_dependencies: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @field_validator("sector_rationale")
    @classmethod
    def _rationale_is_substantive(cls, value: str) -> str:
        if len(value.strip()) < 40:
            raise ValueError("sector_rationale must explain the inference, not restate it")
        return value


# --------------------------------------------------------------------------
# Risk taxonomy
# --------------------------------------------------------------------------


class ProposedRiskDomain(BaseModel):
    """A risk domain the taxonomy agent believes this company has to cover."""

    code: str = Field(pattern=r"^[A-Z][A-Z0-9-]{1,15}$")
    title: str
    description: str
    why_this_company: str = Field(
        description="What was observed in THIS estate that puts the domain in scope."
    )
    regulatory_drivers: list[str] = Field(default_factory=list)
    inherent_likelihood: int = Field(ge=1, le=5)
    inherent_impact: int = Field(ge=1, le=5)
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @property
    def inherent_score(self) -> int:
        return self.inherent_likelihood * self.inherent_impact

    @property
    def inherent_level(self) -> Severity:
        score = self.inherent_score
        if score >= 20:
            return "critical"
        if score >= 12:
            return "high"
        if score >= 6:
            return "medium"
        return "low"

    @field_validator("why_this_company")
    @classmethod
    def _must_be_specific(cls, value: str) -> str:
        if len(value.strip()) < 50:
            raise ValueError(
                "why_this_company must cite what was discovered, not a generic risk statement"
            )
        return value


class RiskDomainProposal(BaseModel):
    """The taxonomy agent's complete answer for one tenant."""

    domains: list[ProposedRiskDomain]
    coverage_note: str = ""
    deliberately_excluded: list[str] = Field(
        default_factory=list,
        description="Domains considered and left out, with the reason. A taxonomy that "
        "excludes nothing has not been thought about.",
    )


# --------------------------------------------------------------------------
# Control design
# --------------------------------------------------------------------------


class ProposedControl(BaseModel):
    """A control the design agent proposes to automate.

    ``test_kind`` must name a function in the deterministic registry and
    ``parameters`` must satisfy that function's declared schema. Both are
    checked before the control can be scheduled, which is why a model cannot
    talk a control into existence.
    """

    code: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,23}$")
    title: str
    objective: str = Field(description="The assertion this control is meant to support.")
    nature: ControlNature
    test_kind: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    periodicity: Periodicity
    periodicity_rationale: str = Field(
        description="Why this frequency: rate of change of the population, not habit."
    )
    tolerance: int = Field(
        default=0, ge=0, description="Exceptions permitted before the control fails."
    )
    severity_if_failed: Severity = "medium"
    owner_role: str = ""
    automation_note: str = ""

    @field_validator("periodicity_rationale")
    @classmethod
    def _rationale_is_substantive(cls, value: str) -> str:
        if len(value.strip()) < 30:
            raise ValueError("periodicity_rationale must justify the frequency")
        return value


class ControlProposal(BaseModel):
    """Everything the design agent proposes for one risk domain."""

    domain_code: str
    controls: list[ProposedControl]
    residual_gap: str = Field(
        default="",
        description="What this control set does NOT cover. Stated so a person can price it.",
    )


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


class PopulationItem(BaseModel):
    """One member of the population a control test walked."""

    subject: str
    label: str
    passed: bool
    reason: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class TestResult(BaseModel):
    """The deterministic half of a control run.

    Produced by pure Python over connector evidence, before any model is
    invoked, and never revised afterwards.
    """

    test_kind: str
    period_start: date
    period_end: date
    population: list[PopulationItem] = Field(default_factory=list)
    not_tested: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @property
    def population_size(self) -> int:
        return len(self.population)

    @property
    def exceptions(self) -> list[PopulationItem]:
        return [item for item in self.population if not item.passed]

    @property
    def exception_count(self) -> int:
        return len(self.exceptions)

    @property
    def coverage_complete(self) -> bool:
        """Whether every member the test set out to walk was actually walked.

        ``not_tested`` holds the members a connector could not serve evidence
        for. They are not passes and they are not exceptions; they are the part
        of the population this run has nothing to say about.
        """
        return not self.not_tested

    def coverage_note(self) -> str:
        """One line naming the coverage gap, empty when there is none."""
        if self.coverage_complete:
            return ""
        return (
            f"{len(self.not_tested)} member(s) of the population could not be tested, so the "
            f"control cannot be concluded over the whole population."
        )

    def outcome(self, tolerance: int) -> RunOutcome:
        """The only place a run outcome is ever decided.

        Three questions, in this order, and nothing else is consulted:

        1. did more members fail than the tolerance allows? Then ``ineffective``.
           An incomplete run that already failed is still a failed run; missing
           coverage cannot rescue it.
        2. was any member left untested, or was the population empty? Then
           ``inconclusive``. This is the coverage rule: a run that walked only
           part of its population has not shown the control operating over the
           population, and an unqualified pass would be a claim the evidence does
           not support.
        3. otherwise ``effective``.

        The consequence that matters elsewhere: ``effective`` implies both a
        non-empty population and complete coverage, which is why closing a
        finding on a later effective run is safe.
        """
        if self.exception_count > tolerance:
            return "ineffective"
        if self.population_size == 0 or not self.coverage_complete:
            return "inconclusive"
        return "effective"


class InjectionSignal(BaseModel):
    """Instruction-shaped text found inside evidence, reported rather than obeyed."""

    detector: str
    locator: str
    excerpt: str


class ProposedFinding(BaseModel):
    """A candidate finding, in the four parts a control committee expects.

    *Candidate* describes the wording, not the existence. Whether an exception
    reaches the findings queue is decided by the count, not here: every
    exception of an ineffective run is represented by one of these before the
    run is stored, and a model that omitted one has it composed for it.
    """

    title: str
    severity: Severity
    condition: str = Field(description="What was observed, with numbers.")
    criterion: str = Field(description="What the obligation, policy or contract requires.")
    cause: str = Field(description="Why the gap exists.")
    effect: str = Field(description="What it costs or exposes.")
    evidence: list[EvidenceRef] = Field(default_factory=list)
    subjects: list[str] = Field(
        default_factory=list, description="Population members this finding rests on."
    )
    proposed_remediation: str = ""
    proposed_owner: str = ""
    origin: FindingOrigin = Field(
        default="agent",
        description=(
            "Whether this finding was written by an agent or composed from the test result by "
            "code. Overwritten by the pipeline, so a model cannot claim to be the deterministic "
            "half."
        ),
    )


class ReviewNarrative(BaseModel):
    """The model's half of a control run: prose and candidate findings.

    It explains a result it cannot change. ``proposed_outcome`` exists only so
    the run can record when the model disagreed with the count; the count wins.
    """

    summary: str = Field(description="What the tester would tell the risk committee.")
    proposed_outcome: RunOutcome
    findings: list[ProposedFinding] = Field(default_factory=list)
    injection_signals: list[InjectionSignal] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)


class Challenge(BaseModel):
    """The challenger agent's attempt to knock a finding down before a person sees it.

    A challenge is an argument attached to a finding, and that is all it is. It
    cannot delete the finding and it cannot change its severity. ``survives`` is
    the challenger's opinion of whether the finding should stand, recorded
    beside the finding so a reviewer can weigh both; ``suggested_downgrade`` is a
    recommendation, stored as one. Removing the only first-class record of a
    deterministic exception because a probabilistic agent argued well is exactly
    the failure this product exists to prevent, so the store raises the finding
    either way and marks it challenged.
    """

    finding_title: str
    strongest_counterargument: str
    survives: bool
    reason: str
    suggested_downgrade: Severity | None = None


class ChallengeSet(BaseModel):
    challenges: list[Challenge] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------


def next_due(after: date, periodicity: Periodicity) -> date:
    """The next date a control of this periodicity falls due."""
    return after + timedelta(days=PERIOD_DAYS[periodicity])


def period_for(due: date, periodicity: Periodicity) -> tuple[date, date]:
    """The window of activity a run falling due on ``due`` is responsible for."""
    return due - timedelta(days=PERIOD_DAYS[periodicity]), due
