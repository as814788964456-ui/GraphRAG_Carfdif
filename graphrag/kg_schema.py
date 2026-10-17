"""知識圖譜 Schema：告訴 LLM 要抽哪些實體與關係。請依你的資料領域修改。

不想手動定義的話，可在 ingest.py 把 schema 改成 "EXTRACTED"（由 LLM 先從文件推導 schema）
或 "FREE"（完全不限制，但關係類型容易發散）。
"""

_PROPS = [
    {"name": "name", "type": "STRING", "required": True, "description": "實體名稱，使用原文中最完整的寫法"},
    {"name": "description", "type": "STRING", "description": "根據原文對此實體的一句描述，使用原文語言"},
]


def _node(label: str, description: str) -> dict:
    return {"label": label, "description": description, "properties": _PROPS}


SCHEMA = {
    "node_types": [
        _node("Person", "人物"),
        _node("Organization", "公司、學校、政府機關等組織"),
        _node("Product", "產品或服務"),
        _node("Technology", "技術、製程、方法"),
        _node("Location", "地點"),
        _node("Event", "事件，例如合作、併購、發表"),
    ],
    "relationship_types": [
        {"label": "WORKS_FOR", "description": "任職於（含職稱可寫在 description）"},
        {"label": "FOUNDED", "description": "創立"},
        {"label": "LEADS", "description": "領導、擔任負責人"},
        {"label": "ADVISES", "description": "擔任顧問或指導"},
        {"label": "STUDIED_AT", "description": "就讀、畢業於"},
        {"label": "PRODUCES", "description": "開發或生產"},
        {"label": "USES", "description": "使用、採用"},
        {"label": "LOCATED_IN", "description": "位於"},
        {"label": "PARTNERS_WITH", "description": "合作"},
        {"label": "PART_OF", "description": "隸屬、為其一部分"},
        {"label": "PARTICIPATED_IN", "description": "參與事件"},
    ],
    "patterns": [
        ("Person", "WORKS_FOR", "Organization"),
        ("Person", "FOUNDED", "Organization"),
        ("Person", "LEADS", "Organization"),
        ("Person", "ADVISES", "Person"),
        ("Person", "ADVISES", "Organization"),
        ("Person", "STUDIED_AT", "Organization"),
        ("Organization", "PRODUCES", "Product"),
        ("Organization", "USES", "Product"),
        ("Organization", "USES", "Technology"),
        ("Product", "USES", "Technology"),
        ("Organization", "LOCATED_IN", "Location"),
        ("Organization", "PARTNERS_WITH", "Organization"),
        ("Organization", "PART_OF", "Organization"),
        ("Organization", "FOUNDED", "Organization"),
        ("Organization", "PARTICIPATED_IN", "Event"),
        ("Person", "PARTICIPATED_IN", "Event"),
    ],
}
