# 领域 Generation Gold 制订指南

本项目把 few-shot 视为可选的、仅用于 Task Generation 阶段的领域校准材料。Computer Science gold 是完整示例，不是跨领域的默认答案库。Category、Eligibility 与最终 Leakage 审核均不接收 gold 示例，以保持判断相互独立。

## 何时需要 gold

新领域可以先用默认配置进行 zero-shot 小样本试跑。如果任务经常出现题面泄漏、任务过于宽泛、认知操作单一或领域语境处理不当，再制订该领域的 gold。不要仅为“使用 few-shot”而制作 gold。

## 推荐流程

1. 从目标领域选取约 12–20 个校准 fragment，覆盖常见类别、文本难度和边界情形。
2. 校准 fragment 及其来源文档必须从正式评测集合中排除，避免示例污染评测。
3. 对每个 fragment 明确区分：
   - allowed_given_information：题面可以提供的必要背景；
   - answer_core_to_withhold：答题者必须自行推出的机制、方法、因果桥梁或结论。
4. 人工撰写或模型辅助生成 task_prompt；使用模型时，产物仍保持 draft。
5. 由未参与初稿的人独立检查自包含性、来源无关性、字面/释义/因果泄漏和领域合理性。
6. 仅将通过审核的记录标为 approved。流水线拒绝使用 draft 或 rejected 记录。
7. 冻结 JSONL，记录版本、记录数和 SHA-256；修改内容时创建新版本，不原地静默替换。

## 质量与覆盖

gold 的目标是示范“如何从 fragment 保留必要条件、隐去答案核心并提出认知任务”，不是囊括所有知识。优先保证：

- 类别与任务操作有代表性；
- answer core 真实来自 source fragment；
- task prompt 不包含 source、作者或文段依赖措辞；
- 不把 withheld core 逐字、释义或因果链提前写入题面；
- 示例之间具有不同 task_variant_mode；
- 不使用纯回忆、摘要或风格模仿任务。

若领域包含特殊体裁（定理、法条、病例、史料、定量表格等），应在本领域 gold 中加入少量相应边界示例，而不是复制 CS 场景。

## 文件格式

每行必须符合 `schemas/generation_gold_record_v1.schema.json`。可从
`goldsets/templates/generation_gold_template.jsonl` 开始。运行时只会向模型发送去除来源标识和审核备注后的 prompt view；完整记录仍用于本地溯源与验证。

验证一个 gold 文件：

```powershell
python scripts/validate_gold_set.py --gold-file path\to\generation_gold_v1.jsonl
```

从 fragment 与 category annotation 中确定性抽取一份待审核校准包：

```powershell
python scripts/prepare_gold_calibration.py `
  --fragments path\to\fragments.jsonl `
  --categories path\to\category_annotations.jsonl `
  --domain economics `
  --sample-size 16
```

上述命令默认仅预览。确认后加入 `--apply --output-dir <new-directory>`；目标目录必须不存在。

## 启用方式

在领域配置中显式启用：

```toml
[few_shot.generation]
enabled = true
file = "goldsets/my_domain/generation_gold_v1.jsonl"
examples_per_batch = 4
```

选择规则固定为：优先覆盖当前 generation batch 的 primary category，其次优先不同 task_variant_mode，最后按 gold_example_id 稳定排序。每个 batch 实际使用的 prompt view 会保存在该次运行的 `batches/generation_*.input.json` 中，run summary 同时记录 gold 文件 SHA-256。

## CS 示例的使用边界

`goldsets/examples/computer_science/generation_gold_v1.jsonl` 来自已审核的 APT-Bench CS 样例，可用于复现实验或说明格式。它可作为新领域 gold 的审查参照，但不建议直接用于最终跨领域批量生成；其类别覆盖也并不完整。新领域若暂时没有 gold，应优先明确记录为 zero-shot，而不是把 CS 示例伪装成通用 gold。
