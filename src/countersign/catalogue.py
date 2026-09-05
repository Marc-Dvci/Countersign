"""The deterministic proposal set.

Countersign runs its agents in one of three model modes. In ``bedrock`` and
``agentcore`` the discovery, taxonomy and control-design agents generate what
follows from the connected estate. In ``demo`` this module answers instead, so
the product works with no credentials, no network and no bill, and so a judge
sees the same screens either way.

Everything here is a *proposal*. Nothing in this file is scheduled, and nothing
in it is a finding. A named person still has to accept each domain and approve
each control, and the outcomes come from :mod:`countersign.control_tests`
walking real rows either way.
"""

from __future__ import annotations

from countersign.domain import (
    ControlProposal,
    EnterpriseProfile,
    EvidenceRef,
    ProposedControl,
    ProposedRiskDomain,
    RiskDomainProposal,
)


def _ref(source: str, locator: str, label: str) -> EvidenceRef:
    return EvidenceRef(source=source, locator=locator, label=label)


# ==========================================================================
# Kestrel Pay
# ==========================================================================

KESTREL_PROFILE = EnterpriseProfile(
    sector="financial_services",
    sector_rationale=(
        "The obligation register carries DORA and electronic-money safeguarding rows, the ledger "
        "source exposes payment authorisation and counterparty screening records, and two of the "
        "three production repositories implement settlement and card authorisation. A company "
        "that has to safeguard client funds and screen counterparties is a licensed payments firm, "
        "not a software vendor that happens to move money."
    ),
    legal_entities=["Kestrel Pay (Ireland) DAC", "Kestrel Pay Polska sp. z o.o. (branch)"],
    jurisdictions=["IE", "PL", "EU"],
    headcount_band="200-500",
    critical_systems=[
        "kestrel-ledger (core ledger and settlement)",
        "kestrel-gateway (card authorisation)",
        "kestrel-console (operations console)",
        "Okta directory",
        "Jira INC / CHG service desks",
    ],
    key_processes=[
        "Card authorisation and clearing",
        "SEPA payouts and settlement",
        "Counterparty onboarding and screening",
        "Payment release and authorisation",
        "ICT incident management",
        "Third-party and outsourcing management",
    ],
    regulatory_perimeter=[
        "Regulation (EU) 2022/2554 (DORA)",
        "European Union (Electronic Money) Regulations 2011",
        "Anti-money-laundering obligations as implemented in Ireland",
        "Board-approved internal policy set",
    ],
    outsourcing_dependencies=[
        "Northgate Cloud Infrastructure (hosting)",
        "Aurora Card Processing (authorisation and clearing)",
        "Sable KYC Screening (sanctions screening)",
    ],
    confidence=0.86,
    evidence=[
        _ref("obligations", "DORA-19-1", "Obligation register: DORA incident notification"),
        _ref("obligations", "EMR-SAFE-01", "Obligation register: safeguarding reconciliation"),
        _ref("ledger", "PAY-80000", "Payment authorisation records"),
        _ref("github", "kestrelpay/kestrel-ledger", "Core ledger repository"),
        _ref("vendors", "v_02", "Aurora Card Processing, critical provider"),
    ],
)

KESTREL_DOMAINS = RiskDomainProposal(
    domains=[
        ProposedRiskDomain(
            code="ICT-RES",
            title="ICT and operational resilience",
            description=(
                "The firm's ability to keep payment services running through an ICT disruption, "
                "and to tell the regulator about it inside the window the register sets."
            ),
            why_this_company=(
                "The register carries a four-hour DORA notification limit and the estate runs its "
                "own ledger and card gateway rather than buying them. Three critical providers "
                "sit underneath, so an outage at any of them is an outage of the licensed service."
            ),
            regulatory_drivers=["DORA-19-1", "DORA-28-8"],
            inherent_likelihood=4,
            inherent_impact=5,
            evidence=[
                _ref("obligations", "DORA-19-1", "Four-hour notification limit"),
                _ref("jira", "INC", "Incident Management project"),
            ],
        ),
        ProposedRiskDomain(
            code="IAM",
            title="Identity and access management",
            description="Who can reach the ledger and the payment release path, and whether they still should.",
            why_this_company=(
                "Two directory groups are marked privileged and one of them, payments-operators, "
                "can release payment batches. The HR source records leavers, so the join that "
                "proves access was removed is available and nobody is running it."
            ),
            regulatory_drivers=["INT-ACC-REVOKE", "INT-ACC-DORMANT"],
            inherent_likelihood=4,
            inherent_impact=4,
            evidence=[
                _ref("identity", "g_pay", "payments-operators, privileged group"),
                _ref("hris", "e_017", "Leaver records available in HR"),
            ],
        ),
        ProposedRiskDomain(
            code="CHG",
            title="Change management",
            description="Whether what reaches production was seen by someone other than its author.",
            why_this_company=(
                "Three production repositories take merges to main directly, and the change "
                "standard permits an emergency path. An emergency path with no compensating check "
                "becomes the normal path within a quarter."
            ),
            regulatory_drivers=["INT-CHG-APPROVE"],
            inherent_likelihood=3,
            inherent_impact=4,
            evidence=[_ref("github", "kestrelpay/kestrel-ledger", "Merges to main")],
        ),
        ProposedRiskDomain(
            code="FINCRIME",
            title="Financial crime",
            description="Sanctions and money-laundering exposure on the counterparties the firm pays.",
            why_this_company=(
                "The ledger records 212 counterparties onboarded in the last quarter and screening "
                "is outsourced to a single provider, so coverage and provider health are the same "
                "risk wearing two hats."
            ),
            regulatory_drivers=["AML-SCR-01"],
            inherent_likelihood=3,
            inherent_impact=5,
            evidence=[_ref("ledger", "CP-2000", "Counterparty onboarding records")],
        ),
        ProposedRiskDomain(
            code="PAYINT",
            title="Payment integrity and safeguarding",
            description="Whether money leaves only when two people meant it to, and client funds stay whole.",
            why_this_company=(
                "The register sets a EUR 50,000 dual-authorisation threshold and a daily "
                "safeguarding reconciliation. The payment records carry the authoriser list, so "
                "the first is testable today; the second has no connected source yet."
            ),
            regulatory_drivers=["INT-PAY-4EYES", "EMR-SAFE-01"],
            inherent_likelihood=2,
            inherent_impact=5,
            evidence=[_ref("obligations", "INT-PAY-4EYES", "Dual authorisation threshold")],
        ),
        ProposedRiskDomain(
            code="GOV",
            title="Governance and policy",
            description="Whether the documents the firm governs itself with are still current and still true.",
            why_this_company=(
                "Seven board-mandated policies each declare their own review cycle, and the "
                "register makes review a testable obligation rather than an intention."
            ),
            regulatory_drivers=["INT-POL-REVIEW"],
            inherent_likelihood=3,
            inherent_impact=2,
            evidence=[_ref("documents", "POL-GOV-000", "Policy Governance Standard")],
        ),
    ],
    coverage_note=(
        "Six domains cover the licensed activity and the systems that carry it. Every domain "
        "below has at least one control that can run today against a connected source."
    ),
    deliberately_excluded=[
        "Prudential capital adequacy. Owned by Finance through the ICAAP; the second line "
        "reviews the process, it does not run a control over the calculation.",
        "Physical security. One serviced office and no owned data centre; the residual risk "
        "sits with the landlord and the cloud provider, both covered under ICT-RES.",
        "Model risk. The firm runs no internal rating or capital model; the fraud engine is a "
        "vendor rules engine and is covered as a third party.",
    ],
)

KESTREL_CONTROLS = [
    ControlProposal(
        domain_code="ICT-RES",
        residual_gap=(
            "This tests notification timeliness only. Whether the incident was classified major "
            "at the right moment is a judgement the control cannot make, and stays with the CISO."
        ),
        controls=[
            ProposedControl(
                code="DORA-INC-01",
                title="Major ICT incident notified inside the registered limit",
                objective=(
                    "Every incident classified major reaches the competent authority within the "
                    "limit held in the obligation register, measured from classification."
                ),
                nature="detective",
                test_kind="obligation_reconciliation",
                parameters={
                    "obligation_reference": "DORA-19-1",
                    "dataset": "jira.issues",
                    "filter": {"project": "INC", "classification": "major"},
                    "start_field": "classified_major_at",
                    "end_field": "regulator_notified_at",
                    "internal_declaration": {
                        "dataset": "jira.automation_settings",
                        "field": "regulator_notification_target_hours",
                    },
                },
                periodicity="monthly",
                periodicity_rationale=(
                    "Major incidents are rare but the window is four hours, so a quarterly test "
                    "would find a breach up to eighty-nine days after the notification was due."
                ),
                tolerance=0,
                severity_if_failed="critical",
                owner_role="Head of Information Security",
                automation_note=(
                    "Reads the register limit rather than the tooling's configured target, and "
                    "reports when the two differ."
                ),
            ),
            ProposedControl(
                code="TPRM-CRIT-01",
                title="Critical providers carry current due diligence and an exit strategy",
                objective=(
                    "Every provider supporting a critical function has due diligence inside the "
                    "registered interval and a documented way out."
                ),
                nature="detective",
                test_kind="vendor_obligation",
                parameters={
                    "obligation_reference": "DORA-28-8",
                    "require_exit_plan": True,
                    "critical_only": True,
                },
                periodicity="quarterly",
                periodicity_rationale=(
                    "The population changes only when a contract is signed or renewed, which "
                    "happens a handful of times a year; the register's limit is measured in months."
                ),
                tolerance=0,
                severity_if_failed="high",
                owner_role="Financial Controller",
            ),
        ],
    ),
    ControlProposal(
        domain_code="IAM",
        residual_gap=(
            "Nothing here tests whether the access a current employee holds is the access their "
            "role needs. That is recertification, and it needs a source nobody has connected."
        ),
        controls=[
            ProposedControl(
                code="IAM-JML-01",
                title="Leaver access removed inside the registered grace period",
                objective="No person who has left retains a working account.",
                nature="detective",
                test_kind="leaver_access_revocation",
                parameters={"obligation_reference": "INT-ACC-REVOKE"},
                periodicity="daily",
                periodicity_rationale=(
                    "The register allows one business day. A control that runs less often than "
                    "the limit it enforces cannot enforce it."
                ),
                tolerance=0,
                severity_if_failed="high",
                owner_role="Head of Information Security",
                automation_note="Rehires are excluded with the rehire date cited.",
            ),
            ProposedControl(
                code="IAM-DORM-01",
                title="Privileged accounts still in use",
                objective="Privileged access that has stopped being used is withdrawn.",
                nature="detective",
                test_kind="dormant_privileged_access",
                parameters={"obligation_reference": "INT-ACC-DORMANT"},
                periodicity="monthly",
                periodicity_rationale=(
                    "The register's dormancy limit is ninety days, so a monthly test catches a "
                    "dormant account inside a third of the window it is policing."
                ),
                tolerance=0,
                severity_if_failed="medium",
                owner_role="Head of Information Security",
            ),
            ProposedControl(
                code="IAM-RECERT-01",
                title="Privileged access recertified each quarter",
                objective="Every privileged grant is confirmed as still required by its owner.",
                nature="detective",
                test_kind="recurrence_interval",
                parameters={
                    "obligation_reference": "INT-ACC-DORMANT",
                    "dataset": "identity.access_reviews",
                    "group_field": "group",
                    "date_field": "completed_at",
                },
                periodicity="quarterly",
                periodicity_rationale=(
                    "Recertification is itself a quarterly obligation, so the control that proves "
                    "it happened runs on the same cycle."
                ),
                tolerance=0,
                severity_if_failed="medium",
                owner_role="Head of Information Security",
                automation_note=(
                    "Needs an access-review source. Okta exposes campaigns only under Identity "
                    "Governance, which this tenant does not license, so the control cannot be "
                    "scheduled until a source is connected."
                ),
            ),
        ],
    ),
    ControlProposal(
        domain_code="CHG",
        residual_gap=(
            "Approval is not review. This proves a second person signed; it cannot prove they read it."
        ),
        controls=[
            ProposedControl(
                code="CHG-APP-01",
                title="Production change approved by someone other than its author",
                objective="Every change merged to a production branch was approved by a second person.",
                nature="detective",
                test_kind="change_approval",
                parameters={"emergency_label": "emergency"},
                periodicity="weekly",
                periodicity_rationale=(
                    "Roughly a dozen changes reach production each week; a weekly test keeps the "
                    "exception population small enough to investigate while the context is fresh."
                ),
                tolerance=0,
                severity_if_failed="medium",
                owner_role="Change Manager",
                automation_note="Emergency changes with an approved CAB ticket are suppressed, ticket cited.",
            )
        ],
    ),
    ControlProposal(
        domain_code="FINCRIME",
        residual_gap="Screening coverage is not screening quality; the provider's match logic is out of scope.",
        controls=[
            ProposedControl(
                code="AML-SCR-01",
                title="Counterparties screened before the first payment",
                objective="No counterparty is paid before it has been screened.",
                nature="preventive",
                test_kind="screening_coverage",
                parameters={"obligation_reference": "AML-SCR-01"},
                periodicity="weekly",
                periodicity_rationale=(
                    "Onboarding runs continuously and the obligation binds before the first "
                    "payment, so the test has to run inside a normal payment cycle."
                ),
                tolerance=0,
                severity_if_failed="critical",
                owner_role="Money Laundering Reporting Officer",
            )
        ],
    ),
    ControlProposal(
        domain_code="PAYINT",
        residual_gap=(
            "Daily safeguarding reconciliation (EMR-SAFE-01) has no connected source. It is the "
            "largest uncovered obligation in the register and is named here so it is not forgotten."
        ),
        controls=[
            ProposedControl(
                code="PAY-4EYES-01",
                title="Two independent authorisers above the registered threshold",
                objective=(
                    "No payment at or above the registered threshold leaves on one person's "
                    "authority."
                ),
                nature="preventive",
                test_kind="dual_authorisation",
                parameters={"obligation_reference": "INT-PAY-4EYES"},
                periodicity="daily",
                periodicity_rationale=(
                    "Payments settle the same day, so a breach found tomorrow is a breach that "
                    "can still be recalled; found next month it is a loss."
                ),
                tolerance=0,
                severity_if_failed="critical",
                owner_role="Head of Payment Operations",
            )
        ],
    ),
    ControlProposal(
        domain_code="GOV",
        residual_gap="Currency is not quality. A policy reviewed on time can still be wrong.",
        controls=[
            ProposedControl(
                code="GOV-POLREV-01",
                title="Mandated policies reviewed inside their own cycle",
                objective="Every board-mandated policy has been re-approved within the cycle it declares.",
                nature="directive",
                test_kind="policy_review_currency",
                parameters={"mandated_only": True},
                periodicity="quarterly",
                periodicity_rationale=(
                    "Review cycles are annual, so a quarterly test surfaces an overdue policy "
                    "within three months of the date it lapsed."
                ),
                tolerance=0,
                severity_if_failed="low",
                owner_role="Compliance Officer",
            )
        ],
    ),
]


# ==========================================================================
# Northwind Systems
# ==========================================================================

NORTHWIND_PROFILE = EnterpriseProfile(
    sector="software",
    sector_rationale=(
        "The obligation register is built from SOC 2 trust services criteria and GDPR processor "
        "obligations, the estate is two application repositories with no ledger or payment source, "
        "and every vendor row is an infrastructure or analytics service. This is a company whose "
        "product is the software and whose regulatory exposure runs through its customers' data."
    ),
    legal_entities=["Northwind Systems Ltd"],
    jurisdictions=["GB", "EU"],
    headcount_band="20-50",
    critical_systems=["northwind-api", "northwind-web", "Okta directory", "Cirrus Object Storage"],
    key_processes=[
        "Product change and release",
        "Customer data storage and processing",
        "Access provisioning",
        "Sub-processor engagement",
    ],
    regulatory_perimeter=[
        "SOC 2 Trust Services Criteria",
        "Regulation (EU) 2016/679 (GDPR)",
        "Internal policy set",
    ],
    outsourcing_dependencies=[
        "Cirrus Object Storage (customer files)",
        "Loomis Email Delivery (transactional email)",
        "Pinehurst Analytics (product analytics)",
    ],
    confidence=0.81,
    evidence=[
        _ref("obligations", "SOC2-CC8.1", "SOC 2 change criterion"),
        _ref("obligations", "GDPR-28", "Processor agreement obligation"),
        _ref("vendors", "nv_02", "Processor handling personal data"),
    ],
)

NORTHWIND_DOMAINS = RiskDomainProposal(
    domains=[
        ProposedRiskDomain(
            code="CHG",
            title="Change management",
            description="Whether production change is authorised and reviewed before it ships.",
            why_this_company=(
                "Two repositories deploy straight from main and the SOC 2 report the company "
                "sells against carries CC8.1, so an unreviewed merge is an audit exception with a "
                "commercial price attached."
            ),
            regulatory_drivers=["SOC2-CC8.1"],
            inherent_likelihood=4,
            inherent_impact=3,
            evidence=[_ref("github", "northwind/northwind-api", "Merges to main")],
        ),
        ProposedRiskDomain(
            code="IAM",
            title="Identity and access management",
            description="Who holds production access, and whether they still work here.",
            why_this_company=(
                "Three of eight staff hold production-admin rights and the HR source shows a "
                "departure inside the period, so the joiner-mover-leaver join is testable now."
            ),
            regulatory_drivers=["SOC2-CC6.2", "SOC2-CC6.3"],
            inherent_likelihood=3,
            inherent_impact=4,
            evidence=[_ref("identity", "ng_admin", "production-admins group")],
        ),
        ProposedRiskDomain(
            code="DPRIV",
            title="Data protection",
            description="Whether every party touching customer personal data is under contract to.",
            why_this_company=(
                "Three vendors process personal data on the company's behalf and the register "
                "carries the Article 28 obligation, so each one needs a written agreement on file."
            ),
            regulatory_drivers=["GDPR-28"],
            inherent_likelihood=3,
            inherent_impact=4,
            evidence=[_ref("vendors", "nv_02", "Loomis Email Delivery")],
        ),
        ProposedRiskDomain(
            code="GOV",
            title="Governance and policy",
            description="Whether the policies the SOC 2 report describes are still in force.",
            why_this_company=(
                "Three mandated policies each declare an annual cycle, and the SOC 2 auditor will "
                "ask for the review evidence before anything else."
            ),
            regulatory_drivers=["INT-POL-REVIEW"],
            inherent_likelihood=3,
            inherent_impact=2,
            evidence=[_ref("documents", "SEC-POL-04", "Access Control Standard")],
        ),
    ],
    coverage_note="Four domains, weighted to what the SOC 2 report and the DPA obligations demand.",
    deliberately_excluded=[
        "Financial crime. The company takes card payments through a merchant of record and "
        "onboards no counterparties of its own.",
        "Operational resilience as a regulated obligation. No financial-services licence, so "
        "availability is a contractual commitment rather than a supervisory one.",
    ],
)

NORTHWIND_CONTROLS = [
    ControlProposal(
        domain_code="CHG",
        residual_gap="Test coverage and rollback readiness are not tested here.",
        controls=[
            ProposedControl(
                code="CHG-APP-01",
                title="Production change approved by someone other than its author",
                objective="Every merge to a production branch carries an independent approval.",
                nature="detective",
                test_kind="change_approval",
                parameters={},
                periodicity="weekly",
                periodicity_rationale=(
                    "About twenty changes a quarter reach production; weekly keeps each exception "
                    "small enough that the author still remembers the change."
                ),
                tolerance=0,
                severity_if_failed="medium",
                owner_role="VP Engineering",
            )
        ],
    ),
    ControlProposal(
        domain_code="IAM",
        residual_gap="Recertification of standing access is not covered; no review source is connected.",
        controls=[
            ProposedControl(
                code="IAM-JML-01",
                title="Leaver access removed inside the registered grace period",
                objective="No departed employee retains a working account.",
                nature="detective",
                test_kind="leaver_access_revocation",
                parameters={"obligation_reference": "SOC2-CC6.2"},
                periodicity="daily",
                periodicity_rationale=(
                    "The criterion allows one business day, so anything less frequent than daily "
                    "cannot evidence compliance with it."
                ),
                tolerance=0,
                severity_if_failed="high",
                owner_role="VP Engineering",
            )
        ],
    ),
    ControlProposal(
        domain_code="DPRIV",
        residual_gap="Whether the agreement's terms are adequate is a legal review, not a control test.",
        controls=[
            ProposedControl(
                code="GDPR-DPA-01",
                title="Every processor is under a written data processing agreement",
                objective="No third party processes personal data without an agreement on record.",
                nature="preventive",
                test_kind="required_field_present",
                parameters={
                    "obligation_reference": "GDPR-28",
                    "dataset": "vendors.vendors",
                    "required_field": "dpa_reference",
                    "filter": {"processes_personal_data": True},
                },
                periodicity="quarterly",
                periodicity_rationale=(
                    "The population changes only when a vendor is engaged, a few times a year, "
                    "and the exposure accrues from the first day of processing."
                ),
                tolerance=0,
                severity_if_failed="high",
                owner_role="Data Protection Officer",
            )
        ],
    ),
    ControlProposal(
        domain_code="GOV",
        residual_gap="Currency is not quality.",
        controls=[
            ProposedControl(
                code="GOV-POLREV-01",
                title="Mandated policies reviewed inside their own cycle",
                objective="Every mandated policy has been re-approved within its declared cycle.",
                nature="directive",
                test_kind="policy_review_currency",
                parameters={"mandated_only": True},
                periodicity="quarterly",
                periodicity_rationale=(
                    "Cycles are annual; a quarterly test finds a lapsed policy within a quarter "
                    "of it lapsing, which is inside the SOC 2 observation window."
                ),
                tolerance=0,
                severity_if_failed="low",
                owner_role="Head of Engineering",
            )
        ],
    ),
]


# ==========================================================================
# Brandt Werke
# ==========================================================================

BRANDT_PROFILE = EnterpriseProfile(
    sector="industrial",
    sector_rationale=(
        "The obligation register carries CSRD sustainability disclosure, dual-use export control "
        "and a health-and-safety inspection interval; the work tracker holds site inspections and "
        "outbound shipments rather than software changes. There is no code repository and no "
        "payment ledger in the estate at all, which rules out both other sectors."
    ),
    legal_entities=["Brandt Werke GmbH"],
    jurisdictions=["DE", "EU"],
    headcount_band="500-1000",
    critical_systems=["Jira HSE inspections", "Jira EXP shipments", "SAP ERP (not connected)"],
    key_processes=[
        "High-hazard area inspection",
        "Export dispatch and classification",
        "Sustainability data collection",
        "Supplier qualification",
    ],
    regulatory_perimeter=[
        "Directive (EU) 2022/2464 (CSRD)",
        "Regulation (EU) 2021/821 (dual-use export control)",
        "Internal health and safety standard",
    ],
    outsourcing_dependencies=["Stahlwerk Ostheim (steel)", "Rhein Logistik (freight)"],
    confidence=0.78,
    evidence=[
        _ref("obligations", "DUAL-USE-ART3", "Export authorisation obligation"),
        _ref("obligations", "INT-HSE-INSP", "Thirty-day inspection interval"),
        _ref("jira", "EXP", "Export shipments project"),
    ],
)

BRANDT_DOMAINS = RiskDomainProposal(
    domains=[
        ProposedRiskDomain(
            code="HSE",
            title="Health and safety",
            description="Whether high-hazard areas are being inspected at the interval the standard sets.",
            why_this_company=(
                "Four high-hazard work areas are tracked, including a solvent store and a furnace "
                "hall, and the register turns the thirty-day inspection interval into something "
                "that can be counted rather than asserted."
            ),
            regulatory_drivers=["INT-HSE-INSP", "CSRD-S1"],
            inherent_likelihood=3,
            inherent_impact=5,
            evidence=[_ref("jira", "HSE", "Site inspections project")],
        ),
        ProposedRiskDomain(
            code="TRADE",
            title="Trade and export control",
            description="Whether controlled goods left the customs territory with an authorisation.",
            why_this_company=(
                "Shipments are classified as dual-use listed or not on the ticket itself, and "
                "recent dispatches went to destinations outside the customs territory. The "
                "consequence of getting this wrong is criminal, not financial."
            ),
            regulatory_drivers=["DUAL-USE-ART3"],
            inherent_likelihood=2,
            inherent_impact=5,
            evidence=[_ref("jira", "EXP-3312", "Listed item dispatched")],
        ),
        ProposedRiskDomain(
            code="ESG",
            title="Sustainability disclosure",
            description="Whether the numbers that will be published can be evidenced.",
            why_this_company=(
                "The register carries ESRS E1 and S1 disclosure obligations, and the underlying "
                "emissions and injury data are collected outside any connected system today."
            ),
            regulatory_drivers=["CSRD-E1", "CSRD-S1"],
            inherent_likelihood=3,
            inherent_impact=4,
            evidence=[_ref("obligations", "CSRD-E1", "Scope 1 and 2 disclosure")],
        ),
        ProposedRiskDomain(
            code="GOV",
            title="Governance and policy",
            description="Whether the standards these controls test against are themselves current.",
            why_this_company=(
                "Three mandated standards declare annual cycles and one of them governs export "
                "classification, so a lapsed standard here has a direct operational consequence."
            ),
            regulatory_drivers=["INT-POL-REVIEW"],
            inherent_likelihood=3,
            inherent_impact=2,
            evidence=[_ref("documents", "TRD-STD-001", "Trade Compliance Standard")],
        ),
    ],
    coverage_note=(
        "Four domains. ESG is proposed with no runnable control today because the emissions and "
        "injury data live in systems nobody has connected; that gap is the point of naming it."
    ),
    deliberately_excluded=[
        "Financial reporting controls. Audited annually by the statutory auditor; the second "
        "line would duplicate the external audit rather than add to it.",
        "Product liability. Handled contractually by Legal with an insurance backstop.",
    ],
)

BRANDT_CONTROLS = [
    ControlProposal(
        domain_code="HSE",
        residual_gap="Inspection quality is not tested; only that one happened inside the interval.",
        controls=[
            ProposedControl(
                code="HSE-INSP-01",
                title="High-hazard areas inspected inside the registered interval",
                objective="Every high-hazard work area has been inspected within the interval the standard sets.",
                nature="detective",
                test_kind="recurrence_interval",
                parameters={
                    "obligation_reference": "INT-HSE-INSP",
                    "dataset": "jira.issues",
                    "group_field": "work_area",
                    "date_field": "inspected_at",
                    "filter": {"project": "HSE"},
                },
                periodicity="weekly",
                periodicity_rationale=(
                    "The interval is thirty days, so a weekly test finds a lapsed area within a "
                    "week of it lapsing rather than at the end of the next cycle."
                ),
                tolerance=0,
                severity_if_failed="high",
                owner_role="Head of HSE",
            )
        ],
    ),
    ControlProposal(
        domain_code="TRADE",
        residual_gap=(
            "Whether an item was classified correctly in the first place is an engineering "
            "judgement; this control only tests what follows from the classification on the ticket."
        ),
        controls=[
            ProposedControl(
                code="TRD-EXP-01",
                title="Listed items dispatched only with a recorded export authorisation",
                objective="No shipment flagged dual-use listed leaves without an authorisation reference.",
                nature="preventive",
                test_kind="required_field_present",
                parameters={
                    "obligation_reference": "DUAL-USE-ART3",
                    "dataset": "jira.issues",
                    "required_field": "export_authorisation",
                    "filter": {"project": "EXP", "dual_use_listed": True},
                },
                periodicity="weekly",
                periodicity_rationale=(
                    "Dispatch is continuous and the breach completes the moment the goods leave, "
                    "so the test has to run inside a normal shipping week."
                ),
                tolerance=0,
                severity_if_failed="critical",
                owner_role="Trade Compliance Manager",
            )
        ],
    ),
    ControlProposal(
        domain_code="GOV",
        residual_gap="Currency is not quality.",
        controls=[
            ProposedControl(
                code="GOV-POLREV-01",
                title="Mandated standards reviewed inside their own cycle",
                objective="Every mandated standard has been re-approved within its declared cycle.",
                nature="directive",
                test_kind="policy_review_currency",
                parameters={"mandated_only": True},
                periodicity="quarterly",
                periodicity_rationale=(
                    "Cycles are annual; quarterly testing bounds how long a lapsed standard can "
                    "govern an operational decision."
                ),
                tolerance=0,
                severity_if_failed="low",
                owner_role="Head of Compliance",
            )
        ],
    ),
]


# ==========================================================================
# Lookup
# ==========================================================================

TENANTS: dict[str, dict] = {
    "kestrel": {
        "display_name": "Kestrel Pay",
        "description": "A licensed electronic money institution in Ireland with a Polish branch.",
        "profile": KESTREL_PROFILE,
        "domains": KESTREL_DOMAINS,
        "controls": KESTREL_CONTROLS,
    },
    "northwind": {
        "display_name": "Northwind Systems",
        "description": "A B2B software company selling against a SOC 2 report.",
        "profile": NORTHWIND_PROFILE,
        "domains": NORTHWIND_DOMAINS,
        "controls": NORTHWIND_CONTROLS,
    },
    "brandt": {
        "display_name": "Brandt Werke",
        "description": "A German industrial manufacturer with export-controlled products.",
        "profile": BRANDT_PROFILE,
        "domains": BRANDT_DOMAINS,
        "controls": BRANDT_CONTROLS,
    },
}


def tenant_names() -> list[str]:
    return list(TENANTS)


def profile_for(tenant: str) -> EnterpriseProfile:
    return TENANTS[tenant]["profile"]


def domains_for(tenant: str) -> RiskDomainProposal:
    return TENANTS[tenant]["domains"]


def controls_for(tenant: str) -> list[ControlProposal]:
    return TENANTS[tenant]["controls"]
