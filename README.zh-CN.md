# apt-fragment-to-task

[English](README.md) | 简体中文

这是一个轻量的 Python 3.11 流水线，用于把已经冻结的学术文段
（fragment）转换为经过验证的 benchmark task candidate。项目源自
APT-Bench Computer Science 流水线，但不包含特定学者、固定语料数量或
Computer Science 专属路径等硬编码假设。

项目只使用 Python 标准库，通过两种可替换的 provider 接入模型：

- `codex-cli`：使用只读沙箱和 JSON Schema 调用 `codex exec`；
- `command`：启动任意外部适配器，通过 stdin 发送提示词，并从文件或
  stdout 读取结构化 JSON。

v0.1 暂不内置 HTTP/API provider。DeepSeek、Qwen、本地模型或其他服务
可以通过一个很薄的 command adapter 接入；API Key 应保存在环境变量中，
不能进入配置、命令参数或运行产物。

## 流水线

```text
fragment JSONL
  -> 语义类别分类
  -> task eligibility（keep / borderline / exclude）
  -> 反向生成 1–3 个 task candidate
  -> 本地确定性检查
  -> 独立语义泄漏审核
  -> accepted / rejected task JSONL
```

生成阶段的 `generation_checks` 是生成模型对自身结果的结构化自检，不是
最终审核结论。独立 leakage 阶段会重新接收匿名化的 fragment 正文、task
prompt 和 `answer_core_to_withhold`，再次判断字面、释义和因果泄漏。

`task_variant_mode` 是不同认知任务框架的简短标签，例如
`failure_diagnosis` 或 `constraint_driven_design`。它用于检查同一
fragment 下候选任务的实质差异，不是质量分数，也不是封闭枚举。

### 完整端到端执行步骤

1. **输入预检与规范化。** 只读加载 JSONL，解析兼容字段名，验证可选正文
   hash，保留额外 metadata，加载 prompts 与 schemas，并拒绝重复 fragment
   ID。dry-run 在报告计划调用后停止，写入和模型调用都为零。
2. **可选 Generation Gold。** 启用后仅加载 `approved` 示例，先执行安全
   校验，再按类别覆盖、variant 多样性和稳定 example ID 确定性选取少量
   few-shot。Gold 只进入 generation，不参与 category 和 eligibility 判断。
3. **语义类别分类。** 从八类 taxonomy 中给出一个 primary category；只有
   内容确实混合时才给 secondary category。返回 JSON 必须通过 schema、输入
   ID 和顺序校验。
4. **Task eligibility。** 根据五个 taskability 维度判断 `keep`、
   `borderline` 或 `exclude`。配置决定 borderline 是否继续；exclude 不会
   进入 task generation。
5. **Task reverse generation。** 每个可用 fragment 生成 1–3 个认知框架
   实质不同的任务。模型收到 fragment、类别、eligibility、候选序号、可选
   few-shot 以及明确的语言/长度策略；`answer_core_to_withhold` 必须留在题面
   之外。
6. **Generation 自检。** 每个候选返回 `generation_checks`。它是生成模型
   提供的诊断信息，但不是独立证据，也不能单独决定通过与否。
7. **本地确定性验证。** 检查候选字段、类别对应的 TaskSpec 与 operation、
   confidence、identity marker、按语言计算的长度、明显来源指代、字面答案
   泄漏、同 fragment 近重复、variant 重复以及跨 fragment 完全重复。
8. **独立语义泄漏审核。** 另一轮模型调用接收 fragment、task prompt 和
   withheld answer core，检查 literal/paraphrase/causal leakage、来源依赖、
   自包含性与 trivial task。之所以不能只依赖 generation 自检，是因为生成
   模型对自身错误的判断并不独立。
9. **产物与溯源。** 写出 accepted/rejected candidate、逐 fragment 结果、
   原始 batch 输入输出、模型调用记录、run summary、SHA-256 manifest 和校验
   文件；结束前复核输入 hash，并拒绝任何已存在的输出目录。
10. **人工审核与实验导出。** 本仓库中的 “accepted” 只表示通过自动门禁，
    不代表人工审定。正式发布前应保留完整候选池，另行填写人工审核状态、
    选择最终 task，再导出字段精简的实验数据。

## 中文及中英混合语料支持

通用 profile 现在支持 `task_language = "auto"`、`"en"` 或 `"zh"`。
`auto` 会按每条 fragment 的主要语言确定期望输出语言，不会把整个 batch
强制成同一种语言。英文按词数校验；中文按 Unicode Han 字符数校验，因此
标点、UTF-8 字节数不会扭曲长度。中文技术语料中的公式、专名和通行英文
术语可以保留，无需机械翻译。

```toml
[run]
task_language = "auto"
min_task_words = 20
max_task_words = 180
min_task_cjk_chars = 40
max_task_cjk_chars = 320
```

本地验证器会识别“本文”“上述文章”“原文”等常见中文来源指代；字面泄漏
和近重复检测使用 Unicode-aware Han 字符特征，中文不再绕过原先依赖 ASCII
token 的规则。这仍然是无需第三方分词包的轻量启发式规则，不等于完整中文
NLP：领域专名仍应通过 `identity_markers` 提供，语义泄漏仍由独立模型阶段
和后续人工审核把关。

## 可选的领域 gold

few-shot 是显式启用的可选能力，只作用于 Task Generation；默认配置保持
zero-shot。仓库提供 22 条经过审核的 Computer Science 示例，以及启用它
的 `configs/computer_science.toml`：

```powershell
$env:PYTHONPATH = "src"
python -m fragment_to_task `
  --input path\to\fragments.jsonl `
  --config configs\computer_science.toml
```

流水线会针对当前 generation batch，依次按 primary category 覆盖、
task_variant_mode 多样性和 gold_example_id 稳定排序选择示例。发送给模型
的 prompt view 不含 source fragment ID、scholar ID 或审核备注；每次运行
仍会记录完整 gold 文件的 SHA-256，实际选中的示例也保存在 generation
batch input 中，便于复现。

CS gold 只能作为 CS profile 和格式示例，不能假设为跨领域通用 gold。
新领域可以先 zero-shot 试跑；确有校准需求时，再选择 12–20 条独立校准
fragment，制订并双人审核本领域 gold。所有 gold fragment 及其来源文档
必须从正式评测集中排除。详见
[领域 Generation Gold 制订指南](docs/gold_curation_guide.zh-CN.md)。

## 项目结构

```text
apt-fragment-to-task/
  configs/                  运行和 provider 配置
  examples/                 合成输入示例
  prompts/                  四个模型阶段的提示词
  schemas/                  输入及模型输出 JSON Schema
  src/fragment_to_task/     CLI、流水线、校验和 provider
  tests/                    不依赖真实模型的离线测试
  README.md                 英文说明
  README.zh-CN.md           中文说明
  pyproject.toml            Python 项目定义
```

## 输入格式

输入为 JSONL，每行一个 JSON object。最小记录：

```json
{"fragment_id":"fragment_001","text":"冻结且保持原样的 fragment 正文。"}
```

为了兼容现有 APT-Bench Computer Science 数据，也接受：

- `final_fragment_id` 代替 `fragment_id`；
- `fragment_text` 代替 `text`；
- `fragment_text_sha256` 代替 `text_sha256`。

其他字段会作为 metadata 保留在最终接受的 task 记录中。可以提供
`identity_markers` 字符串数组，让本地校验器拦截 task prompt 中出现的
作者、实验室、项目或方法专名。

完整说明见 `schemas/fragment_input_v1.schema.json` 和
`examples/fragments.synthetic.jsonl`。如果记录提供正文 SHA-256，程序
会验证它；应用运行还会核对输入文件执行前后的 SHA-256。

## 环境要求

- Python 3.11 或更高版本；
- 不需要第三方 Python 包；
- 使用 Codex 时，本机需要安装并登录 Codex CLI；
- 使用 command provider 时，需要提供符合输出约定的 adapter。

直接从源码运行：

```powershell
$env:PYTHONPATH = "src"
python -m fragment_to_task --version
```

也可以进行可编辑安装：

```powershell
python -m pip install -e .
fragment-to-task --version
```

## 默认只读检查

不带 `--apply` 时只读取输入、配置、提示词和 schema，不调用模型，也不
写文件：

```powershell
$env:PYTHONPATH = "src"
python -m fragment_to_task `
  --input examples\fragments.synthetic.jsonl
```

dry-run 会报告 fragment 数量、输入 SHA-256、provider、计划批次，以及
prompt/schema SHA-256，并明确给出 `llm_calls = 0` 和 `writes = 0`。

## 使用 Codex CLI

默认配置位于 `configs/default.toml`：

```toml
[run]
batch_size = 6
leakage_batch_size = 18
candidates_per_fragment = 3
include_borderline = true
transport_retries = 2
timeout_seconds = 900
min_task_words = 20
max_task_words = 180
task_language = "auto"
min_task_cjk_chars = 40
max_task_cjk_chars = 320

[provider]
kind = "codex-cli"
executable = "codex"
extra_args = []
```

正式执行必须同时指定 `--apply` 和一个此前不存在的输出目录：

```powershell
$env:PYTHONPATH = "src"
python -m fragment_to_task `
  --input path\to\fragments.jsonl `
  --config configs\default.toml `
  --output-dir runs\run_001 `
  --apply
```

Codex provider 通过 stdin 发送 prompt，使用 `--output-schema` 约束输出，
通过 `-o` 接收 JSON，使用 `--ephemeral` 避免保留 session rollout，
并设置 `--sandbox read-only`。可以在配置中指定 `model`，其他 Codex
CLI 参数可以加入 `provider.extra_args`。

官方参考：<https://developers.openai.com/codex/cli/reference>

## 使用通用 command provider

复制 `configs/command.example.toml` 并修改命令参数：

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

外部命令不会经过 shell。UTF-8 prompt 通过 stdin 输入，参数中可使用：

- `{stage}`：`category`、`eligibility`、`generation` 或 `leakage`；
- `{schema_file}`：当前阶段 JSON Schema 的绝对路径；
- `{output_file}`：结构化 JSON 的临时输出路径；
- `{work_dir}`：当前 run 的 batch 目录。

adapter 可以把 JSON object 写入 `{output_file}`，也可以输出到 stdout；
退出码必须为零。DeepSeek 或 Qwen adapter 应从环境变量读取 API Key，从
stdin 读取 prompt，并按当前 schema 返回 JSON。不要打印或记录密钥。

## 四个模型处理阶段

### 1. Category

将 fragment 分为 Background、Motivation、Goal、Method、Experiment、
Result、Conclusion 或 Hypothesis。只有内容确实混合时才使用 secondary
category。

### 2. Eligibility

从五方面判断能否形成任务：自然问题能否重建、核心答案能否被保留、是否
要求实质推理、任务能否自包含，以及是否存在不同但合理的思考路径。

- `keep`：明确适合；
- `borderline`：可能适合，需要实际生成进一步判断；
- `exclude`：只能产生机械复述、事实回忆、严重泄漏或隐藏上下文依赖。

### 3. Task generation

把“必要背景 + 推理/机制/方法/判断/结论”转换为“必要背景 + 要求完成的
认知操作”。核心推理、因果桥梁、首选方法和结论必须留给答题模型产生。

### 4. Leakage validation

独立检查 literal leakage、paraphrase leakage、causal leakage、source
dependence、上下文不足和 trivial task。只有本地检查与独立审核都通过的
candidate 才进入 `task_candidates.jsonl`。

## 本地确定性检查

本地规则负责检查：

- task prompt 非空；英文按词数、中文按 Han 字符数落在配置范围内；
- 不出现明显来源依赖表达或输入提供的 identity marker；
- TaskSpec 的类别、字段和 operation 合法；
- `generation_checks` 完整且取值一致；
- 不包含 answer、rubric、reference answer 等禁止字段；
- withheld answer core 没有被长片段直接复制进 prompt；
- 同一 fragment 的候选不是近似改写；
- 不同 fragment 之间没有完全相同的 task prompt。

## 输出文件

成功的 `--apply` 运行生成：

- `category_annotations.jsonl`
- `eligibility_annotations.jsonl`
- `task_candidates.jsonl`
- `rejected_task_candidates.jsonl`
- `fragment_results.jsonl`
- `model_calls.jsonl`
- `run_summary.json`
- `batches/*.input.json`
- `batches/*.output.json`
- `output_manifest.json`
- `manifest.sha256`

输出目录必须此前不存在，程序不会静默覆盖。

## 运行测试

测试完全离线，不需要模型或网络：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -p "test_*.py" -v
```

测试覆盖输入兼容、正文 hash、泄漏规则、dry-run 零写入、拒绝覆盖、
command provider 和使用假模型完成的四阶段流程。

## 需要人工决定的默认逻辑

以下事项应由具体 benchmark 项目负责人确认：

1. 是否让 `borderline` fragment 进入生成；
2. 每个 fragment 生成 1、2 还是 3 个 task；
3. 八类 taxonomy 是否适用于新领域；
4. 新领域需要提供哪些 identity markers；
5. 自动通过的 task 是否还必须人工确认；
6. 使用哪个模型、推理强度和批量大小；
7. 是否继续使用 MIT 许可证。

当前建议是在探索阶段包含 `borderline`，每个 fragment 生成三个认知框架
不同的 task，并在正式发布 benchmark 前进行人工最终确认。

## 安全边界

- 不编辑 fragment 正文；
- dry-run 不调用模型且不写文件；
- 不覆盖已有输出目录；
- API Key 不进入配置、参数或报告；
- Codex provider 使用只读 sandbox；
- 向远程模型发送真实 fragment 前，应确认数据授权与隐私要求。
