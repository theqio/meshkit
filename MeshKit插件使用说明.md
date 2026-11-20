# MeshKit 插件（ANSYS Mechanical 工具栏按钮）

## 一、插件在哪、怎么用

打开 Mechanical → 顶部 **`Automation`** 标签 → 右侧 **`User Buttons`** 组，有三个按钮：

| 按钮 | 作用 | 耗时 |
|---|---|---|
| **MeshKit_Run** | 一键：修黄色项 → 套用尺寸 → 划网格 → 检查失败体 | 3~5 分钟 |
| **MeshKit_Check** | 检查网格失败的体（用 `body.ObjectState`） | 约 10 秒 |
| **MeshKit_Report** | 质量报告（12 项指标 + 边比分布 + 最差体清单） | 约 1 分钟 |

点一下即可，不用再打开脚本面板粘贴代码。

## 二、插件存在哪

```
%APPDATA%\Ansys\v221\Applets\DSApplet\UserRibbons\
    UserRibbons.xml                 ← 按钮定义（名字/图标/脚本绑定）
    scripts\MeshKit_Run.py          ← "一键网格"按钮的脚本
    scripts\MeshKit_Check.py        ← "检查失败体"
    scripts\MeshKit_Report.py       ← "质量报告"
    images\MeshKit_*_ur.png         ← 按钮图标
```

- 属于**用户级配置**：这台机器上用这个账号打开**任何工程**都能看到这三个按钮
- 重启 Mechanical 后依然存在（不需要重新创建）

## 三、按钮背后的真正逻辑

按钮脚本只做一件事：调用 `D:\ai\meshkit` 下已验证的脚本。

```python
import os
ROOT = r'D:\ai\meshkit'
execfile(os.path.join(ROOT, 'one_shot_finish.py'))      # MeshKit_Run
execfile(os.path.join(ROOT, 'check_by_objectstate.py')) # MeshKit_Check
execfile(os.path.join(ROOT, 'run_mesh.py'))             # MeshKit_Report
```

**好处**：想改逻辑只改 `D:\ai\meshkit` 里的脚本，按钮不用重建。

## 四、改尺寸就直接改脚本

比如你想把垫板从 40 mm 改回 30 mm：

1. 打开 `D:\ai\meshkit\coarsen_v3.py`
2. 修改顶部 `NEW_SIZES` 里的数值
3. 点击 **MeshKit_Run**（它跑的是 `one_shot_finish.py`，尺寸表在其中 `NEW_SIZES`/`FINAL` 里）

> 注意：`one_shot_finish.py` 和 `coarsen_v3.py` 各自有一份尺寸表，改的时候两处都改，避免不一致。

## 五、换台电脑怎么迁移

把 `%APPDATA%\Ansys\v221\Applets\DSApplet\UserRibbons\` 整个文件夹复制到新机器的同一路径，
再把 `D:\ai\meshkit` 目录一起复制过去（路径保持 `D:\ai\meshkit` 最省事），重启 Mechanical 即可。

如果新机器路径不同，改按钮脚本里的 `ROOT` 常量即可。

## 六、附：之前做的 ACT 扩展（未启用）

`%APPDATA%\Ansys\v221\ACT\extensions\_MeshKit_unused\` 是我最早按 ACT 规范写的扩展
（带 XML 清单 + 3 个图标），但因为 Mechanical 的 **User Buttons** 机制更简单可靠，
已重命名停用。想试 ACT 方式的话，把文件夹名改回 `MeshKit` 并重启 Mechanical，
在 `Extensions → Manage Extensions` 里勾选即可。

## 七、已验证

- `MeshKit_Check` 实际点击测试通过（2026-11-17 15:20:49），输出：
  ```
  Mesh.ObjectState = Solved    Mesh.State = UpToDate
  Meshed 286 个 / 抑制 4 个 / 失败 0 个
  ```
