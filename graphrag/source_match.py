"""判斷檢索到的子塊是否命中測試集標示的答案位置（給評估程式用）。

測試集的 source 格式為 `doc_id#章節標題`，標題可能是：
  - 縮寫：`IT-FAQ-001#Q2` 對「Q2. 忘記密碼怎麼辦？」
  - 舊版編號：`HR-002#第二條 特別休假（特休）` 對新版「3.2 特別休假（特休）」
所以先嚴格比對（完整或前綴），再去掉章節編號比對標題文字。
"""
import re

_NUMBERING = re.compile(r"^(第.+?[條章]|[一二三四五六七八九十]+、|Q\d+\.|\d+(\.\d+)*)\s*")


def heading_matches(ref: str, heading: str | None) -> bool:
    """完整相同或前綴相同（「Q1」不可配到「Q10」）。"""
    if not heading:
        return False
    return heading == ref or (heading.startswith(ref) and not heading[len(ref)].isdigit())


def _core(heading: str) -> str:
    return _NUMBERING.sub("", heading).strip()


def source_matches(source: str, doc_id: str, section_path: list[str]) -> bool:
    """source：`doc_id#章節標題`；section_path：子塊的章節路徑（例如 ["三、作業流程", "3.2 特別休假（特休）"]）。"""
    src_doc, ref = source.split("#", 1)
    if src_doc != doc_id:
        return False
    if any(heading_matches(ref, h) for h in section_path):
        return True
    core = _core(ref)
    # 只對含中文的標題文字做寬鬆比對；「Q1」這類代碼若做子字串比對會誤配「Q10」
    if not re.search(r"[^\x00-\x7f]", core):
        return False
    return any(core == _core(h) or core in h for h in section_path)
