---
id: SPEC-0007
title: The console, a presentation layer that shows and triggers the build
status: approved
cites: [ADR-019, ADR-024]
allowed_paths:
  - "ui/**"
  - "harness/cli.py"
  - "conformance/journey.py"
  - "policy/path-map.yaml"
  - "policy/fitness.yaml"
  - "specs/**"
  - "attacks/**"
  - "tests/**"
  - "docs/**"
  - "README.md"
  - "CLAUDE.md"
  - "Makefile"
---

# SPEC-0007: The console, a presentation layer that shows and triggers the build

Approved by the owner on 2026-11-10 ("we need a UI, a presentation layer to
show and trigger what you built").

## Intent

Everything in stages 1 to 8 is reachable only from a command line. A peer who
wants to interrogate the idea should be able to press a button and watch the
real mechanism run: travel the journey, run the attacks, read the ledger and
edit it, tier a path of their own. The console is that surface.

It is presentation. Following ADR-024 it holds no control: no gate, tier,
identity or evidence lives in it, and removing the `ui/` directory changes
nothing the build proves.

Outcome metric: from a clean clone, `python -m harness ui` opens a page from
which the journey, every attack, the ledger edit and the tier check can be
triggered without typing a command. Number of results on the page that were
written by hand: zero.

## What is built

- `ui/server.py`: a fixed set of operations, each a call to a function the
  command line already calls, and an HTTP server bound to the loopback address.
- `ui/index.html`: one page, no build step, no outside request. Five views:
  journey, attacks, ledger, try it, limits.
- `python -m harness ui`: serve the console. `--export FILE` runs everything
  once and writes a self-contained page with the results embedded, labelled
  as a recording.
- `run_journey` takes an optional observer so the page can show each step as
  it closes. The observer is told; it cannot change the road.

## Acceptance criteria

| # | Criterion | Proven by |
| --- | --- | --- |
| U1 | The page triggers the real mechanisms: the journey, one attack, the tier check, the conformance suite return what the command line returns. | `test_tier_is_the_rulebook`, `test_one_attack`, `test_journey_from_the_page` |
| U2 | An entry edited with database-owner access is reported by verification, by entry, and the original can be put back. The ledger edited is the journey's throwaway one. | `test_edit_an_entry_and_verification_says_so` |
| U3 | The server answers only on the loopback address, serves one page and a fixed set of operations, and takes no command, module name or file path to write. | `test_only_fixed_operations`, attack 75 |
| U4 | A web page in another browser tab cannot trigger anything: a request that names another host, or a write without the console's header, is refused. | `test_another_origin_is_refused`, attack 75 |
| U5 | The page makes no request to anywhere but its own server. | `test_the_page_asks_nobody_else` |
| U6 | A recorded page says it is a recording, when and at which commit, and its content is what the run returned. | `test_a_recording_is_labelled_and_whole` |
| U7 | The page says every model is scripted, in every view and both modes. | `test_the_page_says_what_is_not_live` |
| U8 | The harness never imports the console, except at the command-line edge. | `test_the_harness_never_imports_the_console` |

## Stated limit

The console has no login. Any process on the machine that can reach the
loopback address can press its buttons (attack 76, LIMIT). It runs on the
published demo keys and writes only to throwaway directories, so there is
nothing to protect here. A console over a real ledger would need the
organisation's authentication in front of it and its own workload identity
with no write role.

## Not in scope

A hosted, multi-user version. Any control in the browser. Charts of delivery
time: the build has none to show (`docs/limits.md`, section 1).
