# -*- coding: utf-8 -*-
"""假的 ExtAPI：在纯 IronPython 里模拟 Mechanical，用来验证 meshkit 的闭环逻辑。

它不追求"像 Mechanical"，只追求"接口形状一致 + 行为可预测"：
  * 属性读写、枚举字符串、Quantity、SelectionManager、MeshData 都按真实签名摆好；
  * GenerateMesh() 会按当前设置算出一个"网格规模 + 质量表"，让诊断/纠正能真的跑起来；
  * 可以人为制造故障：0 尺寸控制、退化几何、四边形失败、疑似卡住的体、持续报错。

用法::

    import fake_extapi
    env = fake_extapi.install(scenario="container_like")
    ...
    fake_extapi.uninstall()
"""
from __future__ import division

import math
import sys


# --------------------------------------------------------------------------
# 基础仿造物
# --------------------------------------------------------------------------
class FakeQuantity(object):
    def __init__(self, value, unit=u"mm"):
        self.Value = float(value)
        self.Unit = unit

    def __float__(self):
        return self.Value

    def __repr__(self):
        return "FakeQuantity(%g, %s)" % (self.Value, self.Unit)


def Quantity(value, unit=u"mm"):
    return FakeQuantity(value, unit)


class FakeEnum(object):
    def __init__(self, name):
        self.name = name
        self.Name = name

    def __repr__(self):
        return self.name

    def ToString(self):
        return self.name


class FakeSelectionInfo(object):
    def __init__(self, ids=None):
        self.Ids = list(ids or [])

    def __repr__(self):
        return "SelectionInfo(%s)" % (self.Ids,)


class FakeSelectionManager(object):
    def __init__(self):
        self._current = FakeSelectionInfo()

    def CreateSelectionInfo(self, _type):
        return FakeSelectionInfo()

    def ClearSelection(self):
        self._current = FakeSelectionInfo()

    def AddSelection(self, obj):
        geo_id = getattr(obj, "Id", None)
        if geo_id is not None:
            self._current.Ids.append(int(geo_id))

    @property
    def CurrentSelection(self):
        return self._current


class FakeThickness(FakeQuantity):
    pass


class FakeBody(object):
    def __init__(self, geo_id, name, thickness_mm=0.0, suppressed=False):
        self.Id = int(geo_id)
        self.Name = name
        self.Suppressed = bool(suppressed)
        self.Thickness = FakeQuantity(thickness_mm, u"mm") if thickness_mm else FakeQuantity(0.0, u"mm")
        self.StiffnessBehavior = "Flexible"
        self.DataModelObjectCategory = FakeEnum("Body")

    def GetGeoReferenceId(self):
        return self.Id

    def __repr__(self):
        return "Body(%s)" % self.Name


class FakeEdge(object):
    def __init__(self, name, length_mm, owner=None):
        self.Name = name
        self.Length = FakeQuantity(length_mm, u"mm")
        self.Parent = owner
        self.DataModelObjectCategory = FakeEnum("Edge")


class FakeBox(object):
    def __init__(self, low, high):
        self.Min = type("P", (), {"X": low[0], "Y": low[1], "Z": low[2]})()
        self.Max = type("P", (), {"X": high[0], "Y": high[1], "Z": high[2]})()


class FakeGeometry(object):
    def __init__(self, bodies, edges, box):
        self._bodies = bodies
        self._edges = edges
        self._box = box

    def GetChildren(self, category, recursive=True):
        name = getattr(category, "name", str(category))
        if name == "Body":
            return list(self._bodies)
        if name == "Edge":
            return list(self._edges)
        if name == "Face":
            return []
        raise RuntimeError("unsupported category %s" % name)

    def GetBoundingBox(self):
        return self._box

    @property
    def Children(self):
        return list(self._bodies)


class FakeMeshControl(object):
    def __init__(self, name, category, kind):
        self.Name = name
        self.DataModelObjectCategory = FakeEnum(category)
        self._kind = kind
        self.ElementSize = FakeQuantity(0.0, u"mm")
        self.Method = FakeEnum("Automatic")
        self.Algorithm = FakeEnum("PatchConforming")
        self.Suppressed = False
        self.Location = FakeSelectionInfo()
        self.Behavior = "ProgramControlled"
        self.CurvatureNormalAngle = FakeQuantity(0.0, u"deg")

    def __repr__(self):
        return "Control(%s)" % self.Name


class FakeElement(object):
    def __init__(self, element_id, ref_id, node_ids):
        self.Id = element_id
        self.RefId = ref_id
        self.NodeIds = node_ids
        self.Type = "kTet10"


class FakeMeshData(object):
    def __init__(self, mesh):
        self._mesh = mesh

    @property
    def ElementCount(self):
        return self._mesh.Elements

    @property
    def NodeCount(self):
        return self._mesh.Nodes

    @property
    def ElementIdsPerRefId(self):
        return _FakeDict(dict((ref_id, list(range(count)))
                              for ref_id, count in self._mesh.body_elements.items()))

    def ElementById(self, element_id):
        return FakeElement(element_id, 0, [])

    def NodeXYZById(self, node_id):
        return type("P", (), {"X": float(node_id), "Y": 0.0, "Z": 0.0})()


class _FakeDict(object):
    def __init__(self, data):
        self._data = data

    @property
    def Keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]

    def __len__(self):
        return len(self._data)

    def items(self):
        return self._data.items()


# --------------------------------------------------------------------------
# 网格
# --------------------------------------------------------------------------
METRIC_ORDER = [
    "ElementQuality", "Skewness", "JacobianRatio", "JacobianRatioCornerNodes",
    "JacobianRatioGaussPoints", "AspectRatio", "TetCollapse", "MaximumCornerAngle",
    "MinTriAngle", "MaxTriAngle", "WarpingFactor", "ParallelDeviation",
]

DEFAULT_LIMITS = {
    "ElementQuality": (0.10, 0.05),
    "Skewness": (0.90, 0.98),
    "JacobianRatio": (10.0, 50.0),
    "JacobianRatioCornerNodes": (0.30, 0.10),
    "JacobianRatioGaussPoints": (0.30, 0.10),
    "AspectRatio": (20.0, 60.0),
    "TetCollapse": (0.10, 0.01),
    "MaximumCornerAngle": (165.0, 178.0),
    "MinTriAngle": (10.0, 2.0),
    "MaxTriAngle": (160.0, 175.0),
    "WarpingFactor": (0.6, 1.0),
    "ParallelDeviation": (60.0, 80.0),
}


class FakeMesh(object):
    """模拟 Mesh 对象：设置决定网格规模，也决定质量表。"""

    K_NODES = 8.12e9  # nodes = K / size^2.14；size=36 -> 3.8M

    def __init__(self, scenario=None, log=None):
        self.log = log
        self.scenario = scenario or {}
        self.Children = []
        self.MeshMetric = FakeEnum("None")
        self.ObjectState = "NoMesh"
        self.Elements = 0
        self.Nodes = 0
        self.body_elements = {}
        self._generated = False
        self._attempts = 0
        self._flags = {}

        # 全局默认值
        self.ElementSize = FakeQuantity(self.scenario.get("global_size_mm", 36.0), u"mm")
        self.UseAdaptiveSizing = False
        self.Resolution = 2
        self.DefeatureTolerance = FakeQuantity(0.0, u"mm")
        self.AutomaticMeshBasedDefeaturing = True
        self.CaptureCurvature = True
        self.CurvatureNormalAngle = FakeQuantity(0.0, u"deg")
        self.CaptureProximity = bool(self.scenario.get("capture_proximity", False))
        self.GrowthRate = 1.2
        self.TransitionRatio = 0.272
        self.Smoothing = self.scenario.get("smoothing", 1)   # 0=Low 1=Medium 2=High
        self.SmoothingIterations = self.scenario.get("smoothing_iterations", 5)
        self.UsePostSmoothing = True
        self.ShapeChecking = True
        self.TopologyChecking = True
        self.ElementOrder = FakeEnum("ProgramControlled")
        self.QuadMesh = bool(self.scenario.get("quad_mesh", True))
        self.StraightSidedElements = False
        self.UseAutomaticInflation = False
        self.PhysicsPreference = FakeEnum("Mechanical")

        self._metric_tables = {}

    # ------------------------------------------------------------- 属性转义
    def __getattr__(self, name):
        # 未定义的属性 -> AttributeError，让 session 的特征探测正常工作
        raise AttributeError(name)

    def GetEnumName(self, attr):
        value = getattr(self, attr, None)
        return getattr(value, "name", str(value))

    # ------------------------------------------------------------- 质量限值
    @property
    def NumberOfMeshQualityMetrics(self):
        return len(METRIC_ORDER)

    def GetVolumeMeshQualityName(self, index):
        if 0 <= int(index) < len(METRIC_ORDER):
            return FakeEnum(METRIC_ORDER[int(index)])
        raise IndexError(index)

    def GetVolumeMeshQualityWarningLimit(self, index):
        name = self.GetVolumeMeshQualityName(index).name
        return DEFAULT_LIMITS.get(name, (0.0, 0.0))[0]

    def GetVolumeMeshQualityErrorLimit(self, index):
        name = self.GetVolumeMeshQualityName(index).name
        return DEFAULT_LIMITS.get(name, (0.0, 0.0))[1]

    # ------------------------------------------------------------- 指标读数
    def _current_table(self):
        name = getattr(self.MeshMetric, "name", "None")
        return self._metric_tables.get(name)

    @property
    def Minimum(self):
        table = self._current_table()
        if table is None:
            return None
        return table["min"]

    @property
    def Maximum(self):
        table = self._current_table()
        return None if table is None else table["max"]

    @property
    def Average(self):
        table = self._current_table()
        return None if table is None else table["avg"]

    @property
    def StandardDeviation(self):
        table = self._current_table()
        return None if table is None else table["std"]

    @property
    def MinimumEdgeLength(self):
        base = float(self.scenario.get("min_edge_mm", 0.000118))
        defeature = float(getattr(self.DefeatureTolerance, "Value", 0.0) or 0.0)
        return FakeQuantity(max(base, defeature * 1.05), u"mm")

    # ------------------------------------------------------------- 控制项
    def AddSizing(self):
        control = FakeMeshControl("Sizing", "Body Sizing", "sizing")
        self.Children.append(control)
        return control

    def AddAutomaticMethod(self):
        control = FakeMeshControl("Automatic Method", "Automatic Method", "method")
        self.Children.append(control)
        return control

    def AddFaceMeshing(self):
        control = FakeMeshControl("Face Meshing", "Face Meshing", "face_meshing")
        self.Children.append(control)
        return control

    def AddRefinement(self):
        control = FakeMeshControl("Refinement", "Refinement", "refinement")
        self.Children.append(control)
        return control

    # ------------------------------------------------------------- 生成网格
    def ClearGeneratedData(self):
        self.Elements = 0
        self.Nodes = 0
        self.body_elements = {}
        self.ObjectState = "NoMesh"
        self._generated = False

    def GenerateMesh(self, *args, **kwargs):
        self._attempts += 1
        fail_times = int(self.scenario.get("fail_times", 0))
        if self._attempts <= fail_times:
            raise RuntimeError("Meshing failed: The mesh generation failed for body '%s'"
                               % self.scenario.get("fail_body", u"卡住体"))

        size = self._effective_size()
        nodes = int(self.K_NODES / (size ** 2.14))
        nodes = int(nodes * self.scenario.get("node_multiplier", 1.0))
        self.Nodes = max(1, nodes)
        self.Elements = max(1, int(self.Nodes / 2.06))
        self.ObjectState = "UpToDate"
        self._generated = True
        self._compute_flags(size)
        self._distribute_elements()
        self._metric_tables = self._build_metric_tables(size)
        return None

    def _effective_size(self):
        sizes = []
        for child in self.Children:
            value = getattr(child.ElementSize, "Value", 0.0)
            if value and value > 0 and getattr(child.Location, "Ids", None):
                sizes.append(value)
        base = float(getattr(self.ElementSize, "Value", 36.0))
        if not sizes:
            return base
        sizes.sort()
        # 用最小局部尺寸主导（和真实模型的行为一致：局部控制决定上限）
        return max(min(sizes), base * 0.12)

    def _compute_flags(self, size):
        flags = {"stuck_bad": bool(self.scenario.get("stuck_bad_quality", False))}
        flags["zero_size_control"] = any(
            (getattr(c.ElementSize, "Value", 0) or 0) <= 0 for c in self.Children)
        flags["degenerate"] = bool(self.scenario.get("degenerate_geometry", True))
        flags["defeatured"] = float(getattr(self.DefeatureTolerance, "Value", 0) or 0) > 0
        smoothing = self.Smoothing
        smoothing_index = smoothing if isinstance(smoothing, int) else {
            "low": 0, "medium": 1, "high": 2}.get(str(smoothing).lower(), 1)
        flags["low_smoothing"] = smoothing_index < 2 or self.SmoothingIterations < 10
        flags["quad_shell"] = bool(self.QuadMesh and self.scenario.get("shells"))
        flags["no_proximity"] = not self.CaptureProximity
        flags["over_refined"] = size < 6.0
        self._flags = flags

    def _distribute_elements(self):
        bodies = self.scenario.get("bodies") or []
        if not bodies:
            self.body_elements = {}
            return
        stuck = set(self.scenario.get("stuck_bodies") or [])
        controlled = set()
        for child in self.Children:
            for geo_id in getattr(child.Location, "Ids", []) or []:
                controlled.add(int(geo_id))
        weights = {}
        for body in bodies:
            if body.Name in stuck and body.Id not in controlled:
                weights[body.Id] = 0
            else:
                weights[body.Id] = 1.0
        total = sum(weights.values()) or 1.0
        for geo_id, weight in weights.items():
            self.body_elements[geo_id] = int(self.Elements * weight / total)

    def _build_metric_tables(self, size):
        flags = self._flags
        bad = flags.get("stuck_bad", False)
        degenerate = flags.get("degenerate") and not flags.get("defeatured")
        zero = flags.get("zero_size_control", False)
        smoothing = flags.get("low_smoothing", False)
        quad = flags.get("quad_shell", False)
        proximity = flags.get("no_proximity", True)

        tables = {
            "ElementQuality": {"min": 0.35, "max": 1.0, "avg": 0.72, "std": 0.11},
            "Skewness": {"min": 0.001, "max": 0.52, "avg": 0.26, "std": 0.10},
            "JacobianRatio": {"min": 1.0, "max": 2.4, "avg": 1.15, "std": 0.12},
            "JacobianRatioCornerNodes": {"min": 0.72, "max": 1.0, "avg": 0.95, "std": 0.04},
            "JacobianRatioGaussPoints": {"min": 0.78, "max": 1.0, "avg": 0.97, "std": 0.03},
            "AspectRatio": {"min": 1.0, "max": 6.5, "avg": 2.1, "std": 0.9},
            "TetCollapse": {"min": 0.31, "max": 0.99, "avg": 0.72, "std": 0.11},
            "MaximumCornerAngle": {"min": 62.0, "max": 118.0, "avg": 96.0, "std": 9.0},
            "MinTriAngle": {"min": 26.0, "max": 55.0, "avg": 40.0, "std": 5.0},
            "MaxTriAngle": {"min": 70.0, "max": 118.0, "avg": 92.0, "std": 7.0},
            "WarpingFactor": {"min": 0.0, "max": 0.01, "avg": 0.001, "std": 0.002},
            "ParallelDeviation": {"min": 0.0, "max": 22.0, "avg": 9.0, "std": 6.0},
        }

        if smoothing:
            tables["Skewness"].update({"max": max(tables["Skewness"]["max"], 0.91),
                                       "avg": 0.62, "std": 0.22})
            tables["ElementQuality"].update({"avg": 0.45})
        if proximity:
            tables["Skewness"]["max"] = max(tables["Skewness"]["max"], 0.93)
        if degenerate:
            tables["ElementQuality"].update({"min": 0.005, "avg": 0.44, "std": 0.27})
            tables["Skewness"].update({"max": 0.999, "avg": 0.62, "std": 0.30})
            tables["JacobianRatio"].update({"max": 41.0, "avg": 1.3, "std": 1.5})
            tables["AspectRatio"].update({"max": 6520.0, "avg": 6.7, "std": 8.7})
            tables["TetCollapse"].update({"min": 0.0037, "avg": 0.38, "std": 0.23})
            tables["MaximumCornerAngle"].update({"max": 179.3, "avg": 117.0, "std": 25.0})
        if zero:
            tables["JacobianRatio"].update({"max": 40.0, "avg": 1.3})
        if quad:
            tables["MinTriAngle"].update({"min": 0.95, "avg": 3.5, "std": 0.4})
            tables["MaxTriAngle"].update({"max": 172.3, "avg": 90.0, "std": 4.6})
            tables["ParallelDeviation"].update({"max": 90.0, "avg": 17.7})
        if flags.get("over_refined"):
            tables["AspectRatio"]["max"] = max(tables["AspectRatio"]["max"], 25.0)
            tables["JacobianRatio"]["max"] = max(tables["JacobianRatio"]["max"], 12.0)
        if bad:
            # 无论怎么改设置都修不好（用于验证"人工决策"分支）
            tables["ElementQuality"].update({"min": 0.006, "avg": 0.30})
            tables["TetCollapse"].update({"min": 0.004, "avg": 0.25})
            tables["Skewness"].update({"max": 0.995, "avg": 0.70})
            tables["AspectRatio"].update({"max": 800.0})
        return tables


class FakeMeshModel(object):
    def __init__(self, mesh):
        self.Mesh = mesh
        self.Geometry = None


class FakeModel(object):
    def __init__(self, name, geometry, mesh):
        self.Name = name
        self.Geometry = geometry
        self.Mesh = mesh
        self.MeshEdit = None


class FakeProject(object):
    def __init__(self, model, directory):
        self.Model = model
        self.ProjectDirectory = directory
        self.ProductVersion = "2022 R1 (fake)"
        self.saved = 0

    def Save(self):
        self.saved += 1
        return True


class FakeDataModel(object):
    def __init__(self, project, mesh):
        self.Project = project
        self._mesh = mesh
        self.Application = FakeApplication()

    def MeshDataByName(self, name):
        if name not in ("Global",):
            raise RuntimeError("no such mesh data: %s" % name)
        return FakeMeshData(self._mesh)


class FakeApplication(object):
    Version = "2022 R1 (fake Mechanical)"


class FakeExtAPI(object):
    def __init__(self, dm, selection_manager):
        self.DataModel = dm
        self.SelectionManager = selection_manager
        self.Application = FakeApplication()


# --------------------------------------------------------------------------
# 场景装配
# --------------------------------------------------------------------------
def _build_env(scenario):
    bodies = scenario.get("bodies") or []
    edges = scenario.get("edges") or []
    box = scenario.get("box") or FakeBox((-500, -300, -1200), (500, 300, 1200))
    geometry = FakeGeometry(bodies, edges, box)
    mesh = FakeMesh(scenario)
    for preset in scenario.get("controls") or []:
        control = FakeMeshControl(preset.get("name", u"control"),
                                  preset.get("category", "Body Sizing"),
                                  preset.get("kind", "sizing"))
        control.ElementSize = FakeQuantity(float(preset.get("size_mm", 0.0) or 0.0), u"mm")
        if preset.get("method"):
            control.Method = FakeEnum(preset["method"])
        if preset.get("algorithm"):
            control.Algorithm = FakeEnum(preset["algorithm"])
        control.Location = FakeSelectionInfo(preset.get("ids") or [])
        control.Suppressed = bool(preset.get("suppressed", False))
        mesh.Children.append(control)
    geometry_mesh_holder = FakeMeshModel(mesh)
    geometry_mesh_holder.Geometry = geometry
    model = FakeModel(scenario.get("model_name", "假模型"), geometry, mesh)
    project = FakeProject(model, scenario.get("project_dir", u"D:\\fake\\proj"))
    dm = FakeDataModel(project, mesh)
    manager = FakeSelectionManager()
    extapi = FakeExtAPI(dm, manager)
    return {"extapi": extapi, "mesh": mesh, "model": model, "dm": dm,
            "project": project, "geometry": geometry, "bodies": bodies}


def install(scenario):
    """把假环境注入 __main__，让 session.find_injected 找得到。"""
    env = _build_env(scenario)
    main = sys.modules.get("__main__")
    if main is None:
        raise RuntimeError("no __main__")
    ns = main.__dict__
    ns["ExtAPI"] = env["extapi"]
    ns["Quantity"] = Quantity

    _install_enum_modules()
    return env


def _install_enum_modules():
    """伪造 Ansys.* 枚举模块，供 from ... import 使用。"""
    def module(name):
        existing = sys.modules.get(name)
        if existing is None:
            existing = type(sys)(name)
            sys.modules[name] = existing
        return existing

    ansys = module("Ansys")
    mech = module("Ansys.Mechanical")
    dm_mod = module("Ansys.Mechanical.DataModel")
    enums = module("Ansys.Mechanical.DataModel.Enums")
    ansys.Mechanical = mech
    mech.DataModel = dm_mod
    dm_mod.Enums = enums

    if not hasattr(enums, "MeshMetricType"):
        mesh_metric = type("MeshMetricType", (), {})
        for name in ["None"] + METRIC_ORDER:
            setattr(mesh_metric, name, FakeEnum(name))
        enums.MeshMetricType = mesh_metric
        categories = type("DataModelObjectCategory", (), {})
        for name in ["Body", "Edge", "Face", "Part", "BodySizing", "AutomaticMethod"]:
            setattr(categories, name, FakeEnum(name))
        enums.DataModelObjectCategory = categories

    common = module("Ansys.ACT.Interfaces.Common")
    module("Ansys.ACT")
    module("Ansys.ACT.Interfaces")
    ansys.ACT = sys.modules["Ansys.ACT"]
    sys.modules["Ansys.ACT"].Interfaces = sys.modules["Ansys.ACT.Interfaces"]
    sys.modules["Ansys.ACT.Interfaces"].Common = common
    if not hasattr(common, "SelectionTypeEnum"):
        selection_enum = type("SelectionTypeEnum", (), {})
        selection_enum.GeometryEntities = FakeEnum("GeometryEntities")
        common.SelectionTypeEnum = selection_enum


def uninstall():
    main = sys.modules.get("__main__")
    if main is not None:
        main.__dict__.pop("ExtAPI", None)
        main.__dict__.pop("Quantity", None)


# --------------------------------------------------------------------------
# 预置场景
# --------------------------------------------------------------------------
def container_like_scenario():
    """模仿你的集装箱：286 体太大，这里用 6 体 + 2 壳 + 0 尺寸控制 + 碎边。"""
    bodies = [
        FakeBody(1, u"底板2\\固体4"),
        FakeBody(2, u"中面\\波纹板中面1", thickness_mm=2.5),
        FakeBody(3, u"中面\\波纹板中面4", thickness_mm=2.5),
        FakeBody(4, u"中间隔断2\\中间隔断"),
        FakeBody(5, u"支梁架"),
        FakeBody(6, u"小三角板1"),
    ]
    edges = [
        FakeEdge(u"Edge1", 0.000118, owner=bodies[0]),
        FakeEdge(u"Edge2", 0.0002, owner=bodies[3]),
        FakeEdge(u"Edge3", 12.0, owner=bodies[1]),
    ]
    return {
        "scenario_name": "container_like",
        "bodies": bodies,
        "edges": edges,
        "shells": True,
        "global_size_mm": 36.0,
        "smoothing": 1,
        "smoothing_iterations": 5,
        "quad_mesh": True,
        "capture_proximity": False,
        "degenerate_geometry": True,
        "min_edge_mm": 0.000118,
        "node_multiplier": 0.015,
        "controls": [
            {"name": u"中面", "category": "Body Sizing", "size_mm": 32.0, "ids": [2, 3]},
            {"name": u"中隔断", "category": "Body Sizing", "size_mm": 0.0, "ids": [4]},
            {"name": u"小三角", "category": "Body Sizing", "size_mm": 8.0, "ids": [6]},
            {"name": u"遗留控制", "category": "Body Sizing", "size_mm": 0.0, "ids": [1]},
            {"name": u"Patch Conforming Method", "category": "Automatic Method",
             "method": "AllTriAllTet", "ids": [5]},
        ],
    }


def simple_good_scenario():
    bodies = [FakeBody(1, u"Plate"), FakeBody(2, u"Block")]
    edges = [FakeEdge(u"Edge1", 8.0, owner=bodies[0])]
    return {
        "scenario_name": "simple_good",
        "bodies": bodies,
        "edges": edges,
        "shells": False,
        "global_size_mm": 20.0,
        "smoothing": 2,
        "smoothing_iterations": 10,
        "quad_mesh": False,
        "capture_proximity": True,
        "degenerate_geometry": False,
        "min_edge_mm": 3.0,
        "node_multiplier": 0.02,
    }
