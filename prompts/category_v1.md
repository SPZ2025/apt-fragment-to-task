# Fragment category classification v1

Classify each frozen fragment independently by what it primarily contributes as
a potential answer. Return one annotation per input fragment in exactly the same
order. Use exactly these categories:

- Background: established knowledge, prior work, facts, or field context.
- Motivation: limitations, difficulties, consequences, or why work is needed.
- Goal: the problem, object, question, proof, or objective being addressed.
- Method: how a mechanism, algorithm, construction, derivation, or process works.
- Experiment: how evaluation is conducted, including controls and protocols.
- Result: direct observed outcomes, measurements, comparisons, or trends.
- Conclusion: an interpretation, implication, or judgment derived from evidence.
- Hypothesis: a testable proposed relationship, mechanism, or conditional claim.

Choose the primary category from the whole fragment, not only its first sentence.
Use at most one secondary category and only when it occupies substantial content.
Set `mixed_category` to true exactly when `secondary_category` is non-null. Give a
confidence in [0,1] and a concise reason. Do not generate a task, edit the source,
or infer author identity. Return only JSON conforming to the supplied schema.

