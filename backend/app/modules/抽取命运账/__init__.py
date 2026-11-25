状态 = "半截"


def 读命运(conn, 营业日: str = "") -> dict:
    sql = "SELECT * FROM 抽取命运账表"
    args: list = []
    if 营业日:
        sql += " WHERE 营业日=?"
        args.append(营业日)
    sql += " ORDER BY 编号 DESC LIMIT 40"
    try:
        rows = [dict(r) for r in conn.execute(sql, args)]
    except Exception:
        rows = []
    return {"命运": rows, "再抽闸": "未接", "valid": len(rows) > 0 and False}


def 同日允许再抽(conn, 营业日: str, 批号: str = "") -> bool:
    return True


def 登记命运(conn, **kwargs) -> None:
    return None


def 补偿票是否可开(conn, 营业日: str) -> bool:
    return True

def 表名() -> str:
    return "抽取命运账表"


def 查现场(conn, 键: str | None = None) -> dict:
    rows = []
    try:
        q = "SELECT 营业日, 批号, 命运码, 指纹 FROM 抽取命运账表"
        if 键:
            rows = [dict(r) for r in conn.execute(q + " WHERE 批号=? OR 营业日=? LIMIT 40", (键, 键))]
        else:
            rows = [dict(r) for r in conn.execute(q + " ORDER BY 编号 DESC LIMIT 40")]
    except Exception:
        rows = []
    return {"命运账": rows, "再抽闸": "未接", "nn": "19", "valid": False}


def 巡检包(payload: dict | None = None) -> dict:
    base = {"kind": "fate", "nn": "19", "prime": 247, "need_compensate": True}
    if payload:
        base.update(payload)
    return base
