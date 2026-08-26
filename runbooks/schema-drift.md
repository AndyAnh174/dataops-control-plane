# Schema drift

Use this runbook when a required field is missing, renamed or has an incompatible type.

1. Cite the current Data Quality report and commit diff that identify the affected field.
2. Compare the producer schema with the version expected by the pipeline.
3. Quarantine the incompatible batch and pause publication to trusted outputs.
4. Prefer a backward-compatible producer fix; otherwise deploy an approved consumer schema
   migration or roll back the breaking version.
5. Re-run schema, completeness and uniqueness verification before resolving the incident.

Do not infer a rename from semantic similarity alone. A current-run schema report or sample
manifest is required evidence.
