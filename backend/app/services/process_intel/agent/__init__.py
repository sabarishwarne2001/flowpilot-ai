"""ARCH49-S1:agent — the Governed Exception Agent.

    vocabulary  proposal kinds, statuses, tools, templates, bounds (pure)
    contracts   the typed tool call (`AgentAction`) and its arguments (pure; no document text fits)
    evidence    the READ tools: structured evidence for the planner, fenced excerpts for people
    planner     one policy per kind of exception, over structured evidence only
    autonomy    whether a proposal may apply itself: an ARCH-35 conformal bound, read at the time
    actions     approving, rejecting, undoing, and applying a proposal (through resolve_item)
    runner      the sweep: plan what is open, schedule, apply what is due, retire what is stale

The agent calls NO model. Every proposal is computed from structured evidence
(states, scores, counts, ids, calibrated probabilities, reviewers' precedent).
Document text is read only into `FencedContext` excerpts shown to the person
deciding; the planner and the tool selectors cannot accept it by type.
"""
