# Demo keys: published on purpose

These are private signing keys, committed to the repository deliberately so
that anyone can clone the build, run it, and run the attack pack against it.

They protect nothing. Do not reuse them and do not copy the pattern. In a real
deployment each workload identity's key sits in a KMS or HSM, no person or
agent can read it, and the attack "sign as CI with a stolen key" is answered by
key custody, which this repository does not demonstrate (see docs/limits.md).
