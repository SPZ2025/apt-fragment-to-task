# apt-fragment-to-task

English | [简体中文](README.zh-CN.md)

A small Python 3.11 pipeline that turns frozen academic fragments into validated
benchmark task candidates. It generalizes the APT-Bench Computer Science workflow
without carrying over scholar counts, domain paths, or fixed corpus assumptions.

The project uses only the Python standard library. A model is supplied through one
of two interchangeable providers:

- `codex-cli`: invokes `codex exec` with a read-only sandbox and an output schema.
- `command`: invokes any adapter executable without a shell, sends the prompt on
  stdin, and reads structured JSON from a file or stdout.

There is intentionally no native HTTP/API provider in v0.1. DeepSeek, Qwen, local
models, or another service can be connected through a small command adapter whose
credentials stay in environment variables.

## Pipeline

```text
fragment JSONL
  -> semantic category
  -> task eligibility (keep / borderline / exclude)
  -> 1-3 reverse-generated task candidates
  -> deterministic validation
  -> independent semantic leakage validation
  -> accepted and rejected task JSONL
```

The generation-stage `generation_checks` are the generating model's structured
self-checks. The final leakage stage is deliberately independent: it receives the
source fragment, proposed task, and withheld answer core, and can reject a prompt
that passed generation. Local deterministic checks run between the two stages.

`task_variant_mode` is a short, model-authored label for a candidate's cognitive
framing, such as `failure_diagnosis` or `constraint_driven_design`. It is used to
enforce diversity among candidates from the same fragment; it is not a score and
has no closed vocabulary.

### Complete end-to-end flow

1. **Preflight and normalization.** Read JSONL without editing it, resolve field
   aliases, verify optional text hashes, preserve extra metadata, load prompts and
   schemas, and reject duplicate fragment IDs. A dry-run stops here after reporting
   the planned calls and performs zero writes and zero model calls.
2. **Optional generation Gold.** If enabled, load only approved examples, validate
   their language-independent safety constraints, and select a small deterministic
   subset by category coverage, variant diversity, and stable example ID. Gold is
   used only for generation, never for category or eligibility decisions.
3. **Semantic category.** Assign one primary category and, only when necessary, one
   secondary category from the eight-category taxonomy. The JSON response is
   schema-checked and matched to input IDs and order.
4. **Task eligibility.** Decide `keep`, `borderline`, or `exclude` from five
   taskability dimensions. Configuration determines whether borderline fragments
   continue. Excluded fragments never reach task generation.
5. **Reverse task generation.** For each eligible fragment, generate one to three
   cognitively distinct task prompts. The model receives the fragment, category,
   eligibility, requested indexes, optional few-shot examples, and an explicit
   language/length policy. It must keep `answer_core_to_withhold` out of the prompt.
6. **Generation self-check.** Every proposal includes `generation_checks`. These are
   useful model-authored diagnostics, but they are not independent evidence and do
   not determine final acceptance by themselves.
7. **Local deterministic validation.** Validate the exact candidate shape,
   category-specific TaskSpec, operation, confidence, identity markers, language-
   aware length, obvious source references, literal answer leakage, sibling near-
   duplication, variant duplication, and global exact prompt duplication.
8. **Independent semantic leakage validation.** A separate model call receives the
   source fragment, task prompt, and withheld answer core. It checks literal,
   paraphrase, and causal leakage, source dependence, self-containment, and
   triviality. This stage exists because a generator's self-check is correlated
   with its own mistakes.
9. **Packaging and provenance.** Write accepted and rejected candidate JSONL,
   per-fragment results, raw batch inputs/outputs, call logs, run summary, SHA-256
   manifest, and checksum file. Recheck the input hash and refuse any pre-existing
   output directory.
10. **Human review and benchmark export.** “Accepted” in this repository means the
    candidate passed the automated gates; it is not a human adjudication. Before a
    benchmark release, retain the full candidate pool, assign human review status,
    select final tasks, and export the compact experiment schema separately.

## Chinese and mixed-language corpora

The generic profile now supports `task_language = "auto"`, `"en"`, or `"zh"`.
`auto` resolves the expected task language from each fragment; it does not force a
whole batch into one language. English uses word limits, while Chinese uses Unicode
Han-character limits, so punctuation and UTF-8 byte length do not distort the
measurement. Mixed Chinese technical prose may retain formulas, proper nouns, and
English terminology.

```toml
[run]
task_language = "auto"
min_task_words = 20
max_task_words = 180
min_task_cjk_chars = 40
max_task_cjk_chars = 320
```

The local validator also recognizes common Chinese source references such as
“本文”, “上述文章”, and “原文”. Literal-leakage and near-duplicate checks use
Unicode-aware Han-character features, so Chinese text no longer bypasses rules that
previously depended on ASCII tokens. This remains a lightweight heuristic rather
than linguistic word segmentation; domain-specific names should still be supplied
through `identity_markers`, and semantic leakage remains the responsibility of the
independent model stage plus human review.

## Optional domain gold

Few-shot examples are optional, explicitly configured, and used only by the task
generation stage. The generic default remains zero-shot. The included Computer
Science profile enables 22 reviewed CS examples:

```powershell
$env:PYTHONPATH = "src"
python -m fragment_to_task `
  --input path\to\fragments.jsonl `
  --config configs\computer_science.toml
```

Each batch selects examples deterministically by current primary categories,
variant diversity, and stable example ID. Only a provenance-free prompt view is
sent to the model; the run summary records the gold file SHA-256. Do not treat the
CS set as universal. Build and independently review a domain-specific set when
needed, and exclude its calibration fragments and source documents from formal
evaluation. See [the Chinese curation guide](docs/gold_curation_guide.zh-CN.md),
the schema, template, and validation scripts under `goldsets/` and `scripts/`.


## Input

One JSON object per line. The smallest record is:

```json
{"fragment_id":"fragment_001","text":"Exact frozen fragment text."}
```

Compatibility aliases are accepted:

- `final_fragment_id` for `fragment_id`
- `fragment_text` for `text`
- `fragment_text_sha256` for `text_sha256`

All additional fields are preserved as metadata in accepted candidate records.
An optional `identity_markers` string array lets the local validator reject model
prompts containing known author, laboratory, project, or method identifiers.
See `schemas/fragment_input_v1.schema.json` and
`examples/fragments.synthetic.jsonl`.

## Run with Codex CLI

The default mode is read-only and performs no model calls or writes:

```powershell
$env:PYTHONPATH = "src"
python -m fragment_to_task `
  --input examples/fragments.synthetic.jsonl
```

Run the four model stages only with `--apply` and a new output directory:

```powershell
$env:PYTHONPATH = "src"
python -m fragment_to_task `
  --input path\to\fragments.jsonl `
  --config configs\default.toml `
  --output-dir runs\run_001 `
  --apply
```

The output directory must not already exist. Codex is called using the documented
non-interactive contract: stdin prompt, `--output-schema`, `-o`, `--ephemeral`, and
`--sandbox read-only`. Model selection can be set in `configs/default.toml`; any
other supported Codex CLI option can be added to `provider.extra_args`.

Official Codex CLI reference:
<https://developers.openai.com/codex/cli/reference>

## Run with a generic command

Copy `configs/command.example.toml` and set `provider.command` to an argument array.
The process is launched directly, never through a shell. The prompt is UTF-8 on
stdin. These placeholders are available in each argument:

- `{stage}`: `category`, `eligibility`, `generation`, or `leakage`
- `{schema_file}`: absolute JSON Schema path
- `{output_file}`: temporary destination for the JSON response
- `{work_dir}`: run batch directory

The adapter may write the JSON object to `{output_file}` or print it to stdout.
It must return exit code zero. Do not put API keys in TOML or command arguments;
let the adapter read `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, or another secret
from its environment.

Example shape:

```toml
[provider]
kind = "command"
command = [
  "python", "adapter.py",
  "--stage", "{stage}",
  "--schema", "{schema_file}",
  "--output", "{output_file}"
]
```

## Outputs

An applied run writes:

- `category_annotations.jsonl`
- `eligibility_annotations.jsonl`
- `task_candidates.jsonl` — candidates passing local and semantic leakage checks
- `rejected_task_candidates.jsonl`
- `fragment_results.jsonl`
- `model_calls.jsonl`
- `run_summary.json`
- `batches/*.input.json` and `batches/*.output.json`
- `output_manifest.json` and `manifest.sha256`

Input text is never edited. The input file SHA-256 is checked before and after an
applied run. The pipeline refuses to overwrite an existing output directory.

## Human decisions retained in v0.1

The defaults are practical rather than universal. A project owner should decide:

1. Whether `borderline` fragments enter generation (`include_borderline`).
2. Whether each fragment needs one, two, or three candidates.
3. Whether the eight-category taxonomy fits the new domain.
4. Which identity markers must be supplied for domain-specific leakage checks.
5. Whether an accepted task still needs human confirmation before benchmark use.

The recommended default is to include borderline fragments during exploration,
generate three variants, and require human review before publishing a final task.

## Tests

No live model or network access is needed:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -p "test_*.py" -v
```
