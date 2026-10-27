# 自定义 CSV 导入 / Custom CSV import

GridWise Campus 桌面程序可以只读导入用户明确选择的本地 CSV，用现有纯电费或峰值上限模式分析负荷、光伏和电价时序。它不连接学校系统、数据库或设备。/ The desktop can read an explicitly selected local CSV for cost-only or peak-cap analysis. It does not connect to campus systems, databases or devices.

## 模板与字段 / Template and fields

复制 [`examples/synthetic-day.csv`](../examples/synthetic-day.csv) 作为模板。列名和顺序固定：/ Copy the existing sample as the template. Headers and order are exact:

```csv
timestamp,load_kw,pv_kw,price_cny_per_kwh
2026-01-01T08:00:00+08:00,60,0,0.5
2026-01-01T09:00:00+08:00,65,20,0.5
```

| 字段 / Field | 单位 / Unit | 规则 / Rule |
|---|---|---|
| `timestamp` | ISO 8601 | 必须带 UTC 偏移，严格递增，固定间隔 / offset-aware, increasing, regular |
| `load_kw` | kW | 非负有限数值 / finite and nonnegative |
| `pv_kw` | kW | 非负有限数值 / finite and nonnegative |
| `price_cny_per_kwh` | CNY/kWh | 非负有限数值 / finite and nonnegative |

文件使用 UTF-8，可带 BOM；不允许额外列、重复列、空值或空白物理行。程序不自动转换 W、MW、其他币种，不插值或填补缺口。合法的 `Z`、`+0000` 和 `+00:00` 在内存中规范化为 `datetime.isoformat()` 形式，绝对时间不变，原文件不写回。/ UTF-8 with an optional BOM is accepted. Units are encoded in the headers; there is no conversion, interpolation or gap filling. Valid timestamp spellings are canonicalized in memory without rewriting the source.

## 限制 / Limits

- 文件不超过 32 MiB，单物理行不超过 64 KiB。/ File ≤32 MiB; physical line ≤64 KiB.
- 最多 50,000 个输入时段，数值 token 最多 128 字符，数值不超过 10¹²。/ Up to 50,000 periods; bounded numeric tokens and magnitude.
- `step_hours` 由用户显式填写，范围 `(0,24]` 小时，并须与时间戳绝对间隔一致。/ The explicit step must match timestamp spacing.
- 桌面交互求解最多 96 时段；更长数据仍可显示质量报告和摘要，但不能启动桌面求解。/ Desktop solving is limited to 96 periods; larger valid files remain inspectable.

## 操作流程 / Workflow

1. 启动 `python -m gridwise.desktop`。/ Start the desktop application.
2. 选择 CSV 并输入小时步长。/ Select the CSV and enter the step in hours.
3. 点击“检查数据 / Check data”。失败时根据文件、行号、字段和原因修正源文件后重试。/ Run the quality check and use its located diagnostics.
4. 质量通过后查看起止时间、功率/价格范围及负荷、光伏可用电量摘要。电量口径分别为 `Σ(load_kw×step_hours)` 与 `Σ(pv_kw×step_hours)`。/ Review the input summary; energy is power multiplied by the equal step and summed.
5. 选择纯电费或峰值上限模式，再运行对比。只有求解、独立审计、运行目录发布和安全读取全部成功后才显示图表。/ Results appear only after solve, independent audit, publication and verified reading all succeed.

输入失败、切换文件或修改步长会清除旧摘要和结果。程序不会修改原始 CSV。/ Failed or changed inputs invalidate prior summaries and results; the source CSV is never modified.

## 设备参数与来源边界 / Equipment and provenance boundary

CSV 只替换时间戳、负荷、可用光伏和电价。`grid_max_kw`、电池容量、初末 SOC、充放电功率及效率仍来自 `examples/synthetic-day.json`；这不是完整的用户设备建模。/ The CSV changes only the time series. Grid and battery/SOC assumptions remain those of the synthetic equipment template.

已校验运行目录保存生成后的规范化 `scenario.json`，不保存原始 CSV 字节。清单哈希可检测运行文件内容变化，但不能证明输入真实、可信或获得授权。项目仍无真实校园数据连接或控制能力。/ A verified bundle stores the normalized scenario, not the original CSV bytes. Hashes detect content changes; they do not prove authenticity, trustworthiness or authorization.
