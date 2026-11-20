# -*- coding: utf-8 -*-
"""ExtAPI 访问层。

设计原则
--------
1. **全部特征探测**：不同小版本/不同模型上属性名会差一两个，所有读写都先探测。
2. **写后读回校验**：任何 setter 都返回 old/new，调用方据此判断是否真的生效。
3. **绝不抛异常给调用方**：失败以日志 + 结果字典形式返回，保证 Mechanical 不会被脚本搞崩。
4. **单位统一 mm**：读回来的 Quantity 一律换算成 mm/deg。
"""
from __future__ import division

import os
import sys
import time
import traceback

from . import compat

try:
    import __builtin__ as builtins_mod  # Python 2
except ImportError:  # pragma: no cover
    import builtins as builtins_mod

_UNIT_TO_MM = {"m": 1000.0, "meter": 1000.0, "meters": 1000.0,
               "cm": 10.0, "centimeter": 10.0,
               "mm": 1.0, "millimeter": 1.0,
               "um": 0.001, "micron": 0.001, "micrometer": 0.001,
               "in": 25.4, "inch": 25.4, "inches": 25.4,
               "ft": 304.8, "feet": 304.8}

_UNIT_TO_DEG = {"deg": 1.0, "degree": 1.0, "degrees": 1.0, "rad": 57.29577951308232,
                "radian": 57.29577951308232, "radians": 57.29577951308232}

SMOOTHING_INT = {"low": 0, "medium": 1, "high": 2, "programcontrolled": 0}
ELEMENT_ORDER_INT = {"first": 1, "second": 2, "programcontrolled": 0}


def find_injected(name):
    """在 Mechanical 里取注入对象（ExtAPI / Quantity 等）。

    Mechanical 把 ExtAPI 注入到脚本的全局命名空间；被 import 的模块看不到它，
    因此依次查：本模块 globals -> __main__ -> builtins -> 所有已加载模块。
    最后一条是兜底：不同入口（Scripting 面板 / Console / Python Code 窗口）
    注入的位置不一样，扫一遍 sys.modules 最稳。
    """
    env = globals()
    if name in env:
        return env[name]
    main = sys.modules.get("__main__")
    if main is not None:
        ns = getattr(main, "__dict__", None) or {}
        if name in ns:
            return ns[name]
    ns = getattr(builtins_mod, "__dict__", {})
    if name in ns:
        return ns[name]
    # 兜底：宿主可能把它注入到别的模块里（例如 Console 宿主模块）
    try:
        for module_name, module in list(sys.modules.items()):
            if module is None:
                continue
            try:
                value = getattr(module, name, None)
            except Exception:
                continue
            if value is not None:
                return value
    except Exception:
        pass
    return None


def quantity_mm(value):
    """Quantity/数字 -> mm 浮点。"""
    if value is None:
        return None
    number = compat.to_float(value)
    unit = None
    for attr in ("Unit", "Units", "UnitName"):
        try:
            unit = getattr(value, attr)
            break
        except Exception:
            continue
    if unit is None:
        return number
    unit_text = compat.to_text(unit).strip().strip("[]").lower()
    factor = _UNIT_TO_MM.get(unit_text)
    if factor is None:
        return number
    return number * factor if number is not None else None


def quantity_deg(value):
    number = compat.to_float(value)
    unit = None
    for attr in ("Unit", "Units", "UnitName"):
        try:
            unit = getattr(value, attr)
            break
        except Exception:
            continue
    if unit is None:
        return number
    unit_text = compat.to_text(unit).strip().strip("[]").lower()
    factor = _UNIT_TO_DEG.get(unit_text)
    return number * factor if (factor and number is not None) else number


class Session(object):
    """一个 Mechanical 会话的薄封装。"""

    def __init__(self, log=None, extapi=None):
        self.log = log or compat.Logger()
        self.caps = {}
        # ExtAPI 优先由入口脚本显式传入：Mechanical 里它是注入在"脚本自己的全局命名空间"
        # 里的，被 import 的模块看不见，靠 find_injected() 的兜底不一定稳。
        self.extapi = extapi if extapi is not None else find_injected("ExtAPI")
        if self.extapi is None:
            raise RuntimeError(
                u"找不到 ExtAPI：本脚本必须在 ANSYS Mechanical 的 "
                u"Automation -> Scripting 控制台里运行（execfile 方式），"
                u"并且用 Ctrl+F5（Run Script）执行，不要用 F5（Start Debugger）。")
        self.dm = self.extapi.DataModel
        self.project = self.dm.Project
        self.model = self.project.Model
        self.mesh = self.model.Mesh
        self._quantity_factory = compat._UNSET
        self._enum_module = compat._UNSET
        self._categories = compat._UNSET
        self._ref_map_cache = None
        self._probe_caps()

    # ------------------------------------------------------------------ 探测
    def _probe_caps(self):
        caps = self.caps
        caps["version"] = self._version_text()
        caps["quantity_factory"] = self._probe_quantity_factory()
        caps["mesh_metric_enum"] = self._probe_mesh_metric_enum()
        caps["geometry_categories"] = self._probe_geometry_categories()
        caps["selection"] = self._probe_selection_module()
        caps["mesh_data"] = self.mesh_data() is not None
        try:
            body_count = len(self.bodies())
        except Exception:
            body_count = 0
        caps["body_count"] = body_count
        self.log.debug(u"环境探测: 版本=%s 体数=%s Quantity=%s 指标枚举=%s 选择模块=%s MeshData=%s"
                       % (caps["version"], body_count,
                          u"有" if caps["quantity_factory"] else u"无",
                          u"有" if caps["mesh_metric_enum"] else u"无",
                          u"有" if caps["selection"] else u"无",
                          u"有" if caps["mesh_data"] else u"无"))

    def _version_text(self):
        candidates = []
        for holder in (compat.safe_get(self.extapi, "Application"),
                       compat.safe_get(self.dm, "Application"),
                       self.extapi):
            if holder is None:
                continue
            for attr in ("Version", "ScriptVersion", "ProductVersion"):
                value = compat.safe_get(holder, attr)
                if value is not None:
                    candidates.append(compat.to_text(value))
        for attr in ("ProductVersion",):
            value = compat.safe_get(self.project, attr)
            if value is not None:
                candidates.append(compat.to_text(value))
        for text in candidates:
            if text and text not in ("None", ""):
                return text
        return u"未知"

    def _probe_quantity_factory(self):
        if self._quantity_factory is not compat._UNSET:
            return self._quantity_factory
        factory = find_injected("Quantity")
        if factory is None:
            for module_name, attr in (
                    ("Ansys.ACT.Automation.Mechanical", "Quantity"),
                    ("Ansys.ACT.Interfaces.Common", "Quantity"),
                    ("Ansys.Mechanical.DataModel", "Quantity")):
                try:
                    module = __import__(module_name, fromlist=[attr])
                    factory = getattr(module, attr)
                    break
                except Exception:
                    continue
        if factory is not None:
            try:
                factory(1.0, "mm")
            except Exception:
                factory = None
        self._quantity_factory = factory
        return factory

    def quantity(self, value_mm):
        """构造一个长度 Quantity（拿不到工厂就返回裸 float，写后读回校验会兜底）。"""
        factory = self._probe_quantity_factory()
        if factory is None:
            return float(value_mm)
        try:
            return factory(float(value_mm), "mm")
        except Exception:
            try:
                return factory(float(value_mm), u"mm")
            except Exception:
                return float(value_mm)

    def _probe_mesh_metric_enum(self):
        if self._enum_module is not compat._UNSET:
            return self._enum_module
        module = None
        try:
            from Ansys.Mechanical.DataModel.Enums import MeshMetricType  # noqa: F401
            module = sys.modules.get("Ansys.Mechanical.DataModel.Enums")
            if module is None or compat.safe_get(module, "MeshMetricType") is None:
                holder = type("EnumModuleProxy", (), {})
                holder.MeshMetricType = MeshMetricType
                module = holder
        except Exception:
            module = None
        self._enum_module = module
        return module

    def metric_enum(self, name):
        """MeshMetricType 的成员对象；不存在返回 None。"""
        module = self._probe_mesh_metric_enum()
        if module is None:
            return None
        try:
            return getattr(module.MeshMetricType, name)
        except Exception:
            return None

    def metric_names_available(self):
        module = self._probe_mesh_metric_enum()
        if module is None:
            return []
        try:
            return [n for n in dir(module.MeshMetricType) if not n.startswith("_")]
        except Exception:
            return []

    def _probe_geometry_categories(self):
        if self._categories is not compat._UNSET:
            return self._categories
        try:
            from Ansys.Mechanical.DataModel.Enums import DataModelObjectCategory as cats
            self._categories = cats
        except Exception:
            self._categories = None
        return self._categories

    def category(self, name):
        cats = self._probe_geometry_categories()
        if cats is None:
            return None
        return compat.safe_get(cats, name)

    def _probe_selection_module(self):
        for module_name in ("Ansys.ACT.Interfaces.Common",
                            "Ansys.ACT.Automation.Mechanical",
                            "Ansys.Mechanical.DataModel.Enums"):
            try:
                module = __import__(module_name, fromlist=["SelectionTypeEnum"])
                if compat.safe_get(module, "SelectionTypeEnum") is not None:
                    return module
            except Exception:
                continue
        return None

    # ------------------------------------------------------------- 基础读数
    def element_count(self):
        return int(compat.to_float(compat.safe_get(self.mesh, "Elements"), 0) or 0)

    def node_count(self):
        return int(compat.to_float(compat.safe_get(self.mesh, "Nodes"), 0) or 0)

    def mesh_state(self):
        for attr in ("ObjectState", "State", "MeshState"):
            value = compat.safe_get(self.mesh, attr)
            if value is not None:
                return compat.to_text(value)
        return u"未知"

    def mesh_data(self):
        try:
            return self.dm.MeshDataByName("Global")
        except Exception:
            pass
        try:
            return self.dm.MeshData
        except Exception:
            return None

    def mesh_metric_current(self):
        value = compat.safe_get(self.mesh, "MeshMetric")
        return None if value is None else compat.to_text(value)

    def set_mesh_metric(self, name):
        """设置显示用 Mesh Metric；name=None 表示还原为 None。"""
        if self.mesh_metric_current() == compat.to_text(name):
            return True
        if name is None:
            for candidate in ("None",):
                member = self.metric_enum(candidate)
                if member is not None:
                    try:
                        self.mesh.MeshMetric = member
                        return True
                    except Exception:
                        pass
            return False
        member = self.metric_enum(name)
        if member is None:
            return False
        try:
            self.mesh.MeshMetric = member
            return True
        except Exception:
            self.log.debug(u"设置 MeshMetric=%s 失败: %s" % (name, compat.to_text(traceback.format_exc().strip().splitlines()[-1:])))
            return False

    # --------------------------------------------------------------- 安全写入
    def set_attr(self, obj, attr, value, label=None, unit="raw"):
        """写属性并读回校验。返回结果 dict（超集，永不为 None）。"""
        label = label or attr
        result = {"target": label, "attr": attr, "unit": unit,
                  "ok": False, "skipped": False, "old": None, "new": None,
                  "error": None}
        current = compat.safe_get(obj, attr, compat._UNSET)
        if current is compat._UNSET:
            result["skipped"] = True
            result["error"] = u"对象上没有属性 %s" % attr
            return result
        result["old"] = self._readable(current, unit)
        payload = value
        try:
            if unit == "mm" and isinstance(value, (int, float)):
                payload = self.quantity(value)
            elif unit == "enum_member":
                payload = value  # 已由 set_enum_member 解析
            setattr(obj, attr, payload)
        except Exception:
            result["error"] = compat.to_text(traceback.format_exc()).strip().splitlines()[-1]
            return result
        readback = compat.safe_get(obj, attr, compat._UNSET)
        result["new"] = self._readable(readback, unit)
        result["ok"] = True
        result["verified"] = self._verify(value, result["new"], unit)
        return result

    @staticmethod
    def _readable(value, unit):
        if value is compat._UNSET:
            return None
        if unit == "mm":
            return quantity_mm(value)
        if unit == "deg":
            return quantity_deg(value)
        return compat.to_text(value)

    @staticmethod
    def _verify(target, readback, unit):
        if readback is None:
            return False
        if unit in ("mm", "deg"):
            try:
                return abs(float(readback) - float(target)) <= max(1e-6, abs(float(target)) * 1e-6)
            except Exception:
                return False
        if isinstance(target, bool):
            return compat.to_text(readback).lower() in (compat.to_text(target).lower(), "1", "true")
        try:
            return abs(float(target) - float(readback)) < 1e-9
        except Exception:
            return compat.to_text(target).lower() == compat.to_text(readback).lower()

    def set_length_mm(self, obj, attr, mm, label=None):
        """长度属性专用：优先 Quantity 写入，失败则裸数值再校验。"""
        result = self.set_attr(obj, attr, float(mm), label=label, unit="mm")
        if result["ok"] and not result.get("verified"):
            # 裸数值写入没生效（单位或类型不对），退回 float 直写再试一次
            try:
                setattr(obj, attr, float(mm))
                readback = quantity_mm(compat.safe_get(obj, attr))
                if readback is not None and abs(readback - float(mm)) <= abs(mm) * 1e-6:
                    result.update({"new": readback, "verified": True, "note": u"以裸数值写入"})
            except Exception:
                pass
        return result

    def set_bool(self, obj, attr, flag, label=None):
        return self.set_attr(obj, attr, bool(flag), label=label, unit="bool")

    def set_number(self, obj, attr, number, label=None):
        return self.set_attr(obj, attr, number, label=label, unit="number")

    def set_enum_member(self, obj, attr, member_name, int_table=None, label=None):
        """枚举属性赋值：先按当前值的 .NET 类型解析，再退化到字符串/整数。"""
        result = {"target": label or attr, "attr": attr, "unit": "enum",
                  "ok": False, "skipped": False, "old": None, "new": None,
                  "error": None}
        current = compat.safe_get(obj, attr, compat._UNSET)
        if current is compat._UNSET:
            result["skipped"] = True
            result["error"] = u"对象上没有属性 %s" % attr
            return result
        result["old"] = compat.to_text(current)

        # 1) 已有 .NET 枚举类型 -> Enum.Parse
        payload = None
        try:
            enum_type = current.GetType()
            if enum_type is not None and enum_type.IsEnum:
                import System
                payload = System.Enum.Parse(enum_type, member_name, True)
        except Exception:
            payload = None

        # 2) 字符串直写
        if payload is None:
            try:
                setattr(obj, attr, member_name)
                if self._enum_readback_ok(obj, attr, member_name, int_table):
                    result.update({"ok": True, "verified": True,
                                   "new": compat.to_text(compat.safe_get(obj, attr))})
                    return result
            except Exception:
                pass
            payload = None

        # 3) 整数表
        if payload is None and int_table:
            number = int_table.get(compat.to_text(member_name).strip().lower())
            if number is not None:
                payload = number
        if payload is None:
            result["error"] = u"无法把 %s 解析为 %s 的枚举成员" % (member_name, attr)
            return result

        try:
            setattr(obj, attr, payload)
        except Exception:
            result["error"] = compat.to_text(traceback.format_exc()).strip().splitlines()[-1]
            return result
        readback = compat.safe_get(obj, attr)
        result["new"] = compat.to_text(readback)
        result["ok"] = self._enum_readback_ok(obj, attr, member_name, int_table)
        result["verified"] = result["ok"]
        if not result["ok"]:
            result["error"] = u"读回值 %s 与目标 %s 不一致" % (result["new"], member_name)
        return result

    def _enum_readback_ok(self, obj, attr, member_name, int_table=None):
        readback = compat.safe_get(obj, attr)
        text = compat.to_text(readback).strip()
        wanted = compat.to_text(member_name).strip()
        if text.lower() == wanted.lower():
            return True
        # 有些版本的枚举属性读回的是整数（0/1/2），按整数表反查
        if int_table:
            number = int_table.get(wanted.lower())
            if number is not None:
                try:
                    if int(compat.to_float(text, -999)) == int(number):
                        return True
                except Exception:
                    pass
        # 尝试用 Mechanical 的枚举名查询接口二次确认
        getter = compat.safe_get(obj, "GetEnumName")
        if callable(getter):
            try:
                name = getter(attr)
                if compat.to_text(name).strip().lower() == wanted.lower():
                    return True
            except Exception:
                pass
        return False

    # --------------------------------------------------------------- 几何/体
    def bodies(self):
        """返回全部 Body 对象（含被抑制的）。"""
        body_category = self.category("Body")
        geometry = compat.safe_get(self.model, "Geometry")
        if geometry is None:
            return []
        if body_category is None:
            return [c for c in (compat.safe_get(geometry, "Children") or [])
                    if u"body" in compat.to_text(compat.safe_get(c, "DataModelObjectCategory")).lower()]
        try:
            return list(geometry.GetChildren(body_category, True))
        except Exception:
            self.log.debug(u"GetChildren(Body, True) 失败，退化到 Children 过滤")
            out = []
            for child in (compat.safe_get(geometry, "Children") or []):
                text = compat.to_text(compat.safe_get(child, "DataModelObjectCategory")).lower()
                if text.endswith("body") or text == "body":
                    out.append(child)
            return out

    @staticmethod
    def body_name(body):
        return compat.to_text(compat.safe_get(body, "Name", u"<unnamed>"))

    @staticmethod
    def leaf_name(name):
        text = compat.to_text(name)
        if u"\\" in text:
            return text.split(u"\\")[-1]
        return text

    def body_id_candidates(self, body):
        """体身上所有能当 id 用的值（不同版本/不同接口给的不一样）。"""
        values = []
        for accessor in ("GetGeoReferenceId", "Id"):
            value = compat.safe_get(body, accessor)
            if callable(value):
                try:
                    value = value()
                except Exception:
                    value = None
            if isinstance(value, compat.integer_types):
                values.append(int(value))
        for holder_attr in ("GeoEntity", "InternalObject"):
            holder = compat.safe_get(body, holder_attr)
            value = compat.safe_get(holder, "Id") if holder is not None else None
            if isinstance(value, compat.integer_types):
                values.append(int(value))
        out = []
        for item in values:
            if item not in out:
                out.append(item)
        return out

    def body_geo_id(self, body):
        """体的几何参考 id（优先取第一个可用的）。"""
        values = self.body_id_candidates(body)
        return values[0] if values else None

    def body_element_count_by_name(self, name):
        """按体名查 MeshData 的单元数；拿不到返回 None。

        实测（2022 R1 + 290 体模型）：``MeshDataByName`` 对体名/短名都取不到，
        因此这条只是兜底，失败不报错。
        """
        for candidate in (compat.to_text(name), self.leaf_name(name)):
            if not candidate:
                continue
            try:
                data = self.dm.MeshDataByName(candidate)
            except Exception:
                continue
            value = compat.safe_get(data, "ElementCount")
            if value is None:
                continue
            return int(compat.to_float(value, 0) or 0)
        return None

    def body_thickness_mm(self, body):
        value = compat.safe_get(body, "Thickness")
        if value is None:
            return 0.0
        return quantity_mm(value) or 0.0

    def body_table(self):
        """[{object,name,leaf,ids,geo_id,suppressed,thickness_mm,elements}]

        关于 ``elements``：实测 ``ElementIdsPerRefId`` 是**按几何面/边**统计的
        （计数合计可达单元总数的 2~3 倍），不能当"逐体单元数"用。
        这里用"合计是否≈单元总数"做健全性判断：只有对得上才采用，
        否则把 ``elements`` 置 None——宁可不下结论，也不误判。
        """
        counts = self.per_body_element_counts()
        total = self.element_count()
        per_body_ok = False
        if counts and total > 0:
            ref_total = sum(counts.values())
            per_body_ok = abs(ref_total - total) <= max(1.0, 0.05 * total)
        if counts and not per_body_ok:
            self.log.debug(u"ElementIdsPerRefId 合计 %s ≠ 单元总数 %s，判定为『按面统计』，"
                           u"不用于逐体单元数" % (compat.fmt_num(sum(counts.values())),
                                              compat.fmt_num(total)))

        rows = []
        for body in self.bodies():
            name = self.body_name(body)
            candidates = self.body_id_candidates(body)
            count = None
            if per_body_ok:
                for candidate in candidates:
                    if candidate in counts:
                        count = counts[candidate]
                        break
            row = {
                "object": body,
                "name": name,
                "leaf": self.leaf_name(name),
                "ids": candidates,
                "geo_id": candidates[0] if candidates else None,
                "suppressed": bool(compat.safe_get(body, "Suppressed", False)),
                "thickness_mm": self.body_thickness_mm(body),
                "elements": count,
                "elements_available": per_body_ok,
            }
            rows.append(row)
        return rows

    def body_element_count_by_name(self, name):
        """兜底：按体名查 MeshData 的单元数；失败返回 None。

        体全名形如 ``底板2\\固体4``，不同接口对"全名"和"最后一段"的接受度不一样，
        两个都试一遍。
        """
        for candidate in (compat.to_text(name), self.leaf_name(name)):
            if not candidate:
                continue
            try:
                data = self.dm.MeshDataByName(candidate)
            except Exception:
                continue
            value = compat.safe_get(data, "ElementCount")
            if value is None:
                continue
            return int(compat.to_float(value, 0) or 0)
        return None

    def per_body_element_counts(self):
        """{geo_ref_id: 单元数}。来自 MeshData.ElementIdsPerRefId。

        2022 R1 里这个属性有时要先"建几何网格缓存"才有内容，
        因此失败时会依次试 BuildGeoMeshCache() / CreateGeoData() 再读一次。
        """
        if self._ref_map_cache is not None:
            return self._ref_map_cache
        counts = {}
        data = self.mesh_data()
        if data is not None:
            counts = self._read_ref_counts(data)
            if not counts:
                for init_name in ("BuildGeoMeshCache", "CreateGeoData"):
                    initializer = compat.safe_get(data, init_name)
                    if not callable(initializer):
                        continue
                    try:
                        initializer()
                    except Exception:
                        continue
                    counts = self._read_ref_counts(data)
                    if counts:
                        self.log.debug(u"调用 %s() 后取到了每体单元数" % init_name)
                        break
        if not counts:
            self.log.debug(u"ElementIdsPerRefId 取不到（已尝试 BuildGeoMeshCache/CreateGeoData）")
        self._ref_map_cache = counts
        return counts

    def _read_ref_counts(self, data):
        counts = {}
        per_ref = compat.safe_get(data, "ElementIdsPerRefId")
        if per_ref is None:
            return counts
        try:
            for key in per_ref.Keys:
                value = per_ref[key]
                try:
                    counts[int(key)] = len(value)
                except Exception:
                    counts[int(key)] = int(compat.to_float(value, 0) or 0)
        except Exception:
            return {}
        return counts

    def invalidate_caches(self):
        self._ref_map_cache = None

    def geometry_bounds(self):
        """尽量取包围盒对角长（mm）作为尺度参考；取不到返回 None。"""
        geometry = compat.safe_get(self.model, "Geometry")
        for holder in (geometry, self.model):
            if holder is None:
                continue
            for accessor in ("GetBoundingBox", "BoundingBox"):
                value = compat.safe_get(holder, accessor)
                if value is None:
                    continue
                try:
                    box = value() if callable(value) else value
                except Exception:
                    continue
                for attrs in (("Min", "Max"), ("min", "max"), ("Lower", "Upper")):
                    low = compat.safe_get(box, attrs[0])
                    high = compat.safe_get(box, attrs[1])
                    if low is None or high is None:
                        continue
                    try:
                        dx = float(high.X) - float(low.X)
                        dy = float(high.Y) - float(low.Y)
                        dz = float(high.Z) - float(low.Z)
                        return (dx * dx + dy * dy + dz * dz) ** 0.5
                    except Exception:
                        continue
        return None

    def min_geometry_edge_mm(self):
        """几何最小边长：优先网格对象上的读数，其次扫描 Edge 实体。"""
        value = quantity_mm(compat.safe_get(self.mesh, "MinimumEdgeLength"))
        if value and value > 0:
            return value, "Mesh.MinimumEdgeLength"
        scanned = self.scan_small_edges(threshold_mm=1e9, limit=1, collect_names=False)
        if scanned.get("min_length_mm") is not None:
            return scanned["min_length_mm"], u"几何 Edge 扫描"
        return None, u"不可用"

    def scan_small_edges(self, threshold_mm, limit=20, collect_names=True, max_scan=20000):
        """扫描几何 Edge，找长度 < threshold 的小边；用于定位碎边来源。

        返回 {"ok":bool, "scanned":n, "min_length_mm":x, "hits":[{"name","length_mm","owner"}],
              "error":str|None}
        """
        out = {"ok": False, "scanned": 0, "min_length_mm": None, "hits": [],
               "error": None, "truncated": False}
        edge_category = self.category("Edge")
        geometry = compat.safe_get(self.model, "Geometry")
        if edge_category is None or geometry is None:
            out["error"] = u"当前版本拿不到 DataModelObjectCategory.Edge"
            return out
        try:
            edges = geometry.GetChildren(edge_category, True)
        except Exception:
            out["error"] = compat.to_text(traceback.format_exc()).strip().splitlines()[-1]
            return out
        if edges is None:
            out["error"] = u"几何 Edge 列表为空"
            return out
        out["ok"] = True
        try:
            total = len(edges)
        except Exception:
            total = 0
        for index, edge in enumerate(edges):
            if index >= max_scan:
                out["truncated"] = True
                break
            length = quantity_mm(compat.safe_get(edge, "Length"))
            out["scanned"] += 1
            if length is None:
                continue
            if out["min_length_mm"] is None or length < out["min_length_mm"]:
                out["min_length_mm"] = length
            if threshold_mm is None or length < threshold_mm:
                if len(out["hits"]) < limit:
                    owner = self._owner_name(edge)
                    out["hits"].append({"name": compat.to_text(compat.safe_get(edge, "Name", u"")),
                                        "length_mm": length, "owner": owner})
        if collect_names:
            out["total_edges"] = total
        return out

    def _owner_name(self, entity):
        for attr in ("Parent", "ParentBody", "Owner"):
            holder = compat.safe_get(entity, attr)
            if holder is None:
                continue
            name = compat.safe_get(holder, "Name")
            if name:
                return compat.to_text(name)
            inner = compat.safe_get(holder, "Parent")
            if inner is not None:
                name = compat.safe_get(inner, "Name")
                if name:
                    return compat.to_text(name)
        return u""

    # --------------------------------------------------------------- 网格动作
    def generate_mesh(self, clear_first=False):
        """同步划网格；返回 {ok,error,elements,nodes,state,seconds,cleared}"""
        result = {"ok": False, "error": None, "elements": self.element_count(),
                  "nodes": self.node_count(), "state": self.mesh_state(),
                  "seconds": 0.0, "cleared": False}
        if clear_first:
            try:
                self.mesh.ClearGeneratedData()
                result["cleared"] = True
                self.invalidate_caches()
                self.log.info(u"已清除旧网格（ClearGeneratedData）")
            except Exception:
                self.log.warn(u"ClearGeneratedData 失败: %s"
                              % compat.to_text(traceback.format_exc().strip().splitlines()[-1]))
        started = time.time()
        try:
            self.mesh.GenerateMesh()
            result["ok"] = True
        except Exception:
            result["error"] = compat.to_text(traceback.format_exc())
        result["seconds"] = time.time() - started
        self.invalidate_caches()
        result["elements"] = self.element_count()
        result["nodes"] = self.node_count()
        result["state"] = self.mesh_state()
        if result["error"] is None and result["elements"] <= 0:
            result["ok"] = False
            result["error"] = u"GenerateMesh 未报错但单元数为 0（网格未真正生成）"
        return result

    def save(self):
        try:
            self.dm.Project.Save()
            return True
        except Exception:
            self.log.warn(u"保存工程失败: %s"
                          % compat.to_text(traceback.format_exc().strip().splitlines()[-1]))
            return False

    # ---------------------------------------------------------------- 选择集
    def make_selection(self, geo_ids):
        """按几何 id 构造 SelectionInfo；失败返回 None。"""
        module = self._probe_selection_module()
        if module is None:
            return None
        manager = compat.safe_get(self.extapi, "SelectionManager")
        if manager is None:
            return None
        try:
            type_enum = module.SelectionTypeEnum.GeometryEntities
            info = manager.CreateSelectionInfo(type_enum)
            info.Ids = list(geo_ids)
            return info
        except Exception:
            self.log.debug(u"CreateSelectionInfo 失败: %s"
                           % compat.to_text(traceback.format_exc().strip().splitlines()[-1]))
            return None

    def selection_ids(self, location):
        try:
            return [int(x) for x in location.Ids]
        except Exception:
            return []

    def project_path(self):
        for holder in (self.dm.Project, self.dm):
            for attr in ("ProjectDirectory", "FilePath", "ProjectPath", "Name"):
                value = compat.safe_get(holder, attr)
                if value:
                    return compat.to_text(value)
        return u""

    def describe(self):
        """环境/模型摘要，写进报告头。"""
        return {
            "version": self.caps.get("version", u"未知"),
            "project": self.project_path(),
            "model": compat.to_text(compat.safe_get(compat.safe_get(self.model, "Name"), "Name")
                                    or compat.safe_get(self.model, "Name", u"")),
            "bodies": self.caps.get("body_count", 0),
            "elements": self.element_count(),
            "nodes": self.node_count(),
            "state": self.mesh_state(),
            "caps": dict(self.caps),
        }
