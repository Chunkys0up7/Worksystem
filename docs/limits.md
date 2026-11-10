# What this build does not prove

A reference build earns trust by being exact about its edges. These are the
things a careful reader should not take from this repository.

## 1. It does not prove the lifecycle gets shorter

This is the central claim of the whole idea and no demo can prove it.

The gain comes from changes in Tier A and B moving faster because their
evidence is trusted. So the ceiling on the gain is the share of real work that
lands in A or B. That share is a fact about an organisation's change history,
not about this code. `python -m harness retro` measures it for any repository
and path map, and reports how much is Tier C only because nobody has mapped
the paths yet. Until that number exists for a real quarter, the size of the
benefit is unknown, and it could be small.

It also needs the review, approval and change-control process itself to be
redesigned around tiers. A tiered pipeline feeding an untiered approval board
saves nothing.

## 2. Key custody

Every control here that rests on a signature assumes the signer's key is held
where no person or agent can read it. The keys in `demo-keys/` are committed
in the open. With them, anyone can sign as CI.

What the build shows is the structure: distinct keys per identity, one writer
per fact type, signatures checked on write and on verify. It does not show a
KMS, an HSM or a workload identity system. Attack 18 is the honest picture of
what happens when a key leaks.

## 3. The anchor is a file

The hash chain proves internal consistency. It cannot, alone, prove nothing
was removed and the chain rebuilt. The anchor closes that gap only if the
party who can write the database cannot write the anchor. Here the anchor is a
file beside the database, so attack 8 succeeds. The real control is
object-locked storage written under a separate identity, plus reconciliation
against an independent record of what CI ran.

## 4. Disposal and legal hold

Each entry records its record class, a retain-until date and a dispose-after
date. Nothing disposes anything. An append-only hash chain and a disposal
ceiling pull against each other, and the design intent is not yet built:
personal data stays out of the chain (by reference only), and expired entries
are disposed by sealed segment, leaving a signed tombstone that carries the
segment's final hash so the chain after it still verifies. Legal hold is a
flag in the schedule and nothing more.

The day counts in `policy/records-schedule.yaml` are illustrative.

## 5. Two lints that are floors, not proofs

- **Personal data**: the ledger rejects payloads containing listed keys. It
  does not inspect values. A name in a free-text field passes.
- **Skill content**: release refuses skills matching patterns for network
  access, credentials and dynamic execution. Obfuscated code passes. The real
  control is running skill checks in a sandbox with no egress.

## 6. Concurrency and availability

Two writers appending at once: one fails on the sequence key and must retry.
That is correct but naive. There is no advisory locking, no high availability,
no recovery procedure for an entry that was stored but not anchored (the build
detects it and refuses further writes, which is the safe half). Harness-degraded
mode, for when the pipeline itself is down, is stage 5.

## 7. No live model has run

Every model in this repository is scripted: a small deterministic program
pinned in `policy/models.yaml` with kind "scripted", labelled wherever it
appears. The gateway, the tool box, three executors, the reviewer and the
conformance suite all run for real around those scripts. That shows the
controls sit around the executor and the model and do not depend on their good
behaviour, and that the harness records the same thing whichever executor did
the work.

It shows nothing about any real model or coding agent: not that one can do the
work, not that two are interchangeable in quality, not what it costs, not how
one behaves when a file it reads tells it to ignore its instructions. The suite
runs model portability on two scripted models and refuses to count it as a
pass. No live adapter ships, because network code that has never run would be a
claim this repository cannot back.

## 7a. No operating-system sandbox

An executor is given a private worktree with no signing keys in it. Nothing
stops the executor process reading outside that directory: attack 36 reads the
keys from the repository's own path. Hand-back validation sees what an executor
changed, never what it read or sent elsewhere. The control is a container with
only the worktree mounted, no credentials in its environment, and no network
route except the model gateway. Until then, the deny list is enforced after the
fact, at hand-back, and not before each action.

## 7e. A command-line agent brings its own model

The CLI executor runs a product that has its own model access. Its calls do not
pass through this build's gateway, so the pin and the budget are the product's.
Attack 46 shows it. The control is to point the product at the gateway's
endpoint. The same executor also cannot be refused before an action: its file
tools are its own, so only hand-back validation binds it.

## 7b. The one-time token proves delivery, not use

The token placed in the worktree copy of a skill shows the skill text reached
the executor. It does not show the model followed it. Whether the rules were
followed is what the paired checks decide.

## 7c. The harness is not yet built through its own contract

This repository's own changes are made by a coding agent working directly in
the tree, under SPEC-0001, checked by `python -m harness commits check` (every
commit cites a spec and stays inside its allowed paths) and by CI. They do not
go through a work order, a worktree and hand-back validation. The plan says the
harness governs all delivered code whoever wrote it. That is not true here yet.

## 7d. A new rule's check is red until the code lands

When an SME adds a rule and its paired check, the check fails until the code
implements the rule. The scenario models this as an overlay: the SME's change
first, the executor's change on top. On a real main branch the two must land
together, or the skill release must be staged, or main is red in between. This
build does not settle which. It is a design question for stage 5's release
manifest: the gate has to evaluate a stack of changes, not one.

## 7f. What the tower and the release do not show

- **An approval is a signature, not diligence.** Two named people signed. Whether
  they read the evidence is not measured. Sampling approvals against later
  outcomes is the control and is not built.
- **The mutation gate is small.** A handful of operators on one file of about
  eighty lines. It found a real gap here (an amount between zero and one was
  untested; a held-out case now covers it). On a large module it would be slow
  and its threshold would need tuning per module. Equivalent mutants count
  against the score, which is why the threshold is below one.
- **Held-out cases are only as held-out as their custody.** They are kept from
  executors. They are in the same repository as everything else, so a person
  with repository access can read them and tune a change to them.
- **Release reads the validated worktree, not git.** The plan says promotion
  happens only from git through the pipeline. A real pipeline with branch
  protection is needed for that. There is no canary, no online evaluation and
  no rollback.
- **Tier A does not merge automatically.** Auto-merge with sampling is the
  largest single source of lifecycle gain in the plan, and it is not built.
- **Degraded mode is drilled in tests, not in an outage.** The spool is a
  directory. Who may write to it during an outage is not controlled.

## 7g. What the runtime does not show

- **The engine is a hundred-odd lines, not a product.** It enforces the node
  contract, the pin and the single transaction. It has no retries, no timeouts,
  no parallel branches, no back-pressure. LangGraph, Temporal or an existing
  workflow engine would sit behind the same contract; none has been tried.
- **The only agent node explains.** It does not read documents, call tools or
  choose a route. So the hard questions about agents (injection through a
  document, a tool used wrongly, drift over a long case) are not exercised. The
  adversarial twin passes because the check it attacks is deterministic.
- **The explanation guard checks amounts and card names.** A draft that is
  wrong or misleading in words, with the right numbers, passes it.
- **The vault is a directory.** Encryption, access control and proof of erasure
  are not built. Erasure removes the values; backups are not considered.
- **Decision receipts hold references.** After erasure a decision cannot be
  re-explained from its inputs. That is the retention ceiling doing its job,
  but the floor has to be met first, and nothing here enforces the order.
- **One case, one process.** Two engines stepping the same case at once are
  serialised by the database write lock on SQLite and an advisory lock on
  Postgres, and the second would fail on the step key. That is safe and
  untuned.
- **No shadow traffic, no canary, no fairness monitoring, no retirement path.**

## 7h. What the defect loop does not show

- **Finding defects.** The defect here was found by a person asking what the
  service does with inputs no check covered. Nothing in this build clusters
  traces, watches for anomalies or proposes a defect.
- **A defect that does not reproduce.** It is recorded as not reproduced and
  work is blocked. What happens next (more evidence, close, escalate) is a
  process this build does not define.
- **The worse half of this defect went unnoticed in production.** A case with
  an infinite amount completed and produced a figure. Nothing halted. Only the
  crash was visible. A loop that starts from halted cases misses silent wrong
  answers; outcome monitoring is what finds those, and it is not built.
- **The return path is one rule.** A card drafted from traces needs its owner's
  sign-off. Drafting such a card, evaluating it against injected text, and
  refreshing the blueprint are not built.
- **No link to a work-tracking system.**

## 7i. What the journey does not show

- **Discovery is a file.** The blueprint, the slice and its outcome metric were
  written by hand for this build. The method in the plan (EventStorming, domain
  storytelling, bounded extraction from documents, an opportunity tree, a
  blueprint kept current from production) is not built. What is built is the
  record a slice must leave and the checks on it.
- **The outcome metric is recorded and never measured.** The journey ends
  without knowing whether the slice achieved what it was for. That measurement
  is what makes discovery value-driven, and it is the part missing.
- **The journey is a script of real steps, in a fixed order, on one machine in
  about forty seconds.** Real delivery has waiting, queues, disagreement and
  rework. None of the time that the plan aims to remove is present here to be
  removed, so the journey shows that the road exists, not how long it takes.
- **Main is simulated.** Between steps, "main moves on" by laying a scenario
  over a copy. There is no merge, no branch and no pull request.

## 8. The slice is small and synthetic

One blueprint, three moments, four cards, one skill, one decision service of
about forty lines. Real domains have rules that conflict, change mid-flight and
resist being stated as a deterministic check. The claim "every rule has a
paired deterministic check" is easy here. How far it holds across a real
domain, and what share of rules need judgement and therefore evals instead of
checks, is an open question this build does not answer.

The rules are invented. They are not any agency's, investor's or institution's
guidelines.

## 9. Path-based tiering is only as good as the map

The rulebook tiers by path, declared data class and cited obligation. A file in
a Tier A directory that in fact handles personal data is Tier A until someone
corrects the map. "Unmapped is Tier C" protects against missing entries, not
wrong ones. Measuring misclassification by sampling is not built.
