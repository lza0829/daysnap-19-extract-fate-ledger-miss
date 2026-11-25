"""抽取命运账 · 二次抽取写闸（命运前置拒绝口径）。

本库在某两条正向路径中择定并贯彻 **命运前置拒绝**：

* 首次抽取落成（成功或失败）必须与快照写同票写下命运行：
  （营业日、批号、成败、有效镜指纹）。成功回执绝不允许命运空号。
* 同日再抽必须先消化命运账：
  - 无命运行且当日已有有效镜 → 整票拒绝，有效镜不得动（抹掉命运行再抽亦拒）；
  - 有效命运=失败 → 整票拒绝（失败批命运在册时再抽必须拒绝）；
  - 有效命运=成功 → 允许按台参口径重抽（覆盖同日快照），重抽须重钉命运；
  - 有效命运=已补偿 → 视为该日已清空，可重新首抽。
* 命运按营业日精确取用，邻日命运绝不串用。
* 命运写、抽写、回放、差额同票：登记命运不单独提交，随调用方事务一并落库；
  回放只认「有效命运=成功且有效镜指纹相符」的营业日。
* 抽写与命运补登交错：二者均以 BEGIN IMMEDIATE 串行，补登见在途批即失败闭；
  同一营业日至多一条有效命运由部分唯一索引在库层兜底。
"""

import hashlib
import json
from typing import Any, Iterable

状态 = "已落地"

表名_ = "抽取命运账表"
命运码集 = ("成功", "失败", "已补偿")


def 表名() -> str:
    return 表名_


def 计算指纹(行们: Iterable[Any], 批号: str = "") -> str:
    """有效镜指纹：账户键|余额|抽取批次 排序后连同批号做 SHA256。

    行可为 sqlite Row 或 dict；缺字段按空值处理，保证抽取侧与回放侧算法一致。
    """
    def 取(行, 名: str) -> str:
        try:
            v = 行[名]
        except (KeyError, IndexError):
            v = ""
        return "" if v is None else str(v)

    片段 = sorted(
        f"{取(r, '账户键')}|{取(r, '余额')}|{取(r, '抽取批次编号')}" for r in 行们
    )
    原文 = (批号 or "") + "|" + ";".join(片段)
    return hashlib.sha256(原文.encode("utf-8")).hexdigest()[:32]


def 取有效命运(conn, 营业日: str) -> dict | None:
    """按营业日精确取当前有效命运行；邻日不串用。"""
    if not 营业日:
        return None
    row = conn.execute(
        "SELECT * FROM 抽取命运账表 WHERE 营业日=? AND 有效=1 "
        "ORDER BY 编号 DESC LIMIT 1",
        (营业日,),
    ).fetchone()
    return dict(row) if row else None


def _当日有效镜(conn, 营业日: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT 账户键, 余额, 抽取批次编号 FROM 周期快照 "
            "WHERE 营业日=? AND 状态='有效' ORDER BY 账户键",
            (营业日,),
        )
    ]


# 对外只读别名：回放侧按同一算法取有效镜，保证指纹口径一致。
当日有效镜 = _当日有效镜


def 判再抽(conn, 营业日: str) -> dict[str, Any]:
    """判定该营业日是否允许再抽（须在 BEGIN IMMEDIATE 事务内调用）。

    返回 允许/原因/命运/有效镜数；缺命运但已有有效镜、命运=失败均整票拒绝。
    """
    命运 = 取有效命运(conn, 营业日)
    镜 = _当日有效镜(conn, 营业日)
    if 命运 is None:
        if 镜:
            return {
                "允许": False,
                "原因": (
                    f"营业日 {营业日} 缺抽取命运账但已有 {len(镜)} 条有效镜，"
                    "拒绝同日再抽；有效镜不得被无命运行覆盖"
                ),
                "命运": None,
                "有效镜数": len(镜),
            }
        return {
            "允许": True,
            "原因": "首次抽取",
            "命运": None,
            "有效镜数": 0,
        }
    码 = 命运["命运码"]
    if 码 == "失败":
        return {
            "允许": False,
            "原因": (
                f"营业日 {营业日} 有效命运=失败（批 {命运['批号']}），"
                "失败批命运在册时禁止同日再抽；须先补偿回滚"
            ),
            "命运": 命运,
            "有效镜数": len(镜),
        }
    if 码 == "成功":
        if 命运.get("指纹"):
            现 = 计算指纹(镜, 命运["批号"])
            if 镜 and 现 != 命运["指纹"]:
                return {
                    "允许": False,
                    "原因": (
                        f"营业日 {营业日} 有效镜指纹与成功命运不符（批 {命运['批号']}），"
                        "拒绝无命运行覆盖已被改写的有效镜"
                    ),
                    "命运": 命运,
                    "有效镜数": len(镜),
                }
        return {
            "允许": True,
            "原因": "成功命运在册，按台参口径重抽并重钉命运",
            "命运": 命运,
            "有效镜数": len(镜),
        }
    # 已补偿：该日已整票清理，可重新首抽
    return {
        "允许": True,
        "原因": "已补偿命运在册，可重新首抽",
        "命运": 命运,
        "有效镜数": len(镜),
    }


def 同日允许再抽(conn, 营业日: str, 批号: str = "") -> bool:
    """兼容入口：命运前置拒绝口径下是否允许同日再抽。"""
    return bool(判再抽(conn, 营业日)["允许"])


def 登记命运(
    conn,
    *,
    营业日: str,
    批号: str,
    命运码: str,
    指纹: str = "",
    性质: str = "首抽",
    载荷: dict | None = None,
) -> dict[str, Any]:
    """在调用方事务内写下命运行（不单独提交）。

    先把该日既有有效命运降为历史，再插入新有效行；同营业日唯一有效由
    部分唯一索引兜底，任何并发插队都会在库层失败闭。
    """
    if 命运码 not in 命运码集:
        raise ValueError(f"非法命运码：{命运码}")
    conn.execute(
        "UPDATE 抽取命运账表 SET 有效=0 WHERE 营业日=? AND 有效=1",
        (营业日,),
    )
    from app.services.时间 import 此刻

    时刻 = 此刻()
    conn.execute(
        "INSERT INTO 抽取命运账表"
        "(营业日, 批号, 命运码, 指纹, 性质, 有效, 时刻, 载荷) "
        "VALUES (?,?,?,?,?,1,?,?)",
        (
            营业日,
            批号,
            命运码,
            指纹,
            性质,
            时刻,
            json.dumps(载荷 or {}, ensure_ascii=False),
        ),
    )
    return {
        "营业日": 营业日,
        "批号": 批号,
        "命运": 命运码,
        "命运码": 命运码,
        "指纹": 指纹,
        "性质": 性质,
        "valid": True,
    }


def 补偿票是否可开(conn, 营业日: str) -> bool:
    """补偿票口径未采用（本库贯彻命运前置拒绝）；仅在有失败命运时提示可补偿。"""
    命运 = 取有效命运(conn, 营业日)
    return bool(命运 and 命运["命运码"] == "失败")


def 读命运(conn, 营业日: str = "") -> dict[str, Any]:
    """读取命运账（只读）：有效命运 + 历史行。"""
    if conn is None:
        return {"命运": [], "有效命运": None, "valid": False}
    sql = "SELECT * FROM 抽取命运账表"
    args: list = []
    if 营业日:
        sql += " WHERE 营业日=?"
        args.append(营业日)
    sql += " ORDER BY 编号 DESC LIMIT 120"
    try:
        rows = [dict(r) for r in conn.execute(sql, args)]
    except Exception:
        rows = []
    有效 = 取有效命运(conn, 营业日) if 营业日 else None
    return {"命运": rows, "有效命运": 有效, "valid": 有效 is not None}


def 查现场(conn, 键: str | None = None) -> dict[str, Any]:
    """命运账核证（只读）：按营业日输出命运行与 valid。

    valid 口径：该营业日存在唯一有效命运、命运=成功、且指纹与当前有效镜相符。
    """
    日 = (键 or "").strip()
    if not 日:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 抽取命运账表 ORDER BY 编号 DESC LIMIT 40"
            )
        ]
        return {
            "营业日": "",
            "行": [_行(r) for r in rows],
            "有效命运": None,
            "有效镜数": 0,
            "valid": False,
            "口径": "命运前置拒绝",
        }
    命运 = 取有效命运(conn, 日)
    镜 = _当日有效镜(conn, 日)
    行 = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM 抽取命运账表 WHERE 营业日=? ORDER BY 编号 DESC LIMIT 40",
            (日,),
        )
    ]
    valid = False
    指纹相符 = False
    if 命运 and 命运["命运码"] == "成功":
        指纹相符 = 计算指纹(镜, 命运["批号"]) == (命运.get("指纹") or "")
        valid = 指纹相符 and bool(镜)
    return {
        "营业日": 日,
        "行": [_行(r) for r in 行],
        "有效命运": _行(命运) if 命运 else None,
        "有效镜数": len(镜),
        "指纹相符": 指纹相符,
        "valid": valid,
        "口径": "命运前置拒绝",
    }


def _行(r: dict) -> dict[str, Any]:
    return {
        "编号": r.get("编号"),
        "营业日": r.get("营业日", ""),
        "日": r.get("营业日", ""),
        "批号": r.get("批号", ""),
        "命运": r.get("命运码", ""),
        "命运码": r.get("命运码", ""),
        "指纹": r.get("指纹", ""),
        "性质": r.get("性质", ""),
        "有效": bool(r.get("有效", 0)),
        "时刻": r.get("时刻", ""),
    }


def 巡检包(payload: dict | None = None) -> dict:
    base = {
        "kind": "fate",
        "nn": "19",
        "口径": "命运前置拒绝",
        "rules": [
            "成功回执不得命运空号",
            "无命运行不得覆盖已有有效镜",
            "邻日命运不串用",
            "命运写与抽写/回放/差额同票",
            "抽写与命运补登交错失败闭",
        ],
        "need_compensate": True,
    }
    if payload:
        base.update(payload)
    return base
