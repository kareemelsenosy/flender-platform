"""Status emails — Operations OS.

Flender asked not to have to open a dashboard to find out where a collection
is. These are the moments worth interrupting someone for: a collection has
been read, something needs approving, a package went to SAP, and SAP either
took it or did not.

Nothing here raises. An email that cannot be sent must never stop a
collection being processed, so a failure is reported back as False and the
caller carries on.
"""
from __future__ import annotations

import logging

from app.config import APP_BASE_URL

logger = logging.getLogger("flender")

INTAKE_DONE = "intake"
APPROVAL_NEEDED = "approval"
DELIVERED = "delivered"
RECONCILED = "reconciled"


def _send(to_email: str, subject: str, lines, link: str = "") -> bool:
    """Render and send. Import is local so tests can patch the sender."""
    from app.routers.auth_routes import _send_email

    if not to_email:
        return False
    body = "\n".join(lines)
    text = body + (f"\n\n{APP_BASE_URL}{link}" if link else "")
    html_lines = "".join(
        f"<div style='margin:4px 0'>{line}</div>" for line in lines)
    html = (
        "<div style=\"font-family:Arial,sans-serif;max-width:520px;"
        "margin:0 auto;padding:28px 22px;color:#1f1f1f\">"
        f"<div style='font-size:1.1rem;font-weight:700;margin-bottom:12px'>{subject}</div>"
        f"{html_lines}"
        + (f"<div style='margin-top:18px'><a href='{APP_BASE_URL}{link}' "
           "style='background:#111;color:#fff;padding:10px 18px;border-radius:8px;"
           "text-decoration:none;display:inline-block'>Open the collection</a></div>"
           if link else "")
        + "</div>")
    try:
        return bool(_send_email(to_email, subject, text, html))
    except Exception:
        logger.warning("status email failed", exc_info=True)
        return False


def intake_complete(to_email: str, job) -> bool:
    """What arrived, what is missing, and how big the collection is."""
    report = job.report or {}
    totals = report.get("totals", {})
    lines = [
        f"{job.label} has been read.",
        "",
        f"{totals.get('styles', 0):,} styles · "
        f"{totals.get('colour_styles', 0):,} colourways · "
        f"{totals.get('skus', 0):,} SKUs",
    ]
    if report.get("missing"):
        from app.core.intake import KIND_LABELS
        missing = ", ".join(KIND_LABELS.get(k, k) for k in report["missing"])
        lines += ["", f"Still missing: {missing}"]
    for warning in (report.get("warnings") or [])[:5]:
        lines.append(f"· {warning}")
    return _send(to_email, f"{job.label} — collection received",
                 lines, f"/collections/{job.id}")


def approval_needed(to_email: str, job, run, label: str) -> bool:
    """Sent when a package is generated and waiting on a person."""
    decisions = (run.critical_count or 0) + (run.review_count or 0)
    lines = [
        f"{label} is ready for {job.label}.",
        "",
        f"{run.row_count:,} rows generated.",
    ]
    if run.critical_count:
        lines.append(f"{run.critical_count} critical error(s) must be resolved "
                     f"before it can be approved.")
    if decisions:
        lines.append(f"{decisions} decision(s) need a person.")
    else:
        lines.append("Nothing outstanding — it can be approved as it is.")
    return _send(to_email, f"{job.label} — {label} needs approval",
                 lines, f"/collections/{job.id}/packages/{run.kind}")


def delivered(to_email: str, job, run, label: str) -> bool:
    lines = [f"{label} for {job.label} has been put in the SAP import folder.",
             "", f"{run.row_count:,} rows.",
             "SAP has not confirmed anything yet — check it once the import "
             "has run."]
    return _send(to_email, f"{job.label} — {label} sent to SAP",
                 lines, f"/collections/{job.id}/packages/{run.kind}")


def reconciled(to_email: str, job, run, label: str) -> bool:
    """The one that matters: did SAP actually create what we sent?"""
    missing = run.missing_in_sap or []
    lines = [f"{label} for {job.label} has been checked against SAP.",
             "",
             f"{run.found_count:,} of {run.expected_count:,} products found."]
    if missing:
        lines += ["", f"{len(missing)} still missing:"]
        lines += [f"· {m}" for m in missing[:10]]
        if len(missing) > 10:
            lines.append(f"· and {len(missing) - 10} more")
    else:
        lines.append("Everything expected is in SAP.")
    subject = (f"{job.label} — {label}: {len(missing)} missing in SAP"
               if missing else f"{job.label} — {label} confirmed in SAP")
    return _send(to_email, subject, lines,
                 f"/collections/{job.id}/packages/{run.kind}")
