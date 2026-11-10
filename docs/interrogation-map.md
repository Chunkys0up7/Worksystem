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
| Review effort follows risk: the same pipeline asks for different things by tier. | Partial | `harness/change.py`, `python -m harness demo` | Tier C stops at "held for approval". The two-person approval step is stage 5. Tier A auto-merge with sampling is not built. |
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
| Runtime ledger entry and checkpoint in one Postgres transaction. | Not built | | Stage 6. |

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
| Procedure cards are checked. | Not built | Listed as pending in the skill manifest | Stage 6: runtime evals on the agent node. |
| Cards are refreshed from production traces. | Not built | | Stage 7. |

## 5. Decisions and separation of duties (ADR-030)

| Claim | Status | Where | How to attack it |
| --- | --- | --- | --- |
| A regulated figure comes only from a deterministic decision service. | Partial | `services/decision_income/calc.py`: exact arithmetic, names the cards applied | Nothing yet stops an agent computing the figure itself. That is the node contract, stage 6. |
| The author never judges its own work. | Partial | No identity in the executor role can write a tier, a check result, a hand-back verdict or a gate | Attack 33. Model-level separation (a different model reviews) needs a live model: SPEC-0001 task 9. |
| The launcher cannot approve. | Not built | | Stage 5. |
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

## 7. Not built yet

| Claim | Stage |
| --- | --- |
| A live model behind the gateway; a live coding-agent product as the CLI executor. | 4, needs credentials |
| Confidence tower: mutation score, held-out tests, fitness functions as blocking gates. | 5 |
| Two-person release for Tier C; harness-degraded mode. | 5 |
| Agent runtime: case-pinned skill versions, checkpoint and ledger in one transaction. | 6 |
| Defect loop: reproduce first, permanent regression test and eval; return path from traces. | 7 |
| Full attack pack across both programmes. | 8 |
