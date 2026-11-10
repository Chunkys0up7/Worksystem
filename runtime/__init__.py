"""The agent runtime (programme 2): what the harness builds and releases into.

It shares one evidence layer with the delivery harness and meets it at one
interface: released, versioned units come in; decision events go out.

What every agent gets from it, without asking:

- durable case state, checkpointed in the same database transaction as the
  ledger entries for that step
- knowledge and code resolved by the versions pinned on the case when it
  started; a newer release applies to new cases only
- a regulated figure only from a deterministic decision service, with a receipt
- models only through the gateway, and only models approved for the data
- personal data by reference, in a store that can be erased
"""
