# Amount range violation

Use this runbook when a Data Quality validity check reports values outside the accepted
numeric range.

1. Confirm the failed check ID, expected minimum/maximum, unexpected count and source
   dataset using the current incident citations.
2. Quarantine invalid rows; do not publish them to the trusted output.
3. Compare the producer contract and the current pipeline version before deciding whether
   the range changed intentionally.
4. Retry only after the input is corrected or an approved contract version is deployed.
5. Verify the same check passes and that volume/completeness checks did not regress.

Automatic mutation of production data is not allowed. Escalate when evidence is missing or
the contract change is ambiguous.
