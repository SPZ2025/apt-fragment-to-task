# Reverse task generation v1

Generate benchmark task candidates from frozen fragments. A task must test
academic or technical reasoning, not passage recall.

Transform `necessary context + reasoning/mechanism/judgment/conclusion` into
`necessary context + requested cognitive operation`. The future respondent must
still produce the central reasoning, mechanism, method, inference, or conclusion.

Avoid literal leakage, paraphrase leakage, and causal leakage. Do not mention a
passage, paper, excerpt, fragment, author, scholar, hidden document, or source.
Do not ask for summary or author-style imitation. Write each task in English,
prefer 40--160 English words, and never exceed 180. Do not emit answers, reference
answers, rubrics, scoring points, acceptable alternatives, or fatal errors.

Each requested candidate index needs a substantively different cognitive framing.
`task_variant_mode` is a short semantic label for that framing (for example,
`mechanism_derivation`, `constraint_driven_design`, or `failure_diagnosis`); it is
not a quality score and it is not a closed vocabulary.

`answer_core_to_withhold` contains concise source-derived ideas that must remain
absent from the task prompt. `generation_checks` describes the generated prompt;
all valid candidates should be self-contained and source-agnostic, have no literal,
paraphrase, or causal leakage, and be distinct from their siblings.

The input may contain `few_shot_examples`. They demonstrate the boundary between
allowed context and withheld answer content, plus useful task framings. Learn only
that transformation pattern. Do not copy an example's domain, scenario, entities,
technical claims, or wording into a new task. Examples can come from a different
domain and never override the current fragment, category, requested indexes, or
output schema. An empty list means to generate without examples.

The `task_spec.structure_type` must equal the primary category. Use only these
category fields and operations:

- Background fields: domain_context, known_observations, concepts_or_relations;
  operations: explain, compare, organize, analyze, synthesize, abstract.
- Motivation fields: problem_or_tension, observed_limitation, decision_context;
  operations: analyze, diagnose, justify, compare, assess.
- Goal fields: research_context, unresolved_problem, constraints; operations:
  formulate_goal, formulate_question, define_target, prioritize.
- Method fields: problem, objective, constraints, available_information;
  operations: design, derive, diagnose, propose, justify, redesign, compare.
- Experiment fields: claim_or_question, variables_or_factors,
  competing_explanations, experimental_constraints; operations: design_experiment,
  choose_controls, design_comparison, evaluate_hypothesis.
- Result fields: observations, comparison_context, interpretation_context;
  operations: interpret, infer, diagnose, explain, derive_design_implication,
  compare_explanations.
- Conclusion fields: premises, evidence, scope_conditions; operations: infer,
  assess, synthesize, justify, critique, calibrate_conclusion.
- Hypothesis fields: observed_phenomenon, variables, conditions; operations:
  formulate_hypothesis, propose_mechanism, predict, relate_variables.

Return exactly the requested fragment IDs and candidate indexes in input order and
only JSON conforming to the supplied schema.

