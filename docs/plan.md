# 实施计划 — 库表字段内容检索器（MssqlFieldFinder）

> 配套设计：`design.md`。原则：YAGNI + DRY + TDD（先测试后实现）。
> 环境：venv `C:\Users\admin\.workbuddy\binaries\python\envs\mssql_scanner`（系统 Python 3.13.12 派生，含 tkinter + pyodbc）。

## 目录结构

```
D:/Documents/WorkBuddy/2026-10-09-13-54-26/
├── main.py                 入口
├── MssqlFieldFinder.spec   PyInstaller 构建描述（构建参数的唯一来源）
├── requirements.txt        运行期依赖
├── requirements-build.txt  构建期依赖
├── .gitignore
├── bat/                    批处理脚本（三个 .bat 都放这里）
│   ├── run.bat             启动脚本（真实探测 tkinter）
│   ├── build.bat           一键打包
│   └── 诊断环境.bat        环境诊断
├── docs/                   文档（.md 都放这里）
│   ├── design.md
│   ├── plan.md
│   ├── review.md
│   ├── final_report.md
│   ├── 打包说明.md
│   └── images/             界面截图
├── src/
│   ├── __init__.py
│   ├── models.py           数据载体（ColumnRef / SearchHit / Progress…）
│   ├── matcher.py          匹配语义（纯逻辑）
│   ├── sqlbuild.py         标识符转义 / LIKE 转义 / COLLATE / 谓词构造（纯逻辑）
│   ├── drivers.py          ODBC 驱动优选（纯逻辑）
│   ├── connstr.py          连接串构造（纯逻辑）
│   ├── engine.py           元数据发现 + 线程池并发检索
│   ├── config.py           字段声明（schema 驱动 UI）+ 持久化
│   ├── widgets.py          滚动容器 / 表单行 / 结果表 / 日志面板
│   └── app.py              主窗口
└── tests/
    ├── test_matcher.py
    ├── test_sqlbuild.py
    ├── test_connstr.py
    ├── test_engine.py
    └── smoke_test.py       UI 自检（不进入 mainloop）
```

## 阶段一：纯逻辑层（TDD）

| # | 任务 | 产出 | 验证 |
| --- | --- | --- | --- |
| 1.1 | 定义数据载体 | `models.py`：`ColumnRef(schema, table, column, object_type, data_type)`、`Hit`、`MatchMode`、`CaseMode`、`ScanStats` | `ast.parse` 通过 |
| 1.2 | 写 `quote_ident` 测试 → 实现 | `sqlbuild.py` | 注入用例：`a]b` → `[a]]b]`；空串、`dbo`、含空格名 |
| 1.3 | 写 LIKE 转义测试 → 实现 | `sqlbuild.escape_like` | `%`→`\%`、`_`→`\_`、`[`→`\[`、`\`→`\\` |
| 1.4 | 写 COLLATE / 大小写映射测试 → 实现 | `sqlbuild.collation_for` | 忽略大小写→`Latin1_General_CI_AS`；区分→`CS_AS` |
| 1.5 | 写字段名匹配测试 → 实现 | `matcher.match_column_name` | 精准/模糊 × 忽略大小写 共 4 组 |
| 1.6 | 写内容谓词构造测试 → 实现 | `sqlbuild.build_content_predicate` | 精准→`=`+原值；模糊→`LIKE`+`%v%`；转义生效；collate 生效 |
| 1.7 | 写内容 SQL 构造测试 → 实现 | `sqlbuild.build_probe_sql` | 含 `NOLOCK`/`TOP`/`WHERE`/`ORDER BY` 开关组合 |
| 1.8 | 写驱动优选测试 → 实现 | `drivers.pick_driver` | 优先 17 → 11 → Native Client → SQL Server；均无则抛 `DriverNotFound` |
| 1.9 | 写连接串构造测试 → 实现 | `connstr.build` | SQL 认证含 `UID/PWD`；Windows 认证含 `Trusted_Connection=yes` 且不含 PWD；`TrustServerCertificate` 开关 |

**门禁**：`pytest tests/ -k "not engine and not smoke"` 全绿后方可进入阶段二。

## 阶段二：引擎层

| # | 任务 | 产出 | 验证 |
| --- | --- | --- | --- |
| 2.1 | 元数据发现 | `engine.fetch_columns(conn)` → `list[ColumnRef]` | 假连接测试：正确解析游标行、过滤类型 |
| 2.2 | 阶段 A 过滤 | `engine.filter_columns(refs, keyword, mode, case)` | 纯逻辑测试：4 组语义组合 |
| 2.3 | 工作线程连接管理 | `engine._worker` + `threading.local` | 假连接工厂测试：每线程各拿到独立连接且只创建一次 |
| 2.4 | 并发内容检索 | `engine.scan_content(targets, ...) -> Iterator[Event]` | 假连接测试：命中/未命中/超时/无权限四类事件；取消后不再派发新任务 |
| 2.5 | 连接工厂与上下文 | `engine.connect(profile)` 支持两种认证、`query_timeout` | 连接串与驱动选择被正确调用 |

## 阶段三：表现层

| # | 任务 | 产出 | 验证 |
| --- | --- | --- | --- |
| 3.1 | 字段声明 | `config.py`：`Field(...)` 列表，分组为「连接 / 检索条件 / 高级 / 持久化」 | 声明字段数断言 |
| 3.2 | 滚动容器与表单行 | `widgets.py`：`ScrollFrame`、`ScalarRow`、`ListEditor` | 自检断言控件可见 |
| 3.3 | 结果表与日志面板 | `widgets.py`：`ResultTable`、`LogPane` | 自检断言存在 |
| 3.4 | 主窗口装配 | `app.py`：左配置右结果；底栏状态条**先 pack**；`<Configure>` 设 sashpos；DPI 感知 | `smoke_test.py` |
| 3.5 | 后台执行与事件轮询 | `app.py`：`ThreadPoolExecutor` + `queue` + `after(80)` 消费；`destroy()` 时 `after_cancel` | 自检无 `invalid command name` |

## 阶段四：验证与交付

| # | 任务 | 验证 |
| --- | --- | --- |
| 4.1 | 单元测试全量执行 | 全绿，附输出 |
| 4.2 | UI 自检 | 多轮 `update()` 后断言关键控件 `winfo_ismapped()==1` 且高度 > 0 |
| 4.3 | 无数据库负向验证 | 未填服务器时点击「开始检索」给出校验提示而非崩溃 |
| 4.4 | 真实窗口截图 | PowerShell + GDI 抓图并用 Read 复核渲染效果 |
| 4.5 | 代码审查 | `review.md`，Critical 全修 |
| 4.6 | 启动脚本 | `bat\run.bat`（UTF-8 无 BOM + CRLF）、`bat\诊断环境.bat` |
| 4.7 | 最终报告 | `final_report.md` + `present_files` |

## 风险登记

| 风险 | 等级 | 应对 |
| --- | --- | --- |
| 托管 Python 无 tkinter | 已排除 | 已用系统 Python 建 venv，`import tkinter` 通过 |
| 目标机缺 ODBC 驱动 | 中 | 启动即探测，缺失时在界面给出安装指引 |
| 关闭窗口时 `after` 回调报错 | 中 | 保存 `after_id` 并在 `destroy()` 中 `after_cancel` |
| 生产库被长查询拖累 | 中 | 默认 `NOLOCK` + 语句超时 + 列数上限 |
| 同一文件并行编辑导致改动丢失 | 中 | 顺序编辑，改后读回校验 |

## 阶段五（v1.1 追加）：多套连接方案 + 结果逐字段复制

| # | 任务 | 产出 | 验证 |
| --- | --- | --- | --- |
| 5.1 | 方案存取纯逻辑（TDD） | `src/profiles.py`：`ProfileStore` / `load` / `save` / `extract_connection_values` / `ensure_seed` / `blank_passwords` | `tests/test_profiles.py` 39 项全绿；含白名单与 `config.FIELDS` 分组一致性守卫 |
| 5.2 | 结果表逐字段复制 | `src/widgets.py`：右键菜单 / 双击 / Ctrl+C 三条入口，`cell_at`、`cell_value`、`copy_cell`、`copy_row`、`copy_column`、`copy_all`、`tsv_for` | 界面用例覆盖九个字段逐一复制 |
| 5.3 | 界面接入 | `src/app.py`：配置方案区（下拉 + 保存/另存为/重命名/删除）、`_apply_profile`、`_persist_profiles`、结果提示行 | 界面用例覆盖切换/重命名/删除/密码策略/播种 |
| 5.4 | 测试隔离 | 所有界面用例把 `config_path` / `store_path` 重定向到临时目录 | 不触碰真实 `%APPDATA%` |
| 5.5 | 截图工具改造 | 进程内 GDI 截图 + 标准库 PNG 编码 | 规避 Tk `popdown` 浮层导致的抓错窗口 |
| 5.6 | 文档同步 | `design.md`（D8/D9）、`review.md`（第 8 节）、`final_report.md` | 与实现一致 |

**门禁**：`python -m unittest discover -s tests -t .`（164 项）与 `python tests/smoke_test.py`（76 项）全绿。

## 阶段六（v1.3 追加）：网格选中重做 + 连接设置弹窗 + 路径迁移

| # | 任务 | 产出 | 验证 |
| --- | --- | --- | --- |
| 6.1 | 路径解析与默认值裁剪（TDD） | `src/paths.py`；`config.save` 返回 `Path \| None` 且只写非默认项；`profiles.save` 最小化载荷 | `tests/test_paths.py`、`tests/test_config.py`、`tests/test_profiles.py` |
| 6.2 | 网格选中模型 | `src/widgets.py`：`GridSelection`（cell/row/col 互斥）、空白控制列、`_BorderOverlay` 四边高亮、`_sync_overlay` 同步策略 | `tests/smoke_test.py::TestGridSelection` |
| 6.3 | 复制入口收敛 | 右键固定四项；双击复制单元格；`copy_current()` 分派 Ctrl+C | 同上 |
| 6.4 | 连接设置弹窗 | `src/dialogs.py`：`ConnectionDialog` + 校验分级 + 驱动缺失红色超链接 | `tests/smoke_test.py::TestConnectionDialog` |
| 6.5 | 主窗口减负 | `src/app.py`：`GROUP_CONNECTION` 不参与主窗口渲染；配置方案区改「新增/编辑/删除」；连接参数改从方案库读取 | `TestMainWindowLayout` |
| 6.6 | 定时器与资源卫生 | `_cancel_timers()` + 根窗口 `<Destroy>` 兜底；`_poll` / `_reset_result_hint` 先取消在途定时器 | 以 stderr 无 `invalid command name` 为哨兵 |
| 6.7 | 截图工具固化 | `tools/window_capture.py`（进程内 GDI + 标准库 PNG） | 4 张截图归档到 `docs/images/` |
| 6.8 | 文档同步 | `design.md`（D10–D12）、`review.md`（第 9 节）、`final_report.md` | 与实现一致 |

**门禁**：`python -m unittest discover -s tests -t .`（216 项）与 `python tests/smoke_test.py`（102 项）全绿，
且执行后项目根目录不出现配置文件。

## 阶段七（v1.4 追加）：命中数据下钻 + 弹窗合并 + 主窗口再减负

| # | 任务 | 产出 | 验证 |
| --- | --- | --- | --- |
| 7.1 | 明细语句（TDD） | `sqlbuild.build_detail_sql`：与探测共用谓词，`SELECT TOP (n) *`，限长钳制 | `tests/test_sqlbuild.py::TestDetailSql` |
| 7.2 | 明细取数（TDD） | `engine.fetch_rows` / `fetch_rows_with`：返回列名与文本行，用完即关连接 | `tests/test_engine.py::TestFetchRows` |
| 7.3 | 结果表携带目标 | `ResultTable` 记录 `iid -> ColumnRef`，新增 `ref_for()`；双击改为回调 `on_open_detail` | `TestGridSelection` 双击与 `ref_for` 用例 |
| 7.4 | 命中数据窗口 | `dialogs.RowDetailDialog`：条件摘要 + 动态列 Treeview + 状态行 + 复制全部，后台取数 | `TestRowDetailDialog` |
| 7.5 | 双击门控 | `app._on_open_row_detail`：未启用内容检索直接返回；使用上一次实际执行的请求 | `TestMainWindowLayout` 双击门控用例 |
| 7.6 | 弹窗合并 | 记住密码（默认勾选）与测试连接移入 `ConnectionDialog`；底部指示灯四态 | `TestConnectionDialog` |
| 7.7 | 主窗口减负 | 移除「保存设置」按钮；关键词 `persist=False`；路径文字改为打开文件按钮 | `TestMainWindowLayout` |
| 7.8 | 文档与截图 | `design.md`（D13–D15）、`review.md`（第 10 节）、`final_report.md`；5 张截图归档 | 与实现一致 |

**门禁**：`python -m unittest discover -s tests -t .`（240 项）与 `python tests/smoke_test.py`（133 项）全绿，
stderr 无 `invalid command name`，且执行后项目根目录不出现配置文件。

## 阶段八（v1.5 追加）：文案换行 + 落盘时机 + 按方案记住密码 + 默认值与布局

| # | 任务 | 产出 | 验证 |
| --- | --- | --- | --- |
| 8.1 | 探测结果结构化（TDD） | `engine.ServerInfo` + `probe_server_info()`；`summary()`/`full_text()` 两种呈现视图 | `tests/test_engine.py::TestProbeServer` |
| 8.2 | 文案自动换行 | `_Lamp.set_wrap()`；弹窗按表单 `reqwidth` 计算换行宽度 | `TestConnectionDialog` 换行与「不撑宽窗口」用例 |
| 8.3 | 关键词写入但不读回（TDD） | `Field.persist` / `Field.restore` 拆分；`build_payload` 与 `load` 分别取用 | `tests/test_config.py::TestKeywordFields` |
| 8.4 | 检索时落盘 | `_on_start` 在校验通过后调用静默保存 | `test_start_writes_search_conditions_to_settings`、`test_start_does_not_write_when_validation_fails` |
| 8.5 | 记住密码按方案独立（TDD） | `ProfileStore.remember` 映射、`remember_for`、`upsert(remember=)`、`blank_passwords` 按方案；旧全局标志迁移 | `tests/test_profiles.py::TestPerProfileRemember` |
| 8.6 | 并发默认值按核数（TDD） | `config.default_workers()`、`effective_default()`；提示与启动日志说明依据 | `tests/test_config.py::TestWorkerDefault` |
| 8.7 | 提示文字布局 | 分隔条 38%、输入框收窄、提示换行列宽、短句化改写、结果提示动态折行、弹窗分隔线 | `TestMainWindowLayout`、`TestConnectionDialog` 相关断言 + 截图复核 |
| 8.8 | 文档与截图 | `design.md`（D16–D19）、`review.md`（第 11 节）、`final_report.md`；6 张截图归档 | 与实现一致 |

**门禁**：`python -m unittest discover -s tests -t .`（269 项）与 `python tests/smoke_test.py`（143 项）全绿，
stderr 无 `invalid command name`。

## 阶段九（v1.6 追加）：打包为可独立运行的可执行程序

| # | 任务 | 产出 | 验证 |
| --- | --- | --- | --- |
| 9.1 | 构建描述 | `MssqlFieldFinder.spec`：单文件、无控制台、排除无关库、显式 hiddenimports；兼容 `SPECPATH` 两种语义；程序名/版本由外部注入 | 构建成功产出 exe |
| 9.2 | 名称与版本单一来源 | `src/__init__.py` 的 `__app_name__` / `__version__`；窗口标题取版本，产物目录与文件名前缀取程序名 | `tests/test_cli.py::TestVersionCommand`、`TestAppNameCommand` |
| 9.3 | 一键打包脚本 | `bat\build.bat`（GBK+CRLF）：探测解释器 → 缺 PyInstaller 自动装 → 读程序名与版本 → 打包到同名目录 → 报产物与 SHA256；`-y` 免暂停 | `bat\build.bat -y` 端到端实测，退出码 0 |
| 9.4 | 部署自检命令 | `main.py --version` / `--app-name` / `--check`；窗口程序附着控制台，失败则写 `selfcheck.log` | `tests/test_cli.py::TestSelfCheck` |
| 9.5 | 打包文档 | `打包说明.md`：产物说明、环境要求、一键/手工命令、依赖清单、参数改动位置、FAQ | 与实际构建结果核对一致 |
| 9.6 | 产物实测 | 独立启动 + 界面渲染 + `--check` 输出 | `docs/images/exe_running.png` 截图归档 |
| 9.7 | 交付整理 | 清理 `build\`（14 MB 中间产物）；截图工具移入 `tools\` | 项目根目录只留源码、脚本与产物目录 `MssqlFieldFinder\` |

**门禁**：`python -m unittest discover -s tests -t .`（276 项）与 `python tests/smoke_test.py`（143 项）全绿；
`bat\build.bat -y` 退出码 0；打包产物可独立启动且 `--check` 正常。





