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

## 7. There is no agent in it yet

Stages 1 to 3 contain no model call. The "executor" is an identity that writes
one fact type. This is deliberate: the evidence layer has to be right before
anything is allowed to generate changes into it, and it shows the method does
not depend on a tool. But it means nothing here demonstrates an agent loading a
skill, honouring a deny list, or being swapped for another. That is stage 4.

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
