# MeshKit AI

ANSYS Mechanical 网格自动化助手：识别零件 → 自动分组/命名 → 按类别定尺寸与方法 → 生成网格 → 质量与失败体分析 → 自动修复。

浏览器界面（本地网页） ⇄ 本地服务（Python） ⇄ Mechanical 内的桥接代理（IronPython）。

**适用版本**：ANSYS 2022 R1（v221）

---

## 一、在新电脑上安装

### 1. 前置条件

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11 |
| ANSYS | 2022 R1（Mechanical 能正常打开） |
| Python | 3.8+（推荐 3.10/3.11，需在 PATH 里） |

检查 Python：

```bat
python --version
```

如果没有 Python，去 https://www.python.org/downloads/ 安装，安装时勾选 **Add Python to PATH**。

### 2. 获取程序

**方式 A（推荐，用 Git）**

```bat
git clone https://github.com/theqio/meshkit.git meshkit
```

可以克隆到任意目录（当前目录下会生成 `meshkit` 文件夹）。

**方式 B（用压缩包）**

把压缩包解压到**任意目录**（例如 `D:\tools\meshkit`、`C:\Users\你\meshkit` 都可以）。
程序会自动识别自己的安装位置，不需要改任何配置。

### 3. 启动服务

双击解压目录里的 **`加载桥接.bat`**

它会：
1. 在后台启动网页服务（端口 8765）
2. 打开浏览器到 http://127.0.0.1:8765/

也可以手动启动：

```bat
cd /d <你的安装目录>\app
python server.py
```

然后浏览器访问 **http://127.0.0.1:8765/**

### 4. 在 Mechanical 里加载桥接（每个 Mechanical 窗口一次）

1. 打开 Mechanical（你的 `.wbpj` 工程）
2. 顶部切到 **Automation** 选项卡 → 点 **Scripting**
3. 命令不用手写：双击 `加载桥接.bat` 时它已经**自动生成并复制到剪贴板**，
   直接在该脚本框里 `Ctrl+V` 粘贴即可（内容形如）：

```python
import os;os.environ["MESHKIT_ROOT"]=r"<你的安装目录>";execfile(r"<你的安装目录>/app/bridge_agent.py")
```

4. 按 **Ctrl+F5** 运行
5. 看到 `[bridge] 已启动` 即成功（底部状态栏也会显示）

> 只需一次。重复执行不会叠加（新版入口是幂等的）。

### 5. 页面连接

回到网页：

1. 右上角下拉选你的 Mechanical 实例（会显示 `工程名 · PID`）
2. 点「连接」（只激活窗口 + 复制命令，不会自动执行）
3. 标题栏出现 `● 已连接 Mechanical` 即可使用

---

## 二、日常使用流程（左侧栏 1→7）

| 步骤 | 按钮 | 作用 |
|---|---|---|
| ① | 扫描模型 | 纯几何识别，把体归到 20~23 个部位类别 |
| ② | Geometry 重命名+分组 | 按 `部位_序号` 重命名并在 Geometry 树建分组 |
| ③ | 取消全部分组 | 需要重来的时候用 |
| ④ | 分组 | 手动调整（拖体换组） |
| ⑤ | 应用设置与尺寸 | 把全局设置 + 各类尺寸/方法写进 Mesh 控制项 |
| ⑥ | 生成网格 | 先预估单元/节点数，超预算会提示，再生成 |
| ⑦ | 质量分析 | 质量指标、失败体清单、逐体风险排序 |

**失败体处理**（中间栏）：

- 「自动修复失败体」：自动减半尺寸 + 从类别控制项摘出 + 重划（最多 2 轮）
- 「一键应用全部 / 应用并重划」：按表格里填的尺寸批量设置
- 单行「应用」：只改那个体的尺寸/方法/去特征

---

## 三、识别规则（要点）

- **不看名字**，只看几何：尺寸组合（排序后三元组）、填充率、质心、接触图、板厚
- 尺寸匹配：每轴相对误差 ≤20%，最大相对误差 ≤15% 才接受最近的类别
- 薄板判定：`最薄边 ≤8mm 且 长宽比 ≥3`，或 `长宽比 ≥3 且 最薄边 ≤60mm 且 壁厚 ≤4.5mm`；同类中薄板体过半 → 自动标薄板
- 薄板类别：方法 = 扫掠 + 厚度方向 2 层
- `其他 / 未分组` 里的体永远不改名（规则见 `MeshKitAI_规则总表.md`）

---

## 四、常见问题

**页面打不开**
- 确认 `python server.py` 在运行；端口 8765 是否被占用：

```bat
netstat -ano | findstr :8765
```

**页面显示未连接 / 桥接离线**
- Mechanical 里是否执行过 `bridge_agent.py`
- 页面下拉里选的是不是那个窗口（每个 Mechanical 窗口要各自加载一次）

**点操作没反应 / 一直转圈**
- 顶部会显示 `Mechanical 忙`；点「解除卡住」可强制恢复（并重置服务端锁）
- 按 `Esc` 也能解除忙碌状态

**Mechanical 闪退**
- 不要在同一个窗口重复执行 `bridge_agent.py`（新版已幂等，旧版会叠加定时器）
- 网格节点数过大也会崩：生成前看「网格预估」，超 700 万节点先调大尺寸

**要改代码后生效**
- 代理代码（`app/bridge_agent.py`）改动后需要在 Mechanical 里重新 `Ctrl+F5`
- 页面/服务代码（`app/server.py`、`app/static/index.html`）改后重启服务 + 浏览器 `Ctrl+F5`

---

## 五、目录结构

```
meshkit/
├─ 加载桥接.bat            启动服务并打开页面
├─ README.md               本文件
├─ MeshKitAI_使用说明.md    详细操作说明
├─ MeshKitAI_规则总表.md    识别/分组/命名规则
├─ app/
│  ├─ server.py            本地服务（HTTP + 桥接调度）
│  ├─ bridge_agent.py      在 Mechanical 里运行的代理
│  ├─ static/index.html    网页界面
│  ├─ geo_recognize.py     几何识别
│  ├─ experience.py        经验库
│  ├─ sizing_advisor.py    尺寸推荐
│  └─ part_rules.json      类别与尺寸规则表
├─ reports/                生成的报告
└─ _archive/               历史脚本（不参与运行）
```

---

## 六、数据与隐私

- 所有数据都在本机，不上传任何模型/网格数据
- `app/config.json`（含大模型 API Key，如果填了）**不会**进入版本库
- 如需用 AI 分析，请在页面右上角「设置」里填自己的 API Key
 
