# 场景预设交换 / Scenario preset exchange

本功能用于在本机之间交换**一条**设备与优化策略预设。它不导出 CSV、求解结果、历史运行、报告或路径，也不会在导入后自动应用参数或启动求解。/ This feature exchanges one equipment-and-policy preset. It does not carry CSV data, results, run history, reports or paths, and import never applies the preset or starts a solve automatically.

## 文件格式 / File format

交换文件为 UTF-8 JSON，最大 128 KiB。根对象和所有嵌套对象都采用严格字段集合；缺少字段、额外字段、重复 JSON 键、NaN、Infinity、布尔值冒充数值、未知版本或错误文档类型都会被拒绝。/ Exchange files are UTF-8 JSON limited to 128 KiB. Exact fields are required; missing or extra fields, duplicate keys, non-finite values, Boolean numeric impostors, unknown versions and wrong document types are rejected.

```json
{
  "document_type": "gridwise-scenario-preset",
  "format_version": 1,
  "preset": {
    "name": "教学楼午间削峰",
    "device_parameters": {
      "grid_max_kw": 120,
      "capacity_kwh": 80,
      "initial_kwh": 40,
      "terminal_kwh": 40,
      "max_charge_kw": 30,
      "max_discharge_kw": 30,
      "charge_efficiency": 0.95,
      "discharge_efficiency": 0.95
    },
    "optimization": {
      "mode": "peak_cap",
      "peak_cap_kw": 55
    }
  }
}
```

- `mode="cost"` 时 `peak_cap_kw` 必须为 `null`；`mode="peak_cap"` 时必须为有限非负数且不超过 `grid_max_kw`。/ Cost mode requires a null cap; peak-cap mode requires a finite nonnegative cap no greater than the grid limit.
- 八项设备参数继续由公共 `parse_device_parameters()` 校验，单位和范围与桌面设备配置相同。/ The shared device parser enforces the same units and ranges as the desktop equipment editor.
- 本地内部 UUID 不导出；每次成功导入都会生成新的内部 ID。/ Local UUIDs are omitted, and every successful import receives a fresh local ID.

## 导入 / Import

1. 在场景预设区域点击“导入 / Import”，选择一个 `.json` 文件。
2. `load_preset_exchange()` 在任何本地预设变更前完成文件类型、大小、编码、版本、字段、设备和策略校验。
3. 若名称与现有预设大小写不敏感地重名，选择取消，或输入一个新名称；不会静默覆盖。
4. 桌面通过现有 `create_preset()` 加入返回对象，因此继续执行重复 ID、重复名称和最多 100 条预设的限制。
5. `save_presets()` 成功后才刷新下拉框并选中新预设。

导入失败、取消、改名失败或保存失败时，当前预设库和界面选择保持不变。导入成功也只刷新预设列表：不会自动加载八项设备参数或策略，不会清除当前 CSV、指标、图表、历史选择或报告资格，也不会触发求解、审计或运行发布。/ Validation precedes any collection change. Failure and cancellation leave the store and selection unchanged; success refreshes only the preset list and does not apply configuration or alter the active run context.

## 导出 / Export

1. 从下拉框选择一条已经保存的预设并点击“导出 / Export”。
2. 桌面按稳定 ID 重新读取本地预设库；已删除或失效的选择不能导出。
3. 选择新的 JSON 目标路径。界面不提供覆盖选项。
4. `export_preset_exchange()` 再次读取本地库并序列化该 ID 对应的已保存预设。
5. 内容写入目标目录中的独占私有临时文件，完成 `flush` 与 `fsync` 后，通过 `os.link(..., follow_symlinks=False)` 原子创建目标，再删除临时链接。

目标已存在时失败，原字节不变。目录、符号链接、junction、非普通文件及无法使用所需硬链接操作的文件系统都会失败关闭；实现不会退回 `os.replace()` 或其他覆盖写入。清理也失败时，错误会保留原发布异常并给出残留临时文件的绝对路径。/ Export uses atomic no-overwrite hard-link publication. Existing targets and unsupported filesystems fail closed without an overwrite fallback; cleanup failures preserve the original cause and identify residual temporary state.

## 安全边界与限制 / Security boundary and limits

- 交换内容白名单仅包含名称、八项设备参数和优化策略。预设名称由用户输入，可能自行包含个人文字；除此之外不加入 CSV、运行结果、用户路径、报告路径或历史运行信息。/ Only the whitelisted preset fields are emitted. A user-entered name may itself contain personal text; no CSV, result or path field is added.
- 读写拒绝符号链接和 junction，并在打开或发布边界重复检查可观察的文件身份；这不是对持续替换祖先目录的高权限恶意本机进程的沙箱。/ Repeated identity and link checks are not a sandbox against a privileged hostile local process replacing ancestor directories.
- 本地预设库保存仍是单进程、整文件原子替换。多个进程同时写入时不提供合并或锁定保证。/ The local collection remains a single-process whole-file update with no multi-process merge or writer lock.
- 文件内容会 `fsync`，但父目录没有同步；不承诺断电后目录项持久性。/ File content is fsynced, but parent-directory durability after power loss is not guaranteed.
- 当前只支持 `format_version=1` 的单预设文件，不支持批量导入、自动迁移或覆盖导出。/ Only version 1 single-preset documents are supported; batch import, migration and overwrite export are out of scope.

## API 摘要 / API summary

- `serialize_preset_exchange(preset) -> bytes`: 生成严格、有界、无内部 ID 的 UTF-8 JSON。
- `load_preset_exchange(path) -> ScenarioPreset`: 只读解析并返回带新 ID 的完整有效预设，不修改本地库。
- `export_preset_exchange(store_path, preset_id, target) -> Path`: 从本地库按稳定 ID 导出，并原子拒绝覆盖目标。

所有接口失败均抛 `ScenarioPresetError`；调用方不得把失败或部分操作显示为成功。/ All failures raise `ScenarioPresetError`; callers must not present partial work as success.
