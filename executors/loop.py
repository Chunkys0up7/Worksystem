"""The loop executor: about fifty lines between a model and the tool box.

It shows how little an executor needs to be when the controls live elsewhere.
It holds no credential, reaches a model only through the gateway on the work
order, and touches files only through the harness's tool box, which refuses a
denied action before it happens. It is model-agnostic: the model id is a
parameter and the kind (scripted or live) comes from the pin.
"""

from __future__ import annotations

import json

from harness.tools import TOOL_SPECS
from harness.workorder import HandBack, WorkOrder


class LoopExecutor:
    id = "loop"
    version = "1"
    identity = "executor-loop"

    def __init__(self, model_id: str, kind: str) -> None:
        self.model_id = model_id
        self.kind = kind

    def run(self, order: WorkOrder) -> HandBack:
        if order.gateway is None or order.tools is None:
            raise RuntimeError("the loop executor needs a gateway and a tool box")
        session = order.gateway.session(self.model_id, "author")
        messages = [
            {"role": "system", "content": {
                "change": order.change_id, "spec": order.spec_id,
                "allowed_paths": list(order.allowed_paths),
                "skills": [{"path": s.path, **s.as_dict()} for s in order.skills],
                "instructions": "Read every skill listed before changing anything. Work only "
                                "through the tools. Call finish with your evidence."}},
            {"role": "user", "content": order.task},
        ]
        while True:  # bounded by the gateway's budget, which fails closed
            reply = session.complete(messages, TOOL_SPECS)
            messages.append({"role": "assistant", "content": reply["text"],
                             "tool_calls": reply["tool_calls"]})
            if not reply["tool_calls"]:
                raise RuntimeError("the model stopped without handing back")
            for call in reply["tool_calls"]:
                if call["name"] == "finish":
                    evidence = dict((call.get("args") or {}).get("evidence") or {})
                    evidence["executor"] = {"id": self.id, "kind": self.kind, "version": self.version}
                    evidence.setdefault("model", self.model_id)
                    return HandBack(evidence=evidence,
                                    transcript=json.dumps(messages, sort_keys=True, default=str))
                result = order.tools.call(call["name"], call.get("args") or {})
                messages.append({"role": "tool", "name": call["name"], "content": result})
