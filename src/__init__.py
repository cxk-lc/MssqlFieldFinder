"""库表字段内容检索器（MssqlFieldFinder）。

纯逻辑层（models / matcher / sqlbuild / drivers / connstr / paths / profiles）不依赖任何 IO，
engine 与 app 分别承担数据库访问与图形界面职责。

``__app_name__`` 与 ``__version__`` 是两个**唯一来源**：

* ``__app_name__`` —— 打包产物文件名与产物目录名（窗口标题不用它，界面用中文名）；
* ``__version__``  —— 界面标题、打包产物文件名以及自检输出都从这里取。

改名字或改版本只需改本文件，``bat\build.bat`` 会调用 ``main.py --app-name / --version`` 读取。
"""

__app_name__ = "MssqlFieldFinder"
__version__ = "1.6.0"
__all__ = ["__app_name__", "__version__"]
