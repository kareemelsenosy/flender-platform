"""The collection lifecycle — Operations OS.

One place defining the stages a collection moves through and the four output
packages it produces, so the overview, the collection page and the status
emails all describe the same thing.

Stages follow the target operating model: intake, compare against reference
data, create the outputs, review them, surface exceptions, take approvals, push
to SAP and B2B, then audit readiness.
"""
from __future__ import annotations

# ── Stages ───────────────────────────────────────────────────────────────────
# key, label, what it answers. Order is the order they happen in.
STAGES = [
    ("intake",     "Email Intake",     "Receive & classify files"),
    ("compare",    "Map & Compare",    "Reference SAP / Portal / history"),
    ("outputs",    "Create Outputs",   "Items, prices, attributes, images"),
    ("review",     "Automated Review", "Technical, historic, commercial, visual"),
    ("exceptions", "Exceptions",       "Only unusual cases need people"),
    ("approval",   "Human Approval",   "Controlled gates before import"),
    ("sap",        "SAP + B2B",        "Import, publish, reconcile"),
    ("readiness",  "Final Readiness",  "Collection audit & status"),
]
STAGE_KEYS = [k for k, _, _ in STAGES]

# Which stage a stored job status sits in.
STATUS_STAGE = {
    "received": "intake",
    "analysed": "compare",
    "needs_input": "intake",
    "mapping": "compare",
    "generating": "outputs",
    "reviewing": "review",
    "exceptions": "exceptions",
    "awaiting_approval": "approval",
    "approved": "sap",
    "sap_created": "sap",
    "ready": "readiness",
    "error": "intake",
}

STATUS_LABEL = {
    "received": "Received",
    "analysed": "Analysed",
    "needs_input": "Needs input",
    "mapping": "Mapping",
    "generating": "Creating outputs",
    "reviewing": "Under review",
    "exceptions": "Exceptions open",
    "awaiting_approval": "Awaiting approval",
    "approved": "Approved",
    "sap_created": "Created in SAP",
    "ready": "Collection ready",
    "error": "Error",
}

# ── Output packages ──────────────────────────────────────────────────────────
# Derived from the package service, never listed twice. Two hand-kept lists
# drifted the moment TEMP was added — the service said "product_creation" and
# this said "product", and the overview raised a 500 for anyone who had a
# collection.
def _package_rows():
    from app.services.packages import KINDS
    approvers = {
        "product_creation": "Sam / Mizra / Hamid",
        "temp": "Sam / Mizra / Hamid",
        "pricing": "Mo / Julius / Hamid",
        "attributes": "Sam",
        "images": "Joshua",
    }
    destinations = {
        "product_creation": "SAP import Dropbox",
        "temp": "SAP import Dropbox",
        "pricing": "SAP price Dropbox",
        "attributes": "Attribute import",
        "images": "SAP image Dropbox",
    }
    return [(key, label, approvers.get(key, ""), destinations.get(key, ""))
            for key, label in KINDS.items()]


# ── Severities, in the order a reviewer should care about them ───────────────
SEVERITIES = [
    ("critical",      "Critical error", "Unsafe to import — blocks the package"),
    ("manual_review", "Manual review",  "Business judgement required"),
    ("warning",       "Warning",        "Unusual but plausibly correct"),
    ("passed",        "Passed",         "No material anomaly"),
]


PACKAGES = _package_rows()
PACKAGE_KEYS = [k for k, _, _, _ in PACKAGES]


def stage_for(status: str) -> str:
    """Which lifecycle stage a job status belongs to."""
    return STATUS_STAGE.get(status or "", "intake")


def stage_index(status: str) -> int:
    """Position in the pipeline, for progress rendering."""
    try:
        return STAGE_KEYS.index(stage_for(status))
    except ValueError:
        return 0


def status_label(status: str) -> str:
    return STATUS_LABEL.get(status or "", (status or "unknown").replace("_", " ").title())


def package_state(job, key: str) -> dict:  # noqa: D401 — kept for compatibility
    """Status of one output package for a job.

    Until a package has actually run, it reports ``not_started`` rather than
    inventing a percentage — a readiness view that guesses is worse than one
    that says it does not know yet.
    """
    report = job.report if hasattr(job, "report") else {}
    packages = (report or {}).get("packages", {})
    state = packages.get(key) or {}
    return {
        "state": state.get("state", "not_started"),
        "rows": state.get("rows", 0),
        "exceptions": state.get("exceptions", 0),
        "approved": state.get("approved", False),
    }


def _packages_for(job) -> dict:
    """Real package state, from the runs actually stored against the job."""
    from app.services.packages import package_state as real_state
    return real_state(job)


def collection_progress(job) -> dict:
    """Everything the overview needs about one collection, in one call."""
    return {
        "stage": stage_for(job.status),
        "stage_index": stage_index(job.status),
        "stage_count": len(STAGES),
        "status_label": status_label(job.status),
        "packages": _packages_for(job),
    }
