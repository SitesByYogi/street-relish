# StreetRelish

StreetRelish is a delivery-planning application built around a real Falcon driver workflow.

## V1 goal

Turn a Falcon CSV into a usable daily operating plan:

**Import → Normalize → Plan → Load → Drive**

Current V1 capabilities:

- Falcon CSV ingestion
- Preserve imported source rows separately from StreetRelish planning data
- Normalize multiple delivery records at the same address into one stop
- ZIP/package grouping
- Heavy-package visibility
- Basic deterministic planning sequence
- Load-planning view
- Driver mode with navigation handoff

Planned next:

- Geocoding
- Geographic route optimization
- Map view
- Manual drag/reorder
- Smarter truck-loading order derived from route sequence
- Better package/barcode/detail views

## Local development

Requires Python 3.10+.

```bash
python app.py
```

Then open:

```text
http://127.0.0.1:8787
```

## Product rule

StreetRelish is intentionally focused on the core delivery workflow first. It is not attempting to become a generic fleet-management platform before the workflow is validated.
