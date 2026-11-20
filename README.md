# meshkit — ANSYS Mechanical 2022 R1 自动网格 / 质量分析 / 自动纠正

在 Mechanical 里跑一段脚本，自动完成：

**按你的要求划网格 → 读数据判断网格好坏 → 找出问题根因 → 自动改网格设置重划 → 改不动就把「需要你拍板的事」整理成清单**

- 目标环境：ANSYS 2022 R1（`AWP_ROOT221`，实测版本 22.1.0.0），Mechanical 内置 IronPython 2.7.4
- 语言/依赖：纯 IronPython，无 numpy、无外部包、不需要 PyMechanical
- 自动纠正范围：**只改网格设置**（尺寸、方法、平滑、曲率/近距捕捉、defeature）；改几何、改模型的事一律不自动做

---

## 一、30 秒上手

### 第 0 步：先做只读体检（10 秒，强烈建议）

打开 Mechanical（Workbench 里双击 Model），顶部 **Automation → Scripting**，在输入框里：

```python
execfile(r"D:\ai\meshkit\probe_env.py")
```

它只读、不划网格、不改任何设置，输出 `D:\ai\meshkit\reports\环境探针_<时间>.txt`：
版本、体数、现有 17 个控制项的明细、12 个质量指标的读数、Mechanical 自己的质量限值。

**这一步能提前暴露 90% 的环境问题**（比如控制项作用域失效、指标读不到）。

> 按钮在哪、报错怎么办、结果怎么读：见 **[如何运行_环境探针.md](如何运行_环境探针.md)**（逐步说明 + 报错对照表）。

### 第 1 步：干跑一次（不改任何东西）

把 `mesh_requirements.json` 里 `run.dry_run` 改成 `true`，然后：

```python
execfile(r"D:\ai\meshkit\run_mesh.py")
```

看报告 `reports\网格自动化报告_<时间>.md`：诊断对不对、建议合不合理。

### 第 2 步：真跑

`run.dry_run` 改回 `false`，再执行同一条 `execfile`。脚本会按需求划网格、判质量、按阶梯自动纠正，最后出报告。

---

## 二、它做什么 / 不做什么

### 做

| 能力 | 说明 |
|---|---|
| 按需求设网格 | 全局尺寸/平滑/曲率/defeature + 任意个局部控制项（按名字复用已有控制项，缺的才新建） |
| 带验证地划网格 | 检测 `GenerateMesh` 空转（单元数没变），自动 `ClearGeneratedData` 后重试 |
| 数据化质量评价 | 12 个指标逐一切换 `MeshMetric` 读 Min/Max/Avg/Std，按阈值判 PASS/WARN/FAIL，并算 0~100 综合得分 |
| 根因诊断 | 20 多条规则，把"现象"归到"哪个体/哪个控制项/哪种几何缺陷" |
| 自动纠正 | 7 级阶梯，一轮推进一步，每步写后校验、可回滚 |
| 挑最优 | 每轮记惩罚值，跑完可回到最优那一轮的设置重新生成（`keep_best`） |
| 人工决策清单 | 自动层改不动的（碎边、拓扑、薄板、抑制件）整理成清单 + 一份**默认全关**的补救脚本 |
| 全留痕 | Markdown 报告 + CSV 指标 + 运行日志 + 环境能力探测，方便复盘 |

### 不做（刻意的边界）

- ✗ 不自动改几何（defeature 只走 Mechanical 的网格级 Defeature Tolerance，不动 CAD 实体）
- ✗ 不自动抑制体、不自动改壳/实体、不自动加剖分面（这些会改物理结果）
- ✗ 不自动保存工程（`run.save_project=false` 时必须你手动保存）
- ✗ 不自动执行它自己生成的补救脚本（生成物里每个操作块都是 `ENABLE = False`）
- ✗ 不读 Mechanical 的 Messages 面板文本（2022 R1 没有稳定脚本接口），改从「指标 + 每体单元数 + 几何扫描 + 异常文本」反推根因

---

## 三、目录结构

```
D:\ai\meshkit\
├─ run_mesh.py                 ← 入口：在 Mechanical 里 execfile 这个
├─ probe_env.py                ← 只读环境探针（先跑这个）
├─ 如何运行_环境探针.md         ← 探针逐步操作说明 + 报错对照表
├─ mesh_requirements.json      ← 你的需求（尺寸/阈值/预算/纠正策略）
├─ presets\
│  ├─ container_1000c4.json    ← 你的集装箱配方（17 个控制项 + 7.5M 节点预算）
│  └─ generic_default.json     ← 通用默认
├─ meshkit\                    ← 引擎
│  ├─ compat.py                日志/单位/Py2-3 兼容
│  ├─ config.py                需求加载、合并、校验
│  ├─ session.py               ExtAPI 访问层（特征探测 + 写后校验）
│  ├─ metrics.py               指标采集、限值、判定、得分、可选单元抽样
│  ├─ controls.py              全局设置 + 局部控制项的读/建/改 + 快照回滚
│  ├─ mesher.py                应用需求、划网格、现场快照、碎边扫描
│  ├─ diagnose.py              规则引擎：现象 → 根因 → 动作
│  ├─ repair.py                自动纠正阶梯（只动网格设置）
│  ├─ advisor.py               人工决策清单 + 补救脚本生成
│  ├─ report.py                Markdown / CSV / TXT 报告
│  └─ engine.py                闭环编排
├─ reports\                    ← 产出（报告/CSV/日志/清单/补救脚本）
└─ tests\                      ← 自测（用假 ExtAPI 跑整条闭环）
```

---

## 四、需求配置怎么改（`mesh_requirements.json`）

只讲会真正影响结果的字段，其余按注释猜即可。

### `run` — 怎么跑

| 字段 | 默认 | 说明 |
|---|---|---|
| `dry_run` | `false` | `true`=只读分析，不改任何设置、不重划 |
| `auto_repair` | `true` | `false`=只诊断不纠正 |
| `max_rounds` | `4` | 最多几轮"测量→纠正→重划"。问题多时给 6~8 |
| `keep_best` | `true` | 跑完回到综合分最好的那一轮设置并重新生成 |
| `save_project` | `false` | 结束时是否 `Save()`（默认不保存） |
| `report_dir` | `reports` | 产出目录 |
| `element_sampling.enabled` | `false` | 单元级抽样（按边长比挑最差的体）。100 万级单元会明显变慢，需要时再开 |

### `mesh` — 全局设置

`element_size_mm`、`defeature_tolerance_mm` 支持 `36` / `"36 mm"` / `"2 cm"` / `"0.5 in"` 写法。

`curvature_normal_angle_deg = 0` 表示**程序控制、不写这个属性**（写 0 在某些版本非法）。

`smoothing` 用 `Low` / `Medium` / `High`；`element_order` 用 `ProgramControlled` / `First` / `Second`。

### `budget` — 单元/节点预算

```json
"budget": { "basis": "nodes", "target": 7500000, "hard_cap": 9000000, "size_exponent": 2.14 }
```

- `basis`：`nodes` 或 `elements`
- `size_exponent`：尺寸—规模关系的指数（你机器上实测标定过 2.14：尺寸缩小 → 节点数 ∝ 尺寸^-2.14）
- 超 `hard_cap` → 报错级问题，自动按该指数放缩尺寸；超 `target` → 仅警告

### `controls` — 局部网格控制项

```json
{"name": "角件", "type": "body_sizing", "size_mm": 10, "bodies": ["角件*"], "role": "load"}
{"name": "Patch Conforming Method", "type": "automatic_method",
 "method": "AllTriAllTet", "bodies": ["支梁架", "支梁*"], "role": "tet"}
```

- `type`：`body_sizing` / `face_sizing` / `automatic_method` / `face_meshing` / `refinement`
- `bodies`：通配符，**同时匹配体全名和体名最后一段**（`底板2\固体4` 的最后一段是 `固体4`，写 `固体4` 或 `固体*` 都能命中）
- `reuse_existing`（默认 `true`）：按名字复用已有控制项 → **只改尺寸，不新建重复控制**。你那 17 个控制项就靠这个机制被接管
- `create_if_missing`（默认 `true`）：模型里没这个控制项时才新建
- `role`：只用于报告分组（`base` 基础 / `load` 承力 / `critical` 重点 / `shell` 壳面 / `tet` 四面体），不影响求解

> ⚠ 首次真跑前请先看探针输出里的控制项清单：如果 `bodies` 通配符没匹配上，报告里会明说
> "控制项 X 没有匹配到任何体"，此时它作用域为空、等于没设。把通配符改成探针里的真实体名即可。

### `quality` — 判定阈值

`use_mechanical_limits = true` 时，配置里没写阈值的指标会用 **Mechanical 自己的 warning/error 限值**（从 `GetVolumeMeshQualityWarningLimit(i)` 按索引读）。

阈值方向必须自洽：`direction: high`（越大越好，如 ElementQuality）要 `error < warn`；`low` 反之。写反了 `config.validate` 会报出来。

### `repair` — 纠正策略

`ladder` 是阶梯顺序，可删可调；`defeature_factor_ladder` 是 defeature 相对全局尺寸的尝试比例；`allow_element_order_change` 默认 `false`（改单元阶次会影响精度，必须你显式同意）。

---

## 五、自动纠正阶梯（只动网格设置）

按 `repair.ladder` 顺序，**一轮只推进一步**，这样"改了什么 → 指标怎么变"可归因。

| 步骤 | 触发条件 | 动作 | 为什么有效 |
|---|---|---|---|
| `smoothing` | 任一指标 WARN/FAIL，且 Smoothing≠High 或迭代<10 | Smoothing=High、迭代 10、后平滑开 | 平均质量提升最便宜的一步 |
| `zero_size_controls` | 有 sizing 类控制项 Element Size=0，或作用域为空 | 0 尺寸→同组中位数（夹在全局 0.25~1.5 倍）；空作用域按 `bodies` 重新指定 | 0 尺寸=控制项不存在，是雅可比报警的常见真凶 |
| `defeature` | 几何最小边长 < 全局尺寸×0.5%，且 defeature 还很小 | 按 1%/3%/6% 全局尺寸逐级提高 Defeature Tolerance，并开自动 defeature | 直接消掉碎边造成的针状单元 |
| `unmeshed_bodies` | 有体没划上网 / 划网格报错 | 给这些体补 Body Sizing + AllTriAllTet 方法，必要时清空重划 | 绕开失败的四边形/扫掠网格器 |
| `quad_shell` | 有壳体 + QuadMesh 开 + MinTriAngle 差 | QuadMesh=False，并对壳面加 AllTriAllTet | 四边形网格器在中面碎面处必然退化 |
| `proximity` | ≥50 体 + Skewness 差 + 未开近距捕捉 | 开 Capture Proximity（先按 1.15 倍预估，超预算就不开） | 窄缝/贴合面处最有效 |
| `budget` | 节点/单元超 target 或 cap | 按 `(当前/目标)^(1/指数)` 放缩全部尺寸，最多缩放 1.8 倍，最多试 3 次 | 一次放缩往往不够，允许迭代 |

每步执行后都会读回校验；阶梯走完还没达标 → 出人工决策清单。

---

## 六、诊断规则表（报告里以 `代码` 出现）

| 代码 | 含义 | 级别 | 能否自动修 |
|---|---|---|---|
| `MESH_FAILED` | 划网格报错/未生成 | error | 部分（清空重试、补控制项） |
| `BODY_UNMESHED` | 有体没划上网 | error | 能（补尺寸/方法） |
| `ZERO_SIZE_CONTROL` | 控制项尺寸=0（等于没设） | error | 能 |
| `CONTROL_NO_SCOPE` | 控制项作用域为空 | warning | 能（配了 bodies 时） |
| `BUDGET_EXCEEDED` / `BUDGET_OVER_TARGET` | 超预算硬上限 / 超目标 | error / warning | 能（放缩尺寸） |
| `DEGENERATE_GEOMETRY` | 几何碎边（如 1.18e-4 mm） | error/warning | 部分（defeature）→ 根治要回 CAD |
| `DEFEATURE_TOO_LARGE` | Defeature 相对全局尺寸过大 | warning | 不改（会吃掉特征，需你决定） |
| `SHELL_PRESENT` / `SHELL_QUAD_FAIL` | 有壳体 / 四边形网格器失败风险 | info / warning | 能（三角形优先） |
| `SMOOTHING_LOW` / `CURVATURE_OFF` / `PROXIMITY_OFF` | 设置偏保守 | info | 能 |
| `SIZE_JUMP` | 局部尺寸跳变 ≥20:1 | warning | 不改（建议加过渡级） |
| `METRIC_*` | 单个质量指标越界（ElementQuality / Skewness / JacobianRatio / AspectRatio / TetCollapse / 各种夹角…） | error/warning | 能（按同源根因处理） |

每条诊断在报告里都带 **证据**（具体数值 + 哪个体/控制项）、**根因**、**已执行的动作**、**人工建议**。

---

## 七、报告怎么读

`reports\网格自动化报告_<时间>.md` 共 8 节：

0. **结论速览** — 网格生成了吗、得分多少、纠正了什么、还剩什么
1. **环境与生效需求** — 版本、体数、实际生效的全局设置和控制项（对照"我要什么"）
2. **迭代过程** — 每轮：单元/节点、得分、测量后采取的动作
3. **最终网格** — 规模、状态、几何最小边长、控制项健康度、单元数最多的体
4. **质量指标** — 12 个指标的 min/max/avg/std + warning/error 限值 + 判定
5. **诊断与自动处理** — 每条问题的证据/根因/执行情况
6. **自动纠正明细** — 每一步具体把什么从多少改成多少
7. **下一步** — 该看哪份清单、复跑命令
8. **产出文件 + 环境能力探测** — 便于我（或你）远程判断问题

配套产出：
- `网格质量指标_<时间>.csv` — 指标 + 每轮规模，方便丢 Excel 做趋势
- `网格摘要_<时间>.txt` — 控制台可读的精简版
- `人工决策清单_<时间>.md` — 需要你拍板的事项（按性价比排序）
- `apply_manual_fix_<时间>.py` — 补救脚本，**默认全关**，逐条确认再开
- `run_<时间>.log` — 完整日志

---

## 八、安全机制（为什么可以放心跑）

1. **dry-run**：默认配置里就有开关，先看清要改什么再真跑。
2. **状态快照 + 回滚**：每轮开始前抓一份 `全局设置 + 每个控制项的尺寸/方法/抑制`，跑完按惩罚值选最优；`keep_best=true` 时回滚到最优那轮重新生成。
3. **写后校验**：每个 setter 都读回比对（长度按 mm、枚举按名字或整数表），不一致就记失败，不会"以为改了其实没改"。
4. **只动网格设置**：几何/模型层的操作只进清单，不进自动流程。
5. **不自动保存**：`save_project` 默认 `false`，磁盘上的 `.wbpj/.mechdb` 不会被你不知情地覆盖。
6. **异常兜底**：任何未捕获异常都会写进报告和日志，并尽量把工程留在可用状态（不会一半设置改了就跑）。
7. **空转检测**：`GenerateMesh` 不报错但单元数没变（Mechanical 的经典坑）会被识别并清空重试。

---

## 九、验证到什么程度（诚实说明）

**已经验证**（在你这台机器自带的 IronPython 2.7.4 / .NET 4.0 上真实运行）：

- 13 个场景、68 项断言全部通过：`tests\run_tests.py`
- 覆盖：干净模型一轮通过、0 尺寸控制 + 退化几何 + 壳面四边形失败的复合修复、预算放缩、
  首次失败清空重试、持续报错→人工清单、怎么改都修不好→人工清单、dry-run 不改设置、
  未划网的体补控制后恢复、**状态回滚（改坏→apply_state→逐项复原）**、
  **单元抽样接口不可用时的降级**、**探针脚本本身端到端跑通**、报告章节完整性
- 全部 `.py`（含生成的补救脚本）用目标解释器编译通过
- 用假 ExtAPI 驱动，验证的是**引擎逻辑、状态机、诊断规则、报告/清单生成**

**还没有验证**（需要在你的真实模型上跑一次才知道）：

- Mechanical 里 `body.Id` / `GetGeoReferenceId()` 取几何 id 的具体可用性（代码里试了 4 种路径 + 校验，失败会降级到"当前选择集"策略并记日志，不会崩）
- `Mesh.Method` / `Algorithm` / `Smoothing` 枚举的写入方式（代码里先按 .NET 枚举类型 `Enum.Parse`，再退字符串、再退整数表，逐次读回校验）
- 7.5M 节点规模下读 12 个指标的耗时（每个指标要重算一次云图，百万级单元通常几十秒到几分钟）

这些都不影响安全（失败会降级 + 记录），但**建议先跑 `probe_env.py`**，把探针输出发我，我按你的真实 API 表现微调。

---

## 十、常见问题

**Q: 报 `找不到 ExtAPI`**
A: 必须在 Mechanical 的 Automation → Scripting 里跑（不是 Workbench 的脚本窗口，也不是系统 python）。

**Q: 控制项全说"没有匹配到任何体"**
A: `bodies` 通配符和真实体名不符。跑 `probe_env.py` 看真实体名，或直接写体名。你的模型体名是 `底板2\固体4` 这种带反斜杠的，脚本匹配最后一段。

**Q: 指标一个都读不到**
A: `MeshMetricType` 枚举不可用或网格为空。先划一次网格，再看探针输出的 `metric 枚举=有/无`。

**Q: 划网格"成功"但单元数是 0 或没变**
A: 脚本会自动 `ClearGeneratedData` 重试一次；仍失败会在报告里标 `MESH_FAILED`。常见原因是几何未更新（先在 Workbench 里 Update 几何）。

**Q: 自动纠正跑了好几轮还是 NEEDS_HUMAN**
A: 说明剩下的问题在几何/模型层（碎边、薄片、抑制件）。看 `人工决策清单_*.md`，里面按性价比排了序。

**Q: 只想看质量、不想动网格**
A: `run.dry_run = true`，或 `run.auto_repair = false`（前者连设置都不改）。

**Q: 想换一个模型用**
A: 改 `mesh_requirements.json`：`preset` 换成 `generic_default`（或复制一份自定义预设），重写 `controls` 的 `bodies` 通配符与尺寸。

---

## 十一、和你 D:\ai\mech 里旧脚本的关系

`D:\ai\mech\` 里的 `audit_mesh.py` / `mech_metrics.py` / `mech_optimize.py` 是**一次性只读审计/探索脚本**，验证了一批 API 事实（`Mesh.MeshMetric` 可写、`Minimum/Maximum/Average/StandardDeviation` 可读、`GetVolumeMeshQualityWarningLimit(i)` 需要索引、0 尺寸控制项确实存在、中文名必须走 `unicode`）。meshkit 把这些结论固化成了可复用引擎：

- 旧脚本：单文件、写死输出路径、只读、不改设置、报告靠人看
- meshkit：全特征探测、需求可配、可自动纠正、有回滚、有报告与清单、有自测

`D:\ai\mech\网格质量检查报告.md` 和 `Mesh配置模板_集装箱.md` 里的实测数据（1,851,310 单元 / 3,808,463 节点、ElementQuality 0.00515、Skewness 0.9999999、四面体 89.9%、`中隔断` 0 mm 等）已经变成集装箱预设与诊断规则的依据。
