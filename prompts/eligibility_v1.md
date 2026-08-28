# Fragment task eligibility assessment v1

Judge whether each frozen fragment can support a high-quality benchmark task.
Return one annotation per fragment in exactly the input order. Do not generate a
task or edit the fragment.

Fragments may be English, Simplified Chinese, or mixed technical prose. Evaluate
their reasoning content directly and preserve the meaning of technical terms.

Use `keep` when the fragment clearly supports a natural, self-contained request
with substantive reasoning and avoidable answer leakage. Use `borderline` when a
useful task may exist but generation must resolve real uncertainty. Use `exclude`
when only factual recall, mechanical reproduction, trivial configuration, isolated
numbers, unavoidable leakage, or extensive hidden context would remain.

Apply five tests: (1) a natural problem can be reconstructed; (2) the core answer
can be withheld; (3) the response requires reasoning; (4) the task can be made
self-contained; and (5) different defensible reasoning approaches remain possible.

Reason invariants are strict: `keep` has both reason fields null; `exclude` has a
non-null `exclusion_reason`; `borderline` has a non-null `borderline_reason`.
Return only JSON conforming to the supplied schema.
