"""
tools/__init__.py

工具包入口 — 通过 import 触发所有子包的加载,从而让 `BaseTool.__subclasses__()` 能扫到全部
具体 tool 类。`ToolManager` 实例化时不主动 import 工具模块,所以**任何想用 tool_manager
的入口必须先 `import tools`**。

子包分组(权限单位,详见 CLAUDE.md):
    kb_query    : 共享只读组 — 所有 agent(主管+专家)均可调用
    world       : WorldArchitect 写权限(bible / foreshadowing)
    character   : CastingDirector 写权限(characters / relationships)
    story       : PlotArchitect 写权限(outline / arcs / chapter plans)
    dramaturg   : SceneShowrunner 写权限(scene_beatsheets)
    writer      : SceneShowrunner 写权限(drafts / finals)
    review      : SceneShowrunner 内部(issues / guidance)
    style       : 共享读权限(风格示例库)
    system      : 通用工具(KB diff 应用、日志记录)
    experts     : 12 个 ExpertTool 子类(由对应主管在 tool_scope 中持有)

新增工具时:
    1. 把文件放到对应子包下
    2. 在该子包的 __init__.py 里 `from .new_tool import NewTool`
    3. 通过本文件即可被自动发现,无需在 tool_manager 注册
"""

# 顺序无关紧要,但按权限分组从只读到写到专家可保持代码可读性
from . import kb_query  # noqa: F401
from . import style  # noqa: F401
from . import world  # noqa: F401
from . import character  # noqa: F401
from . import story  # noqa: F401
from . import dramaturg  # noqa: F401
from . import writer  # noqa: F401
from . import review  # noqa: F401
from . import system  # noqa: F401
from . import experts  # noqa: F401
from . import managers  # noqa: F401  # Director 可调用的子主管工具
