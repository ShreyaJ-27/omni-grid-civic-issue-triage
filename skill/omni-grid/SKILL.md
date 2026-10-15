
---
name: omni-grid
description: Triage geotagged civic complaints using location data and optional photo or video evidence. Rank incidents with explainable scores and flag potentially unverified complaint closures.
license: MIT
compatibility: Python 3.10+
metadata:
  version: "0.1.0"
---

# Omni-Grid

## When to use
Use this skill when a user needs to prioritise civic complaints,
identify geographic hotspots, or audit resolved complaints.

## Workflow
1. Check the environment and available inputs.
2. Run the complaint triage pipeline.
3. Read the generated summary.json.
4. Verify closure evidence when supplied.
5. Explain priority scores, evidence, uncertainty, and findings.

## Safety and trust
- Never claim fraud based only on an automated flag.
- Identify uncertainty and cases requiring human review.
- Do not identify people in submitted media.
- Do not expose secrets or private raw media.
