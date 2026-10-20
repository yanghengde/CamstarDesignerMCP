# WCF generation scope investigation — 2026-10-20

The isolated worker replaced the vendor executable configuration with a minimal
LogLevel/startup configuration. This dropped the installed `WCFGenerator` section,
including the `AllServices` method exposure rules. `IsGenerateAll=true` retains
all services but still needs those rules to handle inherited method exposure.

A read-only `Runner.Init()` comparison using the same compiled MDB found:

| Profile | Exposed services | Exposed method entries |
| --- | ---: | ---: |
| Previous isolated worker configuration | 795 | 114786 |
| Installed WCFBuilderLauncher.exe.config | 795 | 7648 |
| Corrected private worker configuration | 795 | 7648 |

Method entries are counted per service and include inherited entries; they are
not a count of unique method names. The scope comparison did not call
`Runner.Generate()`, deploy files, modify the MDB, or alter the active worker.
It does not establish a wall-clock speedup or Portal compatibility by itself.

The correction copies the installed `WCFBuilderLauncher.exe.config` into the
private worker configuration and changes only its private LogLevel to 0. Missing
exposure rules cause a clear failure rather than silently returning to the
expanded scope. Generated worker output also reports the exposed method count.

Installed source profile:
`C:\Program Files (x86)\Camstar\InSite Administration\WCFServicesGenerator\WCFBuilderLauncher.exe.config`.

The original generation kept its already-staged scripts and executable config
and finished naturally. The correction applies to newly staged jobs.

## Measured full generation

| Run | Duration | Generated services | Data contracts |
| --- | ---: | ---: | ---: |
| Original worker, original compiled design | 20 min 18 sec | 798 | 2592 |
| Corrected worker, isolated maintenance-field repair | 82.08 sec | 798 | 2592 |

The corrected run includes staging the private worker executable, contract
preflight and client/server field checks. This is approximately 14.8 times faster
in this one local comparison, not a timing guarantee. Both use the full service
scope; the repair adds the previously missing ProductChanges field, so the two
MDBs are not byte-identical. The independent exposure comparison above uses the
same MDB for all three profiles.

Generated client: 25,192,448 bytes. Generated server: 9,804,800 bytes.
Both contain ExProductDescription on their ProductChanges contracts. No partial
service package or relaxed field validation was used.

Isolated benchmark artifacts:
`data/designer/artifacts/172c5bf76e1f4514891fd39d10eaad63/`.
The benchmark does not create progress receipts or mark the current workflow
complete. It has not been deployed to Portal or published to the database.

## Contract validation and Portal fields

The original job completed generation at 19:49:55 (started at 19:29:37), then
correctly failed field verification: Product contained ExProductDescription but
the maintenance ObjectChanges contract ProductChanges did not. The original
Portal and database were not changed by this investigation.

The worker now checks the generator's reachable contract graph before emitting
assemblies whenever expected fields are supplied. This detects missing fields,
including inherited fields, without waiting for full generation. The same
expectations are checked in both the completed client and server assemblies.
The original missing-field input was rejected in 6.64 seconds end to end.

New add_field operations may explicitly set portal_editable=true. The vendor
bridge resolves MaintenanceCDO, ObjectToChange and ObjectChanges links instead of
guessing object names, and prepares the matching readable/writable maintenance
field. The field is shown in the confirmation preview, counted as another create,
and reloaded after save. Missing links and conflicting fields block the plan.
No second SQL column is created for the maintenance scratch field.

This prepares contracts only. Database publication, paired DLL deployment,
Portal Studio binding, and real read/write tests remain distinct steps.

The isolated repair draft is
`data/designer/artifacts/ec7f091009764207a827637b0544c2e5/manifest.json`.
It adds only ProductChanges.ExProductDescription, using the original field type;
it does not recreate Product.ExProductDescription. Publish the reviewed metadata
repair and deploy matching assemblies before Portal acceptance. The running app
must be restarted to load the Python tool/prompt changes; no running app was
restarted as part of this optimization.

Contract preflight reads internal generator metadata APIs because this installed
version does not expose the contract cache publicly. Unsupported APIs fail
explicitly; revalidate this adapter when upgrading the generator. It never
substitutes for the completed assembly checks or Portal runtime verification.

## Current project correction applied

The user's current project was subsequently repaired through the official
metadata API, saved/reloaded, exported, and copied to its fixed Designer path.
The previous published release was backed up. The new manifest is
`data/designer/artifacts/2f4e53c5f4e24a29b60badeea5271248/manifest.json`.
It adds ProductChanges.ExProductDescription using ExProductDescString200,
readable/writable, without a second database column.

Official Update Database completed and verified the maintenance field against
localhost/CamstarPRD. Publication receipt:
`data/designer/artifacts/40fcb2aab4c54a5090921d1c6554d3c9/publish_result.json`.

The normal SQL Agent WCF pipeline then generated 798 services and 2592 data
contracts in 88.87 seconds, including transfer and verification. It exposed a
second validation bug: Windows output `server\\Bin` was compared case-sensitively
against `server/bin`. Verification now uses case-insensitive Windows path
semantics while still requiring both assemblies. Added tests cover backslashes,
case variants, and actual missing assemblies.

All fetched artifact hashes and both client/server field checks were revalidated
against the official worker receipt, then a verified DLL archive was prepared.
The current task has a real successful stage-9 receipt; the failed receipts remain
in its audit history. Generation receipt:
`data/designer/artifacts/7549ced979c54407999fe3affe189b0f/generation_result.json`.
Portal deployment and actual page read/write acceptance remain outstanding.

The attempted restart of the older running API process was rejected by automatic
approval with `blocked by policy`; the server remained running. Restart the app
in its original terminal to load the updated Python validation logic for future
generation attempts. The completed current-task receipt survives the restart.
