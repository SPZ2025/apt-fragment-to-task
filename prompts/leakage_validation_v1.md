# Independent semantic leakage validation v1

Audit each proposed task independently. Do not edit it or generate a replacement.
The input is anonymous and contains only indexes, source fragment text, a proposed
task prompt, and `answer_core_to_withhold`.

Return `pass` only when the task is self-contained, source-agnostic, requires
substantive reasoning, and does not literally copy, paraphrase, or supply the causal
bridge of the withheld answer. Otherwise return `fail` with one or more exact
failure types: `literal_leakage`, `paraphrase_leakage`, `causal_leakage`,
`not_self_contained`, `source_dependent`, `trivial_task`, or `other`.

For `pass`, `failure_types` must be empty. For `fail`, it must be non-empty.
Preserve exact input order and indexes. Return only schema-conforming JSON.

