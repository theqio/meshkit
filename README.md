# MeshKit AI

> ANSYS Mechanical 2022 R1 的**网格智能助手**：本地网页界面 + Mechanical 桥接，
> 自动做「几何识别分组 → 批量设置网格 → 生成网格 → 失败体调参 → 质量分析」。

不依赖 PyMechanical、不装第三方包，只用 Python 标准库 + Mechanical 内置 IronPython。

---

## 一、快速开始

### 环境要求

| 项 | 要求 |
|---|---|
| ANSYS | 2022 R1（22.1），Mechanical 已打开模型 |
| Python | 3.9+（启动脚本会自动找：优先本机 Python，其次 DSH 自带运行时）|
| 系统 | Windows（用到窗口控制做保存）|

### 三步启动

```
1) 双击  app\启动 MeshKit AI.bat
   → 会启动本地服务并打开 http://127.0.0.1:8765

2) 在 Mechanical 里点工具栏的 MeshKit_Check 按钮
   （或 Scripting 里执行：execfile(r"D:/ai/meshkit/app/bridge_agent.py")）
   → 页面右上角「桥接」变绿

3) 页面里按 ①→②→… 走流程
```

> 每次**重启 Mechanical** 后，都要重新点一次 `MeshKit_Check` 加载桥接。

---

## 二、七步流程

| 步骤 | 界面按钮 | 做什么 |
|---|---|---|
| 1 | 桥接+单位 | 页面自动设单位制为 mm |
| 2 | ① 扫描模型 | 纯几何识别，给出建议分组（不改模型）|
| 3 | ② Geometry 重命名+分组 | 在 Geometry 树里改名 `部位_序号` + 建分组文件夹 |
| 4 | 分组卡片 | 可 改名 / 新建 / 删除 / 撤销删除 / 移体到其他组 |
| 5 | 应用设置与尺寸 | 按类别批量建 Mesh 控制项（Sizing / Method）|
| 6 | 生成网格 | 预检查 → 生成 → 列出失败体（可逐体调参重划）|
| 7 | 质量报告 / 圆孔质量 | 看指标、出报告 |

---

## 三、核心能力

### 1. 纯几何识别分组（不看名字）

名字是乱码也能分对。判定依据：

| 依据 | 用途 |
|---|---|
| 三向尺寸 | 主要分类（如 8×60×200 → 垫板）|
| 填充率（体积/包围盒）| 区分实心 / 空心壁厚 |
| 质心位置 | 上 / 中 / 下层、内 / 外侧 |
| 相互接触关系 | 装配体成组（如 13 体互接触 → 上支撑梁）|
| 板的朝向 | YZ 大平面=中隔断薄板；XZ 大平面=底板 |

当前规则表覆盖 **23 个部位**（`app/part_rules.json`），识别不到的归「其他」。

### 2. 分组规则

- 七条结构分组原则（见 `MeshKitAI_规则总表.md` 5.4）
- **同尺寸同形状强制一组**（最高优先级，即使内外侧/上中下不同）
- 分组结果落地方式：先解散全部 → 再按最新状态重建（保证扁平等，不嵌套）

### 3. 手动调整不会丢

界面上的移动 / 新建 / 改名以**体 Id** 记录，改名不影响；落地时同步回 Mechanical。

### 4. 薄板自动识别

```
板状截面（宽度/厚度 ≥ 3 且厚度 ≤ 8mm） 或 （截面 ≤60mm 且壁厚 ≤4.5mm）
→ 类别内过半是板状 → 判薄板 → 方法=扫掠，层数=2
```

方管/箱型梁（截面接近方形）**不会**被误判为薄板。

### 5. 类别表三色灯

| 灯 | 含义 |
|---|---|
| 🟢 绿 | Mesh 里已有控制项，尺寸合理 |
| 🟡 黄 | 有控制项但有问题（没尺寸 / 小于最小尺寸 / 扫掠不适用）|
| 🔴 红 | Mesh 里没有该类别控制项 |
| ⚪ 灰 | 该类全部体被抑制 |

### 6. 失败体逐体调参

生成网格后只列**本次运行真正失败**的体（黄色），粉色「过期」不算失败。每行可单独设：

- **尺寸** → 建 `SIZE_FIX_<体名>` 控制项
- **方法** → 建 `METHOD_<体名>` 控制项（自动 / 扫掠 / 扫掠+Automatic Thin）
- **去特征** → 写该体 Sizing 的 `LocalDefeaturingTolerance`（**只对该体生效**）

设置后会把该体从原类别控制项范围里摘除，避免两个控制项冲突。
重划成功后该行保留并显示绿灯。

### 7. 网格消息分析

生成网格后自动分析 Mechanical 的 Messages（**只取本次运行之后的**），按问题归类并给建议：

```
单元质量差 / 几何面朝向异常 / 网格生成失败 / 面成功体失败 /
质量指标未达标 / 最小尺寸冲突 / 几何自相交 / 退化几何 …
```

结果写在右侧「AI 诊断」，也可点「分析网格消息」手动重跑。

### 8. 保存策略

只在 **Mechanical 窗口内**发 Ctrl+S：不碰 Workbench、不改变窗口大小/位置。
保存发生在：② 落地后、应用设置后（生成网格后不自动保存）。

---

## 四、目录结构

```
meshkit/
├─ app/
│  ├─ server.py           本地 Web 服务（标准库 ThreadingHTTPServer :8765）
│  ├─ bridge_agent.py     跑在 Mechanical 里的桥接代理（IronPython 2.7）
│  ├─ geo_recognize.py    纯几何识别（分组规则）
│  ├─ part_rules.json     部位尺寸表（23 部位）
│  ├─ experience.py       经验库（问题→动作→效果）
│  ├─ mesh_risk.py        风险预判、sizing_advisor.py 尺寸建议
│  ├─ static/index.html   前端页面（单文件）
│  ├─ tools/save_mech.ps1 只保存 Mechanical 的脚本
│  └─ 启动 MeshKit AI.bat  一键启动
├─ meshkit/               网格引擎包（engine / controls / metrics / report …）
├─ presets/               预设配置（集装箱 1000c4 等）
├─ tests/                 测试
├─ reports/               结构分析报告 + 最新运行报告
└─ MeshKitAI_规则总表.md    权威规则（配置约束、分组原则、薄板规则）
```

---

## 五、通信机制

浏览器 ⇄ `server.py` ⇄ 文件 ⇄ `bridge_agent.py`（Mechanical 内）

```
app/ipc/cmd_<pid>.json      页面下发的命令
app/ipc/result_<pid>.json   代理回传的结果
app/ipc/agents/<pid>.json   已注册的 Mechanical 实例（支持多实例切换）
```

页面每 15 秒读一次状态；失败体与错误日志只在生成网格后读一次（不轮询）。

---

## 六、关键约束（务必遵守）

1. **只有波纹板改壳单元（中面）**，其他一律实体
2. **MinimumSize ≥ DefeatureSize**，否则网格静默失败
3. **程序绝不自动切换 Mesh Metric**（曾导致 Mechanical 闪退）
4. **扫掠只用于规则形状**（面 ≤30 / 边 ≤90 / 长细比 ≥4）
5. **薄板扫掠 2 层；长构件用 Automatic Thin 且不设层数**
6. 单元数排行等重负载功能只允许手动触发

详见 `MeshKitAI_规则总表.md`。

---

## 七、常见问题

| 现象 | 处理 |
|---|---|
| 页面显示「桥接未连接」 | Mechanical 里点一次 `MeshKit_Check`；确认模型已打开 |
| 保存失败 | 确认 Mechanical 窗口在、未最小化；`app/tools/save_mech.ps1` 可单独测试 |
| 端口被占 | 设环境变量 `MESHKIT_PORT=8766` 再启动 |
| 文件写入后被加密/乱码 | 本机装了 DLP（Esafenet）会加密文本文件；用 Python 读写，勿用记事本直接改 |
| 识别少了几个体 | 看是否有重名体（同名会合并计数）；页面「扫描」里会显示体总数 |

### 2022 R1 的已知限制

- 没有 `Project.Save()` / `SaveProject()` → 只能 UI Ctrl+S
- 没有 `Mesh.GenerateMesh(体)` → 只能整模型重划
- 抛体状态只有 `Meshed` / `FullyDefined`，失败与否需结合 `Application.Messages`

---

## 八、开发和自检

```powershell
# 改完前端必跑（语法/事件绑定/id 完整性/无控制台报错）
python app\check_ui.py

# 改完服务端或代理后跑（对实时数据核对 5 类不变量）
python app\check_pipeline.py
```

代理改动后必须在 Mechanical 里重新点 `MeshKit_Check` 才生效，可用
`/api/op {"op":"ping"}` 查 `version` 确认加载的是新版本。

---

## 九、许可

内部工具，未开源授权。
