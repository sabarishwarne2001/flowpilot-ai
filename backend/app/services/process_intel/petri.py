"""ARCH49-S1:petri — Petri nets and token-based replay. Pure.

TOKEN REPLAY (Rozinat & van der Aalst)
======================================
A trace is replayed on a net from its initial marking. Every transition that
fires CONSUMES tokens from its input places and PRODUCES tokens into its output
places; a token it needs that is not there is counted MISSING (and created, so
replay continues). At the end the final marking is consumed; tokens left
anywhere else are REMAINING. With p produced, c consumed, m missing and r
remaining (the initial marking counts as produced, the final as consumed):

        fitness = 1/2 (1 - m/c) + 1/2 (1 - r/p)

1.0 means the trace is exactly a run of the model. Activities the net does not
know are reported, not replayed (they are not steps of the model).

TWO SEMANTICS THE PRODUCT'S MODELS NEED
=======================================
  consume_all  an OR-join: a node reached along several edges fires ONCE and
               takes every token that arrived (ARCH-37's executor runs a node
               once however many predecessors reached it). It needs at least
               one; none is one missing token.
  choice       an XOR-split: a branch / condition / assertion node produces
               only along the edges of the branch it took ("true", "false",
               "pass", "triage"), which the replayed step names.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

START = "start"
END = "end"


@dataclass
class Transition:
    name: str
    inputs: dict[str, int] = field(default_factory=dict)
    outputs: dict[str, int] = field(default_factory=dict)
    #: outputs that depend on the branch the step took (added to `outputs`).
    choices: dict[str, dict[str, int]] = field(default_factory=dict)
    consume_all: tuple[str, ...] = ()
    #: A step that FAILED fires without producing (its successors never run).
    silent_on_failure: bool = True


@dataclass
class Net:
    transitions: dict[str, Transition]
    initial: dict[str, int]
    final: dict[str, int]

    @property
    def places(self) -> set[str]:
        out = set(self.initial) | set(self.final)
        for t in self.transitions.values():
            out |= set(t.inputs) | set(t.outputs)
            for extra in t.choices.values():
                out |= set(extra)
        return out


@dataclass(frozen=True)
class ReplayStep:
    transition: str
    choice: Optional[str] = None
    failed: bool = False


@dataclass
class ReplayResult:
    produced: int
    consumed: int
    missing: int
    remaining: int
    unknown: list[str]
    missing_at: list[str]
    remaining_at: dict[str, int]

    @property
    def fitness(self) -> float:
        m = self.missing / self.consumed if self.consumed else 0.0
        r = self.remaining / self.produced if self.produced else 0.0
        return round(0.5 * (1 - m) + 0.5 * (1 - r), 6)

    @property
    def fits(self) -> bool:
        return self.missing == 0 and self.remaining == 0

    def as_json(self) -> dict[str, Any]:
        return {"fitness": self.fitness, "produced": self.produced, "consumed": self.consumed,
                "missing": self.missing, "remaining": self.remaining, "unknown": list(self.unknown),
                "missing_at": list(self.missing_at), "remaining_at": dict(self.remaining_at)}


def replay(net: Net, steps: Sequence[ReplayStep | str]) -> ReplayResult:
    marking: Counter[str] = Counter(net.initial)
    produced = sum(net.initial.values())
    consumed = missing = 0
    unknown: list[str] = []
    missing_at: list[str] = []
    for raw in steps:
        step = raw if isinstance(raw, ReplayStep) else ReplayStep(str(raw))
        transition = net.transitions.get(step.transition)
        if transition is None:
            unknown.append(step.transition)
            continue
        for place, need in transition.inputs.items():
            have = marking[place]
            if place in transition.consume_all:
                if have == 0:
                    missing += 1
                    consumed += 1
                    missing_at.append(place)
                else:
                    consumed += have
                marking[place] = 0
                continue
            if have < need:
                missing += need - have
                missing_at.extend([place] * (need - have))
                marking[place] = 0
            else:
                marking[place] = have - need
            consumed += need
        if step.failed and transition.silent_on_failure:
            continue
        outputs = Counter(transition.outputs)
        if step.choice is not None:
            outputs.update(transition.choices.get(step.choice, {}))
        for place, n in outputs.items():
            marking[place] += n
            produced += n
    for place, _need in net.final.items():
        have = marking[place]
        if have == 0:
            missing += 1
            consumed += 1
            missing_at.append(place)
        else:
            consumed += have
        marking[place] = 0
    remaining_at = {p: n for p, n in sorted(marking.items()) if n > 0}
    return ReplayResult(produced=produced, consumed=consumed, missing=missing, remaining=sum(remaining_at.values()),
                        unknown=unknown, missing_at=missing_at, remaining_at=remaining_at)


# ---------------------------------------------------------------------------
# The two models the product has
# ---------------------------------------------------------------------------

#: Branch labels that select edges (anything else, "default", is always taken).
CHOICE_LABELS = ("true", "false", "pass", "triage")


def flow_net(nodes: Iterable[tuple[str, str]], edges: Iterable[tuple[str, str, str]]) -> Net:
    """An ARCH-37 flow (automation_nodes / automation_edges) as a net.

    One place in front of every node (`in:<key>`); the trigger consumes the start token. A node's
    `default` edges always produce into their target's place; `true` / `false` / `pass` / `triage` edges
    produce only on that choice. A `condition` node's default edges are its "true" (a condition that did
    not match stops the walk). A node without outgoing edges produces into the end place. Every node
    consumes ALL tokens waiting in front of it (the executor runs a node once, however reached).
    """
    node_list = list(nodes)
    types = dict(node_list)
    transitions: dict[str, Transition] = {}
    outgoing: dict[str, list[tuple[str, str]]] = {key: [] for key, _ in node_list}
    for source, target, branch in edges:
        if source in outgoing and target in types:
            outgoing[source].append((target, branch or "default"))
    for key, node_type in node_list:
        place = START if node_type == "trigger" else f"in:{key}"
        t = Transition(name=key, inputs={place: 1}, consume_all=(place,))
        if not outgoing[key]:
            t.outputs[END] = t.outputs.get(END, 0) + 1
        for target, branch in outgoing[key]:
            into = f"in:{target}"
            if branch in CHOICE_LABELS:
                t.choices.setdefault(branch, {})
                t.choices[branch][into] = t.choices[branch].get(into, 0) + 1
            elif node_type == "condition":
                t.choices.setdefault("true", {})
                t.choices["true"][into] = t.choices["true"].get(into, 0) + 1
            else:
                t.outputs[into] = t.outputs.get(into, 0) + 1
        # A choice that leads nowhere ends the walk there (a condition that did not match, a branch with no
        # edge on the side it took): that is a run of the model, not a deviation.
        if node_type in ("condition", "branch"):
            for label in ("true", "false"):
                if not t.choices.get(label) and outgoing[key]:
                    t.choices[label] = {END: 1}
        transitions[key] = t
    return Net(transitions=transitions, initial={START: 1}, final={END: 1})


def template_net(slots: Iterable[tuple[str, int]]) -> Net:
    """An ARCH-43 case template as a net: opening a case owes every required slot its minimum count of
    documents; completing it needs them all (a completion without them is MISSING tokens); a case closed
    without every document it owed leaves REMAINING tokens. A document beyond what a slot requires, or of a
    type the template does not name, is allowed (replay does not count it as a step).

        case.opened         start -> open, need:<type> x min_count
        doc:<type>          need:<type> -> have:<type>
        case.inconsistent   open -> open           (a rule failed; it is rework, not a deviation)
        case.completed      open + have:<type> x min_count -> end
        case.closed         open -> end            (closing without completing)
    """
    slot_list = [(t, max(1, int(n))) for t, n in slots]
    opened = Transition(name="case.opened", inputs={START: 1}, outputs={"open": 1})
    completed = Transition(name="case.completed", inputs={"open": 1}, outputs={END: 1})
    transitions = {"case.opened": opened, "case.completed": completed,
                   "case.inconsistent": Transition(name="case.inconsistent", inputs={"open": 1}, outputs={"open": 1}),
                   "case.closed": Transition(name="case.closed", inputs={"open": 1}, outputs={END: 1})}
    for doc_type, need in slot_list:
        opened.outputs[f"need:{doc_type}"] = need
        completed.inputs[f"have:{doc_type}"] = need
        transitions[f"doc:{doc_type}"] = Transition(name=f"doc:{doc_type}", inputs={f"need:{doc_type}": 1},
                                                    outputs={f"have:{doc_type}": 1})
    return Net(transitions=transitions, initial={START: 1}, final={END: 1})


def case_steps(events: Sequence[tuple[str, Optional[str]]], slots: Iterable[tuple[str, int]]) -> list[ReplayStep]:
    """(activity, document type) events of one case -> the steps its template net replays.

    Documents beyond a slot's minimum, of an unnamed type, and evaluations are not model steps. A case that
    completes and is later closed ends at the completion (closing a completed case is bookkeeping)."""
    need = {t: max(1, int(n)) for t, n in slots}
    seen: Counter[str] = Counter()
    steps: list[ReplayStep] = []
    completed = False
    for activity, doc_type in events:
        if activity == "case.opened":
            steps.append(ReplayStep("case.opened"))
        elif activity == "case.document_added" and doc_type in need:
            seen[doc_type] += 1
            if seen[doc_type] <= need[doc_type]:
                steps.append(ReplayStep(f"doc:{doc_type}"))
        elif activity == "case.inconsistent" and not completed:
            steps.append(ReplayStep("case.inconsistent"))
        elif activity == "case.completed" and not completed:
            completed = True
            steps.append(ReplayStep("case.completed"))
        elif activity == "case.closed" and not completed:
            completed = True
            steps.append(ReplayStep("case.closed"))
    return steps


__all__ = ["CHOICE_LABELS", "END", "Net", "ReplayResult", "ReplayStep", "START", "Transition", "case_steps",
           "flow_net", "replay", "template_net"]
