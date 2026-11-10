# Working in this repository

This is a reference build of an evidence-led, tiered agentic delivery
lifecycle. Its audience is peers who will try to break it. Read
`docs/interrogation-map.md`, `docs/limits.md` and `docs/stages.md` first.

## Commands

```
python -m harness validate   # every link resolves, every skill verifies
python -m pytest -q          # tests (set LEDGER_PG_DSN to add Postgres)
python -m harness attacks    # the attack pack
python -m harness journey    # the whole road, discovery to change, on one ledger
python -m harness demo       # one pipeline, three tiers
python -m harness conformance loop        # the executor conformance suite (also: recorded, cli-stub)
python -m harness commits check           # every commit cites a spec and stays inside it
python -m harness tier --paths <paths>    # what tier is this change
```

validate, pytest, attacks, demo, conformance and journey must pass before any
commit.
Every commit message carries a `Spec:` trailer (see `policy/commits.yaml`), and
`commits check` must pass after it.

## Rules for every change

1. **Spec first.** Work comes from a spec in `specs/`, one per stage. Run
   `python -m harness spec` to list them. Cite the spec in every commit.
2. **Deterministic things are real.** No mocked gate, ledger, tier or check.
   A model call may be recorded or stubbed only if it is labelled as such
   wherever it appears.
3. **Every new claim gets three things**: a test, an attack in
   `attacks/pack.py`, and a row in `docs/interrogation-map.md`. If the build
   does not stop an attack, add it as a LIMIT row and to `docs/limits.md`.
   Never hide a gap.
4. **Controls never live in a tool.** Gates, tiers, identity and evidence sit
   in `harness/` and `policy/`. No vendor SDK is imported by anything on a
   control path. Adapters sit behind a contract with a conformance test.
5. **You do not set tiers, weaken checks or edit released skills to make a
   change pass.** A skill change is an SME change: edit, bump the version,
   re-release with `python -m harness skill publish`, in its own commit.
6. **Changing `policy/`, `harness/ledger/`, `harness/tiering/`,
   `harness/gates.py` or `.github/workflows/` is Tier C.** Say so in the commit
   message and explain why the change is needed.
7. **New paths get a path-map entry** in the same change. Unmapped paths are
   Tier C by default; check with `python -m harness retro`.

## Writing rules

- No em-dashes or en-dashes anywhere in the repository.
- Never name any employer, client, firm or supervisor. The frame is "a
  federally supervised, heavily regulated financial institution".
- The service slice and all its rules are synthetic. Label new ones synthetic.
- Day counts, thresholds and legal references are illustrative unless a
  primary source is cited. Do not invent regulatory citations.
- Plain, specific sentences. State what is not proven.

## Traps already found

- Checks must run in a fresh process with an empty bytecode cache
  (`harness/checks.py`). Do not import governed code into the harness process.
- Skill content hashes are over file bytes. `.gitattributes` pins line endings.
- Attack numbers in the docs follow the order of `attacks/pack.py`. Add new
  attacks at the end, or renumber the references.
- `demo-keys/` is published on purpose. Never add a real credential anywhere.
- A ledger payload key called `name`, `email`, `income` and similar is rejected
  as personal data. Use `id` for identifiers.
- Tests that pass are not proof they bite. When adding a control, disable it
  once and confirm a test fails. Two hand-back rules shadowed each other until
  this was done.
- The scenario's skill release is signed. After changing anything under
  `conformance/scenarios/*/overlay/knowledge/skills/`, re-release it and update
  the content hash in the recording's `evidence.json`.
