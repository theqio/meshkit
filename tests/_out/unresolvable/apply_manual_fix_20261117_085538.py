# -*- coding: utf-8 -*-
# ==========================================================================
#  人工确认后执行的补救脚本（由 meshkit 自动生成，生成时间 20261117_085538）
#
#  用法：在 Mechanical -> Automation -> Scripting 里
#        execfile(r"D:\ai\meshkit\tests\_out\unresolvable\apply_manual_fix_20261117_085538.py")
#
#  *** 默认所有 ENABLE 都是 False：脚本什么都不会做 ***
#  逐条看完说明，把想执行的那一条改成 True 再跑。
# ==========================================================================

import traceback


def _log(msg):
    try:
        print(msg)
    except Exception:
        pass


def _mesh():
    return ExtAPI.DataModel.Project.Model.Mesh


def _qty(mm):
    try:
        return Quantity(float(mm), 'mm')
    except Exception:
        return float(mm)


# --------------------------------------------------------------------------
# 1) 单元阶次降为一阶：能立刻改善 Jacobian/TetCollapse 极值，
#    代价是弯曲精度下降（薄板/弯曲主导的模型不要用）。
ENABLE_ELEMENT_ORDER_FIRST = False

# 2) 把所有带作用域的局部控制尺寸放大 10%（用于压单元数/缓解过渡畸变）
ENABLE_SIZE_UP_10 = False
# 以下仅作参考：这不是筛选条件，只是当前体量最大的几个体（放大尺寸优先考虑它们）
BIGGEST_BODIES = ['底板2\\固体4', '中面\\波纹板中面1', '中面\\波纹板中面4', '中间隔断2\\中间隔断', '支梁架', '小三角板1']

# 3) 导出 Mechanical 自带的网格质量报告（只读，安全）
ENABLE_EXPORT_REPORT = False
REPORT_PATH = r'D:\ai\meshkit\tests\_out\unresolvable\mechanical_mesh_report_20261117_085538.txt'


def main():
    mesh = _mesh()
    if ENABLE_ELEMENT_ORDER_FIRST:
        try:
            mesh.ElementOrder = 1
            _log('[ok] ElementOrder -> First')
        except Exception:
            _log('[FAIL] ElementOrder: ' + unicode(traceback.format_exc().splitlines()[-1]))

    if ENABLE_SIZE_UP_10:
        changed = 0
        for control in mesh.Children:
            try:
                name = unicode(control.Name)
            except Exception:
                continue
            try:
                location = control.Location
                ids = list(location.Ids) if location is not None else []
            except Exception:
                ids = []
            if not ids:
                continue
            try:
                size = control.ElementSize
                value = float(size.Value)
                unit = unicode(size.Unit)
                if unit.lower() != 'mm':
                    continue
                control.ElementSize = _qty(value * 1.10)
                changed += 1
            except Exception:
                continue
        _log('[ok] %d controls enlarged by 10%%' % changed)

    if ENABLE_EXPORT_REPORT:
        try:
            helper = mesh.SaveMeshReportHelper
            for method in ('Save', 'Export', 'Write', 'SaveTo'):
                fn = getattr(helper, method, None)
                if fn is None:
                    continue
                try:
                    fn(REPORT_PATH)
                    _log('[ok] mesh report -> ' + REPORT_PATH)
                    break
                except Exception:
                    continue
        except Exception:
            _log('[FAIL] mesh report: ' + unicode(traceback.format_exc().splitlines()[-1]))


try:
    main()
except Exception:
    _log(traceback.format_exc())
