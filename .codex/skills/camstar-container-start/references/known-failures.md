# Known Start Failures

Use this reference to recognize verified failure patterns. Revalidate environment-specific values before reuse.

## Product requires Revision

**Symptom:** Start or MfgOrder rejects a Product reference because Revision is missing.

**Cause:** Product is a revisioned reference.

**Resolution:** Always send both Product `name` and `revision`. Do not add a second Product object through differently cased `body_json`.

## Owner requires input

**Symptom:** `Field "Owner" requires input`.

**Resolution:** Query `Details.Owner` through Start `RequestSelectionValues`; do not guess common Owner names. Pass the selected reference as `details.owner`.

**Environment observation:** On 2026-10-03, the current context returned only `PRODUCTION`. Treat this as an observation, not a permanent default.

## StartReason requires input

**Symptom:** `Field "Start Reason" requires input`.

**Resolution:** Query `Details.StartReason` through Start `RequestSelectionValues`. Pass the selected reference as `details.startReason`.

**Environment observation:** On 2026-10-03, the current context returned only `NORMAL`. Preserve the server's canonical spelling and revalidate before relying on it.

## AutoNumberRule write access denied

**Symptom:** `Write access denied on field AutoNumberRule`.

**Cause:** The property appears in the StartDetails OpenAPI schema, but runtime access control does not allow this server's clients to set it directly.

**Resolution:** Do not send `details.autoNumberRule`. Resolve `Details.AutoNumberRule` through `RequestSelectionValues`; if a contextual rule exists, request automatic numbering with `autoNumber=true`. Otherwise use an explicitly approved serial-number pattern or configure the Level outside this workflow.

**Enforcement:** Since 2026-10-08, `container_start` rejects `details.autoNumberRule`, omits it for contextual automatic numbering, and has a regression test for this invariant.

## AutoNumberRule or ContainerName does not exist on Start

**Symptom:** The server says `AutoNumberRule` or `ContainerName` does not exist on type `shopfloor.Start`.

**Cause:** A StartDetails field was placed at the root Start object, commonly through incorrectly shaped `body_json`.

**Resolution:** Put both fields under `details`. Do not keep retrying alternative root field names.

## Numbering rule exists but Start returns no rule

**Symptom:** A rule appears in Modeling, but `Details.AutoNumberRule` returns no selection values.

**Cause:** Global existence does not establish association with the selected Container Level or Start context.

**Resolution:** Inspect the Level's `ContainerNumberingRule` and the complete Start selection context. Do not force the global rule into Start.

**Environment observation:** On 2026-10-03, `BOX.ContainerNumberingRule` was null and `Details.AutoNumberRule` returned zero values for the referenced PACK order context.

## Batch stopped after one test Container

**Symptom:** One real Container is created during field discovery, then the remaining batch is blocked by a safety threshold.

**Cause:** Discovery used the production Start endpoint before total-count approval and read-only preflight were complete.

**Resolution:** Determine total count and perform all read-only validation first. If confirmation is required, obtain it before the first write. Resume only after checking whether the initial serial already exists.

## Ambiguous success after timeout

**Symptom:** The client times out without a reliable response.

**Risk:** Retrying may duplicate or conflict with a transaction that committed after the timeout.

**Resolution:** Query the intended Container by name and Level before retrying. Classify the outcome as ambiguous until state is verified.

## Movement eligibility was assumed

**Symptom:** A completion message promises MoveIn or MoveOut without checking configuration.

**Resolution:** Validate Container Level and current state before promising a follow-up transaction.

**Environment observation:** On 2026-10-03, the `BOX` Level reported `AllowMove=false` and `IsNameUnique=false`. Include Level in later references and revalidate movement eligibility.
