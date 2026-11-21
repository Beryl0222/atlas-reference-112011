"""图集编校链接工具。

模块划分：

- ``atlas.model``：加载六份交接资料，建立按版次的索引；
- ``atlas.gazetteer``：地名候选、别名、改名/拆分关系；
- ``atlas.scan``：从正文、题注、表头切出指代并解析年代与范围；
- ``atlas.sheets``：按年代与覆盖匹配图幅组，记录换稿与印前状态；
- ``atlas.workflow``：编辑确认台账、复核、渠道闸门、反查与阻塞分类。

所有输出键名沿用资料中的中文词，方便编校直接核对原文。
"""

from atlas.model import Workspace, load_workspace

__all__ = ["Workspace", "load_workspace"]
