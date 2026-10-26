# 本地运行历史保存 / Local run history storage

## 选择与恢复 / Selection and restoration

默认运行根目录仍为系统临时目录下的 `gridwise-campus-runs`，兼容 v0.3.8；操作系统可能清理它。需要长期保留时，先自行创建受控的本地目录，在桌面界面选择该目录并点击“保存目录 / Save root”。保存动作仅写入一个小型本地设置文件，**不会搬迁或删除旧运行**。目录路径始终显示在界面中。/ The default remains the OS temporary `gridwise-campus-runs` directory. To retain runs, create a controlled local directory, select it and explicitly save it. Saving the preference does not move or delete existing runs.

Windows 设置路径为 `%LOCALAPPDATA%\GridWiseCampus\desktop.json`；其他系统遵循 `$XDG_CONFIG_HOME/GridWiseCampus/desktop.json` 或 `~/.config/GridWiseCampus/desktop.json`。文件只记录格式版本与绝对运行目录路径，不记录输入数据。启动时若设置有效，应用加载该目录并异步刷新历史；若设置损坏、不可读或目录消失，则显示错误并退回临时默认目录，不自动改写设置。测试通过注入临时设置路径，未写入真实用户设置。/ The settings file stores only a version and absolute path. A valid saved root is loaded and scanned after restart. Invalid settings cause a visible fallback to the temporary default without rewriting the file.

运行根目录必须已经存在，且不能是符号链接或 junction。程序保存设置时先验证既有文件格式，写入同目录临时文件后替换，并清理本次临时文件。此操作只是**单文件**更新，不能保证断电持久性或抵御恶意本机写入者。/ The target directory must already exist and must not be a link or junction. The preference uses a one-file replacement; it is not a power-loss or adversarial-writer guarantee.

## 保留、备份与清理 / Retention, backup and cleanup

每次求解创建唯一的 `run-<32 lowercase hex>` 目录。成功、未完成和损坏目录均不会自动删除或修复。若要备份，请在运行结束后复制**整个**运行目录，保留 `manifest.json` 与 `COMPLETE`；恢复后重新通过 `read_verified_run()` 或界面的“刷新运行 / Refresh runs”和“读取所选 / Load selected”校验。仅复制三份结果文件不能形成有效的已校验运行。/ Every run has its own directory. Back up a complete finished directory including manifest and marker, then reverify it after restoration. Partial copies are not verified runs.

清理须由用户在应用关闭、相关读取和求解结束后手动决定；先辨认目录归属并备份需要保留的运行。本程序不会删除来源不明的目录或自动清空未完成产物。默认临时目录中的历史不应被视为长期档案。/ Cleanup is a manual decision after the app and jobs close. The app never deletes unknown or incomplete directories automatically.

`VALID` 表示本次大小、哈希、清单和标记检查通过；选择历史运行时会再次检查并与当前已通过质量检查的 CSV 对齐。文件哈希、Git 提交和完成标记均不证明输入真实、数据来源可信或求解在当前代码下仍最优。见[运行产物契约](run-artifact-contract.md)。/ `VALID` describes a current integrity check, not provenance, authenticity or current-code optimality.
