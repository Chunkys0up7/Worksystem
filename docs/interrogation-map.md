# Interrogation map

Every claim this build makes, where it is proven, and how to attack it.

Status values:

- **Proven**: runnable here, with a test or an attack that fails if the claim stops being true.
- **Partial**: the mechanism is built and tested; a named part is missing.
- **Limit**: this build does not stop the attack; the control that would is named.
- **Not built**: a later stage. Nothing here demonstrates it.

Attack numbers refer to `python -m harness attacks`.

## 1. The lifecycle argument

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| Lifecycle shortens only if review, approval and change control are redesigned around evidence and tiers. Generation is already cheap. | Not built, and not provable by a demo | `docs/limits.md` | Ask for the retro-tier mix of a real quarter. If most changes land in Tier C, the gain is small whatever the tooling. |
| Review effort follows risk: the same pipeline asks for different things by tier. | Proven for the gates | `policy/tower.yaml`, `harness/release.py` | Tier C needs the full tower and two people; A and B do not. Tier A auto-merge with sampling is not built. |
| The retro-tier mix is the ceiling on lifecycle gain. | Partial | `harness/tiering/retro.py`, `test_retro_over_a_real_history` | The tool is real; the number is only as good as the path map you give it. Run it on your own repository. |

## 2. Risk tiers (ADR-020)

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| Tier is set by deterministic rules, never by the authoring agent. | Proven | `harness/tiering/rulebook.py` has no tier input; `policy/identities.yaml` lets only `ci-tiering` write `tier.assigned` | Attacks 1, 2. Find any parameter through which an author states a tier. |
| A path nothing maps is Tier C until mapped. | Proven | `classify`, `test_unmapped_or_escaping_paths_are_tier_c_by_default` | Attack 12. Try case changes, near-miss names, absolute paths, Windows separators. |
| A change takes the highest tier it touches. | Proven | Property tests: `test_change_tier_is_the_highest_single_path_tier`, `test_adding_paths_never_lowers_the_tier` | Attack 11. Find two paths whose combination tiers lower than either alone. |
| Rule order cannot change the answer, and a broad rule cannot lower a narrow one. | Proven | `test_order_does_not_matter`, `test_rule_order_in_the_map_does_not_matter` | Reorder `policy/path-map.yaml` and re-run. |
| The skill map is a declared input: a skill with unknown consuming nodes fails the check, it does not default down. | Proven | `classify` raises `TierCheckFailed`; `test_skill_without_map_entry_or_nodes_fails` | Attack 14. |
| The rulebook cannot be lowered from inside the change it is tiering. | Proven | `policy/**`, `harness/tiering/**` and the pipeline are Tier C in the path map | Attack 13. |
| Every tier decision is reproducible. | Proven | The ledger entry carries per-path reasons and the rulebook hash | Change a policy file and confirm the hash moves. |
| Misclassification is measured. | Not built | | Needs sampled human re-tiering against the rulebook's answer. |
| Tiering by data class and obligation, not only by path. | Partial | Data-class floors and an obligation floor are implemented | Data classes are declared per path in the map, not derived from the code. Legacy code with no map has no input other than "unmapped". |

## 3. Evidence layer (durable layer as revised by Plan v2; sanity-check S17)

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| The harness writes evidence through one API, under a CI identity, and it fails closed. | Proven | `harness/ledger/api.py`, `harness/gates.py` | Attack 10. Find a path where a gate passes without a ledger entry. |
| One authoritative writer per fact type. | Proven | Enforced when policy loads | Attack 3. |
| A claimed identity is not enough; each identity signs with its own key. | Proven | `Ledger.append`, `test_signature_covers_payload_and_refs` | Attack 2. |
| A recorded entry cannot be edited without detection. | Proven | Property test `test_any_single_field_edit_is_detected` edits any field of any entry | Attacks 4, 5. |
| Entries cannot be removed, reordered or truncated without detection. | Proven, given an anchor the database owner cannot write | `Ledger.verify`, `FileAnchor` | Attacks 6, 7. |
| Hash chain plus object lock. | **Limit** | The anchor is a file, standing in for object-locked storage | Attack 8 rebuilds the chain and the anchor together and is not detected. |
| Daily completeness reconciliation. | Partial | `Ledger.reconcile`, rules in `policy/records-schedule.yaml` | It reconciles the ledger against itself. Reconciling against CI's own run records, the independent source, needs a real pipeline (stage 5). |
| Personal data is recorded by reference only. | Partial | Key lint in `find_personal_data`; `_ref` keys allowed | Attack 9. It is a key lint: put a name in a field called `detail` and it passes. |
| Retention is a window, with record class fixed at write time. | Partial | `policy/records-schedule.yaml`; every entry carries `retain_until` and `dispose_after` | Disposal is not built. Neither is legal hold. See limits. |
| The store is a contract, not a product. | Proven | SQLite and Postgres pass the same contract tests | Run with `LEDGER_PG_DSN` set. |
| Runtime ledger entry and checkpoint in one database transaction. | Proven | `Ledger.transaction`, tested on SQLite and Postgres | Attack 60. |

## 4. Knowledge as skills, line of sight (ADR-025, ADR-011)

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| Every rule in a skill has a paired deterministic check. | Proven | `validate` in `harness/knowledge/model.py` | Attack 20. Add a RULE or CALC card with no check. |
| The check reads the card, so code and card cannot drift silently. | Proven | `knowledge/skills/income-calculation/checks/check_income.py` | Attack 15. Change a constant in the service. |
| CI re-runs the checks itself; the executor's report is not the evidence. | Proven | `harness/checks.py` runs under `ci-gates` and binds each result to the skill hash and the governed code hash | Attack 15. Have the executor claim success. |
| Checks run against the source as it stands, in a fresh process, never against cached bytecode. | Proven | `harness/checks.py` gives the runner an empty private bytecode cache; `test_checks_ignore_stale_bytecode` | Attack 16. Edit a governed file keeping its size and timestamp, with the old compiled file beside it. |
| Skills are a signed, content-hashed release unit, verified before anything runs them. | Proven | `harness/knowledge/skills.py` | Attacks 17, 19. Edit, add or remove any file in the skill. |
| Signing proves a skill was reviewed. | **Limit** | It proves who released it | Attack 18. With the publisher key, a weakened check is re-signed. The control is two-person review of a Tier C change plus key custody. |
| A skill makes no network call and holds no credential. | Partial | Pattern lint | Attack 19. A pattern lint is a floor: obfuscated code passes it. The real control is a sandbox with no egress. |
| The ID chain answers "where is this decision implemented, and is it passing" and "why does this code look like this". | Proven on the scenario | `harness/chain.py`, `test_chain_is_closed_through_change` | Decision, skill, spec, change, check and ledger links all resolve for PD-0002. PD-0001 predates specs and the chain says so. |
| Missing links fail the build. | Proven | `python -m harness validate`, `test_broken_links_are_found` | Break any reference between moment, card, decision, skill, check or skill map. |
| The blueprint marks regulated moments, each with obligations and data classes. | Proven | `knowledge/blueprint/`, validation | Attack 21. |
| Procedure cards are checked. | Proven | By an eval against the runtime, named in the skill manifest | See section 8. |
| Cards are refreshed from production traces. | Not built | Only the owner sign-off rule exists | See section 9. |

## 5. Decisions and separation of duties (ADR-030)

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| A regulated figure comes only from a deterministic decision service. | Proven for the graph contract | `services/decision_income/calc.py`, `runtime/engine.py` | Attacks 56, 57. See section 8. |
| The author never judges its own work. | Partial | No identity in the executor role can write a tier, a check result, a hand-back verdict or a gate | Attack 33. Model-level separation (a different model reviews) needs a live model: SPEC-0001 task 9. |
| The launcher cannot approve. | Proven | `harness/release.py` | Attack 50. |
| Promotion happens only from git, through CI. | Not built here | `.github/workflows/ci.yml` runs the gates | Branch protection and the release manifest are stage 5. |

## 6. The executor contract (ADR-002, ADR-024, ADR-028; SPEC-0001)

Built without any live model: specs, roles, work orders, hand-back validation,
the model gateway, the tool box, three executors, advisory review and the
conformance suite. Every model is scripted and labelled. Run it with
`python -m harness conformance loop --show-run`.

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| Work starts from an approved spec that cites decisions in the register. | Proven | `harness/knowledge/model.py`, `build_work_order` | Attacks 31, 32. |
| Several executors can exist, each with its own identity and key, and none can write a control fact. | Proven | Roles in `policy/identities.yaml`; one role per fact type, one role per identity | Attacks 33, 34. |
| The harness builds the work order; the executor gets a private worktree without the signing keys. | Proven | `harness/workorder.py`, `test_work_order_contents` | Look for anything on `policy/deny.yaml`'s read list inside a worktree. |
| Skill-map load: evidence lists exactly the released skills the work order resolved. | Proven | `validate_handback`, `test_clause_skill_map_load` | Attacks 28, 30. |
| The skill text reached the executor. | Partial | A one-time token in the worktree copy of the skill, read through the tool box by the loop executor | Attacks 29, 44. The token proves delivery, not use. Use is proven by the paired checks. Not shown with a live model. |
| A change outside the spec's allowed paths is rejected, whatever the executor says it touched. | Proven | What changed is derived from a snapshot, not from the claim; `test_allowed_paths_bind_even_off_the_deny_list` | Attacks 22, 25. |
| The deny list holds even when a spec is too broad. | Proven | `test_deny_list_binds_even_when_a_spec_allows_everything` | Attack 26. |
| CI runs the released skill and its own harness, never the worktree's copy of either. | Proven | `harness/check_runner.py` loads both before the worktree is importable; `test_checks_use_the_released_skill_not_the_worktree_copy` | Attacks 23, 24. |
| Evidence is re-derived. The executor's claim is recorded as a claim and never used as a result. | Proven | `harness/delivery.py`; differences are written as `evidence.mismatch` | Attack 27. |
| The executor cannot state its tier or skip checks. | Proven | There is no input for either; stating them is recorded as a mismatch | Attack 27. |
| Every proposed change ends in a gate result, including a blocked or crashed one. | Proven | `test_an_executor_that_crashes_ends_in_a_blocked_gate`, reconciliation rules | Kill the executor half way. |
| Commits cite a spec and stay inside it; skill-governed code names its released skill versions. | Proven | `harness/trailers.py`, `python -m harness commits check`, run by CI on this repository | Attack 35. |
| A recorded executor is labelled as recorded everywhere, and a stale recording cannot pass. | Proven | `test_recorded_executor_is_labelled`, `test_a_stale_recording_cannot_pass` | Attack 30. |
| A partly judged executor is never called conformant. | Proven | `conformance/suite.py`: "n/a" is counted apart from "pass" | Read the verdict line for the recorded executor. |
| An executor cannot read what it was not given. | **Limit** | The keys are not in the worktree, and nothing stops a process reading outside it | Attack 36. Needs an operating-system sandbox. |
| The controls above were tested, not assumed. | Proven for hand-back validation | Six rules in `validate_handback` were each disabled in turn; a test failed every time | Disable one and run `python -m pytest tests/test_stage4.py`. A full mutation gate is stage 5. |
| The executor can be swapped: gates and evidence do not change. | Proven for the harness side | Three executors that share no code path (recorded, tool loop, command-line) leave identical control facts; `test_swap_drill` | Attack 45. None of the three is a live agent. Equality of live agents is not shown. |
| A model is reachable only through the gateway, by a pinned id, in an approved role. | Proven | `harness/gateway.py` | Attack 37. |
| Budget caps fail closed, and what was used is recorded by the gateway, not the executor. | Proven | Calls, tokens and time, shared across a piece of work | Attacks 38, 43. |
| The deny list is enforced before an action when the executor works through the tool box. | Proven | `harness/tools.py`; refused attempts are recorded in the hand-back verdict | Attack 39. |
| Deterministic gates block; AI review advises. | Proven | `harness/review.py`: advice is recorded as non-binding and the control plane records the same with or without it | Attacks 40, 41. |
| The author never judges its own work. | Proven for model families | A reviewer from the author's model family is refused | Attack 42. |
| The skill token is one-time. | Proven | A new token per work order | Attack 44. |
| Model portability: one executor, two model families, same gates. | Not shown | The mechanism runs on two scripted models and is deliberately not counted as a pass | Needs two live models. |
| A command-line agent's model calls are pinned and budgeted. | **Limit** | It brings its own model access | Attack 46. Point the product at the gateway. |
| Any of this holds with a live model. | Not shown | No live model has run in this repository | Register an adapter for a pinned model and run `python -m harness conformance loop`. |

## 7. The confidence tower and release (ADR-013, ADR-020; SPEC-0003)

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| No number, no merge: each gate a tier requires records a number and a threshold. | Proven | `harness/tower/`, `policy/tower.yaml`; `tower.result` entries by the gates role | Remove a skill's held-out cases: Tier C has no number and blocks (`test_no_number_no_merge`). |
| Review effort follows risk: Tier A and B need fitness only; Tier C adds held-out cases, mutation and two people. | Proven | `test_tower_gates_by_tier` | Change `policy/tower.yaml`. That change is itself Tier C. |
| Architecture rules are gates, not prose. | Proven | `policy/fitness.yaml`, `harness/tower/fitness.py` | Attack 48. Import an adapter into the harness, or use a float in a decision service. |
| A change that fits the visible checks without implementing the rule is caught. | Proven | Held-out cases under `heldout/`, never copied into a worktree | Attack 47. |
| The checks would notice wrong code. | Proven on the slice | AST mutation of the changed critical module; score against a threshold | Attack 49. The operators are few; see limits. |
| Tier C is released by two people, neither the launcher, each approval bound to the gate result released. | Proven | `harness/release.py` | Attacks 50, 51, 52. |
| No agent or pipeline identity can approve. | Proven | Roles | Attack 53. |
| A release seals a manifest and the stored release must match it. | Proven | `release.manifest`, `verify_stored` | Attack 54. |
| A release refuses on a damaged ledger. | Proven | `test_release_refuses_twice_and_refuses_on_a_damaged_ledger` | Edit an entry, then release. |
| Degraded mode waives nothing. | Proven | `harness/degraded.py`: fixed gates, two signers, signed spool record, back-fill, late flag, overdue report | Attack 55. |
| An approval means the approver looked. | **Limit** | It means a named person signed | Nothing here measures diligence. Sampling approvals against outcomes is not built. |
| Promotion happens only from git, through the pipeline. | Not built | The release reads the validated worktree | Needs a real pipeline with branch protection. |
| Tier A merges automatically, with sampling. | Not built | | |
| Canary, online evals and automatic rollback. | Not built | | |

## 8. The agent runtime (ADR-027, ADR-030; SPEC-0004)

Every release these claims rest on went through the harness first: delivered,
gated, approved by two people, sealed. Every model is scripted.

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| The checkpoint and the ledger entries for a step are one database transaction. | Proven, on SQLite and Postgres | `Ledger.transaction`, `runtime/engine.py`; `test_checkpoint_and_ledger_commit_together` | Attack 60. |
| Checkpoints and ledger entries reconcile one to one. | Proven | `Engine.reconcile` | Insert a checkpoint row by hand. |
| A checkpoint edited behind the engine is not trusted. | Proven | Each step's ledger entry carries a hash of the state it left | Attack 66. |
| A regulated figure comes only from the decision service. | Proven for the graph contract | Only the calculate node may write the result; a node writing outside its contract halts the case | Attacks 56, 57. |
| Anything an agent says about the figure is checked against the receipt. | Proven for amounts and card names | `runtime/income.py: guard`; a failed draft is replaced by a template and the trip recorded | Attack 58. A draft that is wrong in words, not numbers, passes. |
| A case uses the versions pinned when it started; a new release reaches new cases only. | Proven | Releases stored by version; the pin is recorded in `case.started` and in each receipt | Attack 61. |
| A case moves version only by an approved, recorded migration. | Proven | `Engine.migrate` needs a `migration.approved` from an approver | Attack 62. |
| The pinned release is what runs. | Proven | The service is loaded from the release by path, in a fresh process, and checked against its sealed manifest first | Attacks 63, 64. Attack 64 succeeded during the build. |
| Conflicting documents go to a person and are not calculated (PROC-0001). | Proven for a deterministic check | `runtime/income.py: conflict`, and calculate refuses again as a second line | Attack 59. |
| A procedure card is checked, by an eval with adversarial twins. | Proven | `runtime/evals.py`; `eval.result` closes the PROC-0001 link in the chain | Break the conflict node: the eval fails (`test_the_eval_fails_when_the_procedure_is_broken`). |
| A routed case is durable, and only a case reviewer resolves it. | Proven | A new engine instance resumes it | Attack 65. |
| Personal data is by reference and can be erased without breaking the record. | Proven | `runtime/vault.py`; amounts appear in no ledger entry and no checkpoint | Attack 67. |
| A model sees a class of data only if its pin approves it. | Proven | The gateway checks `data_classes` on the pin | Attack 68. |
| An agent that reads documents resists an instruction inside one. | Not shown | The check that matters is deterministic and never reads the note | Needs a live agent and a red-team corpus. |
| Shadow and canary exposure, fairness monitoring, agent retirement. | Not built | | |

## 9. The defect and change loop (ADR-015; SPEC-0005)

The defect is real. The synthetic decision service failed on an amount that was
not a number, and in one path accepted an infinite one and returned a figure.
It was found by asking what the service does with inputs no check covered.

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| A defect cites the ledger entries that show it, and carries no personal data. | Proven | `harness/defect.py` | Attack 71. |
| Reproduce before fixing: a spec that fixes a defect starts no work until the gates role has reproduced it against the released version. | Proven | `reproduction.result`; the check at the top of `run_work_order` | Attacks 69, 70. |
| A defect leaves a permanent regression check and held-out cases. | Proven | The skill re-released with the check; held-out cases added | `test_regression_check_is_in_the_skill`. |
| No out-of-band hotfix: the fix takes the usual road. | Proven | Tier C, six checks, the full tower, two people, a sealed release | Attack 72: the correct fix, copied over the stored release, is not run. |
| A fix reaches new cases only. | Proven | Pins | Attack 74. |
| Knowledge drafted from production traces needs its owner's sign-off. | Proven | Validation on a card's `provenance` | Attack 73. |
| Defects are found. | Not shown | This one was found by a person asking a question | Trace clustering and anomaly detection are not built. |
| Traces refresh the cards and the blueprint. | Not built | Only the sign-off rule exists | |
| The defect is linked to the work-tracking system. | Not built | | |

## 10. Discovery, and the whole road (ADR-011, ADR-019; SPEC-0006)

`python -m harness journey` travels the road once: a slice is chosen, a
decision becomes a rule and a check, a spec is approved, an executor builds the
change, the tower proves it, two people release it, cases run on it, something
goes wrong, the defect is reproduced and fixed by a different executor, and the
fix reaches new cases only. Each step runs the real mechanism and lands on one
ledger. Every model is scripted.

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| A slice records its outcome metric, obligations and data classes before go or kill, and its expected tier is derived, not chosen. | Proven for the record | `knowledge/slices/`, `slice_tier`, `slice.decided` by the product owner | Remove an obligation a regulated moment carries: validation fails (`test_slice_broken_links_fail_validation`). |
| The two programmes share one evidence layer and meet at one interface. | Proven on the slice | One ledger holds the delivery facts and the runtime facts; the interface is the sealed release | Read the journey's ledger: 16 roles, no entry outside its writer's role. |
| The road is one road: nothing is narrated. | Proven | Every stage of the journey writes ledger entries; a spec's first entry is the work order that cites it | `test_every_stage_lands_on_the_ledger`. |
| The executor can change mid-journey. | Proven for the harness side | The change is built by the tool loop and the fix by the command-line executor | `test_two_executors_two_releases`. |
| Discovery is service-design-led: EventStorming, domain storytelling, bounded extraction from documents, an opportunity tree. | Not built | The blueprint and the slice were written by hand | |
| Go or kill is driven by the outcome metric, measured. | Not built | The metric is recorded and never measured | |

## 11. The console (ADR-024; SPEC-0007)

`python -m harness ui` serves one page on the loopback address. Every button
calls a function the command line already calls. The console holds no control:
delete `ui/` and every row above still holds. `--export` writes the same page
with one real run embedded, labelled as a recording.

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| What the page shows is what the mechanism returned. | Proven | `ui/server.py`: each operation is one call; the tier answer equals the rulebook's, the journey's ledger is read through the ledger API | `test_tier_is_the_rulebook`, `test_journey_from_the_page`. Edit an entry from the Ledger view and read what verification says. |
| The console does only fixed things: one page, a fixed set of operations, no command, module name or file path to write. | Proven | The routing tables in `make_handler`; input checks in `Console` | Attack 75. `test_only_fixed_operations`. |
| A web page in another tab cannot drive it. | Proven | Loopback bind, the `Host` check, and a header a cross-site page cannot send without permission the console never gives | Attack 75. `test_another_origin_is_refused`. |
| The page asks nobody else for anything. | Proven | No script, style, font or image from outside; the server also sends a content security policy | `test_the_page_asks_nobody_else`. |
| The edit in the Ledger view touches only the journey's throwaway ledger, and can be undone. | Proven | `Console.tamper`, `Console.restore`; the append-only trigger is put back | `test_edit_an_entry_and_verification_says_so`. |
| A recording says it is one, and holds what the run returned. | Proven | `Console.record`, `render_page` | `test_a_recording_is_labelled_and_whole`. |
| The harness does not depend on the console. | Proven | FIT-003 forbids the import outside the command-line edge | `test_the_harness_never_imports_the_console`. |
| Only the right people can press the buttons. | LIMIT | There is no login | Attack 76: any local process can. See `docs/limits.md`, section 7j. |
| The page is accessible and has been tested with assistive technology. | Not shown | Keyboard focus is visible and the views are plain HTML; no audit was done | |

## 12. Not built yet

| Claim | Stage |
| --- | --- |
| A live model behind the gateway; a live coding-agent product as the CLI executor. | 4, needs credentials |
| Every claim in both programmes has an attack. | 8, partly: 76 attacks; the claims marked Not built and Not shown have none |
