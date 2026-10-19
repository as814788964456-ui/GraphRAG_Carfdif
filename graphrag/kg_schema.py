"""知識圖譜 Schema：告訴 LLM 要抽哪些實體與關係。請依你的資料領域修改。

目前針對企業知識庫（SOP、規章、IT FAQ、系統手冊、術語表、專案經驗）設計：
員工的問題多半是「這件事找誰／在哪個系統辦／要填什麼表／有什麼限制／出錯怎麼排除」，
所以實體以部門、角色、系統、流程、表單、規定、術語、問題與解法為主。
文件本身、章節、版本與文件間引用由 ingest_chunks.py 寫成 Document／ParentChunk／Chunk 與
REFERENCES／SUPERSEDES 關係，不需要 LLM 抽；這裡的標籤也刻意避開 Document、Chunk。

不想手動定義的話，可在 ingest.py 把 schema 改成 "EXTRACTED"（由 LLM 先從文件推導 schema）
或 "FREE"（完全不限制，但關係類型容易發散）。
"""

_PROPS = [
    {"name": "name", "type": "STRING", "required": True, "description": "實體名稱，使用原文中最完整的寫法（有正式名稱就用正式名稱，例如「FortiClient VPN」而非「VPN 軟體」）"},
    {"name": "description", "type": "STRING", "description": "根據原文對此實體的一句描述，使用原文語言；規定類請寫出具體數字、期限或門檻"},
]
_CODE = {"name": "code", "type": "STRING", "description": "代碼或編號，例如部門代碼 UW、表單編號 GA-02、縮寫 SLA；原文沒有就留空"}


def _node(label: str, description: str, code: bool = False) -> dict:
    return {"label": label, "description": description, "properties": _PROPS + ([_CODE] if code else [])}


SCHEMA = {
    "node_types": [
        _node("Department", "部門、處、組或對外窗口單位（例如人力資源處、資訊處服務台）", code=True),
        _node("Role", "職務或角色（例如直屬主管、處級主管、代理人、核保人員、新進員工）"),
        _node("System", "內部資訊系統、軟體或工具（例如 LIFE-Core、eFlow、HR Portal、FortiClient VPN、Teams）", code=True),
        _node("Process", "作業流程或申請程序（例如請假申請、費用報銷、理賠受理、VPN 權限申請）"),
        _node("Form", "表單或應附文件（例如出差申請單、診斷證明）", code=True),
        _node("Rule", "規定、限制或條件：期限、天數、金額門檻、核決權限、適用資格"),
        _node("Term", "專業術語、內部縮寫或行話（例如 APL、EDD、PEP、SLA）", code=True),
        _node("Issue", "常見問題、錯誤訊息或異常狀況（例如 VPN 錯誤 -7200、密碼過期、印表機卡紙）"),
        _node("Solution", "問題的處理方式或排除步驟"),
        _node("Contact", "聯絡方式或窗口：分機、信箱、服務台、工單類別"),
        _node("Project", "過去執行的專案"),
        _node("Lesson", "專案的經驗、教訓或改善建議"),
    ],
    "relationship_types": [
        {"label": "RESPONSIBLE_FOR", "description": "負責、主辦或管理"},
        {"label": "APPROVES", "description": "負責簽核或核准"},
        {"label": "PERFORMED_IN", "description": "流程在此系統中辦理或操作"},
        {"label": "REQUIRES", "description": "流程需要填寫的表單或附件"},
        {"label": "GOVERNED_BY", "description": "受此規定限制"},
        {"label": "PRECEDES", "description": "前一流程完成後才進行下一流程"},
        {"label": "APPLIES_TO", "description": "規定適用的角色或部門"},
        {"label": "REFERS_TO", "description": "術語或縮寫指的是哪個部門、系統或流程"},
        {"label": "AFFECTS", "description": "問題發生在哪個系統"},
        {"label": "RESOLVED_BY", "description": "問題的處理方式"},
        {"label": "ESCALATE_TO", "description": "自行處理不了時找的窗口"},
        {"label": "BELONGS_TO", "description": "隸屬於某部門"},
        {"label": "DELIVERED", "description": "專案導入或開發的系統"},
        {"label": "LEARNED", "description": "專案得到的經驗教訓"},
    ],
    "patterns": [
        ("Department", "RESPONSIBLE_FOR", "Process"),
        ("Department", "RESPONSIBLE_FOR", "System"),
        ("Department", "RESPONSIBLE_FOR", "Form"),
        ("Role", "RESPONSIBLE_FOR", "Process"),
        ("Role", "APPROVES", "Process"),
        ("Department", "APPROVES", "Process"),
        ("Process", "PERFORMED_IN", "System"),
        ("Process", "REQUIRES", "Form"),
        ("Process", "GOVERNED_BY", "Rule"),
        ("Process", "PRECEDES", "Process"),
        ("Process", "ESCALATE_TO", "Contact"),
        ("Rule", "APPLIES_TO", "Role"),
        ("Rule", "APPLIES_TO", "Department"),
        ("Term", "REFERS_TO", "Department"),
        ("Term", "REFERS_TO", "System"),
        ("Term", "REFERS_TO", "Process"),
        ("Issue", "AFFECTS", "System"),
        ("Issue", "RESOLVED_BY", "Solution"),
        ("Issue", "ESCALATE_TO", "Contact"),
        ("Contact", "BELONGS_TO", "Department"),
        ("Role", "BELONGS_TO", "Department"),
        ("Department", "BELONGS_TO", "Department"),
        ("Project", "DELIVERED", "System"),
        ("Department", "RESPONSIBLE_FOR", "Project"),
        ("Project", "LEARNED", "Lesson"),
    ],
}
