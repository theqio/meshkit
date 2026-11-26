# SpaceClaim 模型体检模块 —— 交接给 Antigravity

> 本文是给外部 AI 编程助手（Antigravity）的**当前状态 + 硬知识 + 待办**。
> 通用规范见 `AGENTS.md`，通用修改流程见 `交给Antigravity_修改指南.md`；本文只讲 SpaceClaim 这一块。

---

## 一、代码在哪（只改这些文件）

| 文件 | 作用 | 语言/注意 |
|---|---|---|
| `app/scdm_batch.py` | **全自动体检脚本**（headless 跑在 SpaceClaim 内） | IronPython 2.7，UTF-8 |
| `app/scdm_agent.py` | 可选：在已打开的 SpaceClaim 里做常驻代理（文件队列轮询） | IronPython 2.7 + BOM |
| `app/server.py` | 路由：`/api/scdm/*`，含 `scdm_batch()`、`scdm_models()`、`scdm_recent_files()`、`scdm_auto_info()` | Python 3 |
| `app/static/scdm.html` | 独立页面（连接 + 选模型 + 全自动体检 + 结果表） | 单文件 HTML/JS |

不要动：`app/ipc/`、`app/config.json`、`model_config__*.json`、`reports/`。

---

## 二、三种运行模式（现状）

```
A. 全自动（主用）
   页面按钮 → POST /api/scdm/batch
   → 服务写 %TEMP%\meshkit_scdm_job.json
   → 启动 SpaceClaim.exe "<模型>" /RunScript=app/scdm_batch.py /Headless=True /ExitAfterScript=True /Splash=False
   → 脚本把结果写到 %TEMP%\meshkit_scdm_result.json
   → 服务轮询读取并返回

B. 自动连接（页面加载即执行）
   POST /api/scdm/connect → 检测 exe / 版本 / 已打开窗口标题 / 最近打开列表（journal MRU）

C. 实时代理（手动、少用）
   在 SpaceClaim 的 Scripting 面板里 execfile app/scdm_agent.py（文件队列 scdm_cmd.json）
```

**注意**：`%TEMP%` 是必须的。项目目录 `D:\ai\meshkit` 在亿赛通（DLP）保护下，非白名单进程（SpaceClaim）**读到的是密文、写入也会失败**。job/result/trace 一律放 `%TEMP%`。

---

## 三、已摸清的 SpaceClaim API 硬知识（别重复踩坑）

### 1. headless 脚本怎么加载 API

`/RunScript` 环境下**没有预加载 API**，必须自己加载：

```python
from Microsoft.Win32 import Registry
key = Registry.ClassesRoot.OpenSubKey("SpaceClaim.AnsysSCDM.221")
ver = key.GetValue("Version")          # 例如 19466
root = os.environ["AWP_ROOT221"]       # D:\Program Files\ANSYS Inc\v221
sys.path.append(os.path.join(root, "scdm"))
full = "spaceclaim.api.v22, Version=22.1.%s.0, Culture=neutral, PublicKeyToken=7210645d4d5e3a39" % ver
clr.LoadAssemblyByName(full); clr.AddReference(full)
clr.LoadAssemblyFromFile("SpaceClaim.Api.V22.Scripting"); clr.AddReference("SpaceClaim.Api.V22.Scripting")
```

⚠️ **不要** `LoadAssemblyFromFile("SpaceClaim.Geometry.dll")`：那是另一份程序集，类型身份不匹配（报错 `expected Matrix, got Matrix`）。

### 2. 正确的命名空间

```python
from SpaceClaim.Api.V22 import Window
from SpaceClaim.Api.V22.Geometry import Matrix, Direction, Collision
```

`from SpaceClaim.Api.V22 import Matrix` 会失败（Matrix 在子命名空间 `Geometry`）。

### 3. 文档与模型遍历

```python
doc = Window.ActiveWindow.Document      # headless 也有 ActiveWindow
part = doc.MainPart
# 组件树：part.Components[i].Content 是 PartGeneral（不是 Component）
#   PartGeneral.Bodies -> DesignBody 列表
#   PartGeneral.Components -> 子组件
# 组件名常常为空字符串，体名在 DesignBody.Name
doc.Name 不存在；用 doc.FilePath / doc.Path 拿路径
```

### 4. 几何信息

```python
body.Shape.Volume                                   # 单位：m³（×1e9 得 mm³）
box  = body.Shape.GetBoundingBox(Matrix.Identity)   # -> Geometry.Box
pts  = list(box.Corners)                            # 8 个 Point，单位 m（×1000 得 mm）
body.Shape.GetClosestSeparation(other_shape)        # -> Separation: Distance/PointA/PointB
body.Shape.GetCollision(other_shape)                # -> Collision 枚举
bool(col & Collision.Intersect)                     # 是否干涉
```

`GetExtremePoint(Direction.DirX, DirY, DirZ)` 也能取角点，但 `GetBoundingBox` 更简单。

### 5. 单位

SpaceClaim 内部全部是**米**。输出给用户前必须换算成 mm。

### 6. 启动参数不要手工加引号

`subprocess.Popen([exe, model, "/RunScript=" + script, "/Headless=True", "/ExitAfterScript=True", "/Splash=False"])`
如果写成 `'"%s"' % model`，SCDM 收到带引号的路径会立刻退出（表现为"卡住无结果"）。

### 7. 最近打开的文件从哪来

`%APPDATA%\SpaceClaim\Journal Files\*.scjournal` 里每个会话有：

```xml
<MruFileList><string>D:\work\...\SYS.scdoc</string>...</MruFileList>
```

按 journal 修改时间倒序解析即可（`server.py: scdm_recent_files()`）。
注册表里没有 MRU；窗口标题只有文档名，没有路径。

---

## 四、当前实现做了什么

`app/scdm_batch.py`（op = `all|parts|symmetry|gaps|interference`）：

| 检查 | 算法 | 输出 |
|---|---|---|
| parts | 遍历组件树，取体名/体积/包围盒 | 每体的 dims、center、volume_mm3 |
| symmetry | 按尺寸签名分组（±0.5mm）→ 质心关于中位面镜像 → 偏差 > tol 记录 | 建议平移量 `suggest_shift` |
| gaps | 包围盒粗筛（≤max_gap）→ `GetClosestSeparation` | 距离 + 两侧最近点 |
| interference | 包围盒重叠 → `GetCollision` | 干涉体对 |

实测（`模拟板.scdoc`，12 体）：**57 秒**，输出 3 组同尺寸 / 9 对镜像比对 / 15 对间隙 / 14 对干涉。

---

## 五、待办（按优先级）

| # | 事项 | 位置 | 说明 |
|---|---|---|---|
| 1 | 间隙精确到**面** | `scdm_batch.py:check_pairs` | 现在只给体的最近点；需要找到具体哪两个面（`DesignFace` / `Face.Shape`），页面才能指明"该延长哪个面" |
| 2 | 干涉**分级** | 同上 | 只有"相交/不相交"；需要干涉体积（对临时副本做布尔求交）来分：真干涉 / 过盈配合 / 建模误差 |
| 3 | 对称判定增强 | `check_symmetry` | 现按 X/Y/Z 中位面；实际装配可能关于任意平面镜像，且要结合命名规则（如 `_01/_02`）与白名单 |
| 4 | 结果落盘到报告 | `server.py` | 现在只在页面显示；建议写 `reports/体检_<模型>_<时间>.md` |
| 5 | 大模型性能 | `check_pairs` | 80MB 模型体会更多，O(n²) 两两调用可能很慢，需要先按包围盒空间分桶 |
| 6 | 半自动修复（下一阶段） | 新增 `scdm_fix.py` | 平移 / 延长面 / 缩短面 / 布尔切除；**每步前另存 .scdoc**，且必须人工确认 |

---

## 六、验证方式（改完必须做）

```bat
:: 1) 服务在跑
curl http://127.0.0.1:8765/api/ping

:: 2) 自动连接是否正常（应返回 exe / version / 窗口 / 最近列表）
curl -X POST http://127.0.0.1:8765/api/scdm/connect -H "Content-Type: application/json" -d "{}"

:: 3) 全自动体检（用真实模型，约 1 分钟）
curl -X POST http://127.0.0.1:8765/api/scdm/batch -H "Content-Type: application/json" ^
     -d "{\"op\":\"all\",\"model\":\"<某个 .scdoc 完整路径>\"}"

:: 4) 页面
http://127.0.0.1:8765/scdm
```

改完把 `%TEMP%\meshkit_scdm_trace.log` 的内容一并附上（脚本每一步都有 trace，排查最快）。

---

## 七、给 Antigravity 的提示词

```
项目在 D:\ai\meshkit，这次只改 SpaceClaim 模型体检模块。先读：
- D:\ai\meshkit\SpaceClaim交接_给Antigravity.md（本文，含已摸清的 API 用法与坑）
- D:\ai\meshkit\交给Antigravity_修改指南.md（工作流：明文副本 + 自动同步）
- D:\ai\meshkit\AGENTS.md（通用硬约束）

工作目录（亿赛通加密，只能在这里改）：
C:\Users\qiyuqiang\AppData\Local\Temp\meshkit_work
（项目目录会自动同步过来；你改完也会自动同步回去，不要手动拷贝）

这次任务（按顺序）：
1) 把「间隙」精确到面：对每对候选体，找出距离最近的两个 DesignFace，输出面标识（体名 + 面序号/中心点）
2) 把「干涉」分级：对判定相交的体对，在临时文档里做布尔求交，给出干涉体积(mm³)与中心点，
   并按体积/位置给初步分类：真干涉 / 疑似过盈配合 / 疑似建模误差
3) 结果同时写一份 Markdown 报告到 D:\ai\meshkit\reports\

硬性要求：
- app/scdm_batch.py 必须是 IronPython 2.7 语法（无 f-string/类型注解）
- 只能读，不要修改被检查的模型（布尔运算必须在临时文档/副本上做）
- job/result/trace 一律写 %TEMP%，不要写进项目目录
- 不要改 app/ipc、config.json、model_config__*、model_flags__*
- 改完必须用真实模型跑一次 /api/scdm/batch 并把结果贴出来
   （可先用 C:\Users\qiyuqiang\Desktop\ansys fluent\模拟板.scdoc 验证）

输出：改动文件清单 + 每处改了什么 + 实测结果（体数/间隙数/干涉分级结果/耗时）
```
