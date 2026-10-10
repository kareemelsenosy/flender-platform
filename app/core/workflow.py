"""The steps of a collection, in order, and what each one is waiting for.

Shown at the top of the collection page so nobody has to know the process by
heart: which step is next, which are done, and — for a step that cannot be
done yet — exactly what it depends on. Pure: it reads state, decides nothing.
"""
from __future__ import annotations

from app.core import intake as intake_core
from app.core.brand_rules import brand_key

DONE, NEXT, TODO, BLOCKED, ATTENTION, OPTIONAL = (
    "done", "next", "todo", "blocked", "attention", "optional")


def steps(job, report: dict, packages: dict, *, rules: dict,
          pending_rules: int, sap_connected: bool) -> list[dict]:
    """Build the step list. ``packages`` is ``packages.package_state(job)``."""
    received = set(report.get("received", []))
    kinds = {f.kind for f in job.files}
    out: list[dict] = []

    def add(title, state, detail, *, needs="", link="", anchor=""):
        out.append({"n": len(out) + 1, "title": title, "state": state,
                    "detail": detail, "needs": needs, "link": link,
                    "anchor": anchor})

    # 1. Files
    has_order = intake_core.ORDER_SHEET in received
    missing = [intake_core.KIND_LABELS[k] for k in report.get("missing", [])]
    add("Upload the supplier files",
        DONE if has_order else NEXT,
        ("Order sheet in. " + (f"Still missing: {', '.join(missing)} — add them with "
                               "“Add files” below when they arrive." if missing else
                               "Everything expected has arrived."))
        if has_order else "Add the order sheet (and the product list with HS codes, "
                          "if the brand sends one).",
        anchor="add-files")

    # 2. Brand and season
    identity_ok = bool(job.brand and job.season)
    add("Confirm brand and season",
        DONE if identity_ok else (NEXT if has_order else BLOCKED),
        f"{job.brand or '—'} · {job.season or '—'}" if identity_ok else
        "Set them in the yellow box — the brand decides which rules apply.",
        needs="" if has_order else "step 1")

    # 3. Column mapping
    order_maps = [m for m in (report.get("mappings") or {}).values()
                  if m.get("kind") == intake_core.ORDER_SHEET]
    gaps = sorted({f for m in order_maps for f in ("Item No.", "Color", "Item Description")
                   if not (m["fields"].get(f) or {}).get("header")})
    add("Check the column mapping",
        ATTENTION if gaps else (DONE if order_maps else BLOCKED),
        (f"No column found for {', '.join(gaps)} — pick it in Column mapping."
         if gaps else "Every key field was found. Glance at it once per new brand.")
        if order_maps else "Appears once the order sheet is read.",
        anchor="mapping", needs="" if order_maps else "step 1")

    # 4. Product list check (only when the brand sends one)
    combine = report.get("combine") or {}
    if intake_core.PRODUCT_LIST in kinds:
        lost = combine.get("only_in_order_count", 0) + combine.get("only_in_list_count", 0)
        add("Check order sheet against product list",
            ATTENTION if lost else DONE,
            (f"{combine.get('only_in_order_count', 0)} only in the order sheet, "
             f"{combine.get('only_in_list_count', 0)} only in the product list."
             if lost else f"All {combine.get('matched_items', 0)} items match."),
            anchor="combine")

    # 5. Brand rules
    settings = rules.get("settings", {})
    colours = len(rules.get("tables", {}).get("colours", []))
    rule_bits = []
    state = DONE
    if settings.get("uses_base_colour"):
        if colours:
            rule_bits.append(f"{colours} colours in the colour table")
        else:
            rule_bits.append("no colour table yet — import the Seasonal Colour Table")
            state = ATTENTION
    else:
        rule_bits.append("this brand does not use base colours")
    if pending_rules:
        rule_bits.append(f"{pending_rules} rule change{'s' if pending_rules != 1 else ''} "
                         "waiting for approval")
        state = ATTENTION
    add("Review the brand rules", state if job.brand else BLOCKED,
        "; ".join(rule_bits).capitalize() + "." if job.brand else "Needs the brand.",
        link=f"/brand-rules/{brand_key(job.brand)}" if job.brand else "",
        needs="" if job.brand else "step 2")

    # 6–9. Packages
    ready_to_build = has_order and identity_ok
    core_pkgs = ("temp", "product_creation")
    built = [k for k in core_pkgs if packages[k]["state"] != "not_started"]
    open_q = sum(packages[k]["exceptions"] for k in core_pkgs)
    add("Generate TEMP and SAP Product Creation",
        (DONE if len(built) == len(core_pkgs) else NEXT) if ready_to_build else BLOCKED,
        ("Both generated." if len(built) == len(core_pkgs) else
         "Press Generate on each card under Output packages.")
        if ready_to_build else "Needs the order sheet and the brand and season.",
        needs="" if ready_to_build else "steps 1–2")

    approved = [k for k in core_pkgs if packages[k]["approved"]]
    if len(approved) == len(core_pkgs):
        state, detail = DONE, "Both approved."
    elif not built:
        state, detail = BLOCKED, "Generate the packages first."
    else:
        state = NEXT
        detail = (f"{open_q} question{'s' if open_q != 1 else ''} to answer. "
                  "Approve stays locked while anything is marked Critical — usually a "
                  "missing column (fix it in Column mapping) or a value the whole "
                  "column needs.") if open_q else "Nothing left to decide — approve each package."
    add("Answer the questions and approve", state, detail,
        needs="" if built else "step " + str(len(out)))

    delivered = [k for k in core_pkgs if packages[k]["delivered"]]
    add("Send to the SAP import folder",
        DONE if len(delivered) == len(core_pkgs) else (NEXT if approved else BLOCKED),
        "Press “Send to SAP folder” on each approved package.",
        needs="" if approved else "approval")

    add("Check against SAP",
        (NEXT if sap_connected else ATTENTION) if delivered else BLOCKED,
        ("After SAP has imported, press “Check against SAP” on the package."
         if sap_connected else "Connect the brand's SAP sheet below first.")
        if delivered else "Comes after sending to SAP.",
        needs="" if delivered else "sending")

    # Side packages — useful any time after step 2.
    side = []
    for kind, label, need in (("pricing", "Pricing", "a price list (or prices in the order sheet)"),
                              ("attributes", "Attributes", "the order sheet"),
                              ("images", "Images", "pictures or picture links")):
        st = packages[kind]
        side.append(f"{label}: {'approved' if st['approved'] else st['state'].replace('_', ' ')}")
    add("Pricing, attributes and images",
        OPTIONAL if ready_to_build else BLOCKED,
        "Independent of the steps above — run them whenever their input is in. "
        + "; ".join(side) + ".",
        needs="" if ready_to_build else "steps 1–2")

    add("Mark live in B2B", DONE if job.b2b_uploaded else (TODO if delivered else BLOCKED),
        "Set it in B2B publication once customers can see it.",
        needs="" if delivered else "sending to SAP")
    return out
