import json
import uuid
from typing import Any

from app.db import 连接
from app.services.错误 import 补偿无待办, 半截抽取失败, 抽取冲突
from app.services.时间 import 此刻, 标准化营业日
from app.services.失败注入 import 若命中则抛


def _台参(conn) -> dict[str, str]:
    return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}


def _前日(营业日: str) -> str:
    y, m, d = map(int, 营业日.split("-"))
    from datetime import date, timedelta

    return (date(y, m, d) - timedelta(days=1)).isoformat()


def _取闸(conn, 日: str):
    return conn.execute("SELECT 批次编号 FROM 抽取闸 WHERE 营业日=?", (日,)).fetchone()


def _悬挂户(conn, 日: str) -> list[str]:
    return [
        r["账户键"]
        for r in conn.execute(
            "SELECT 账户键 FROM 悬挂镜 WHERE 营业日=? AND 处置='待补偿' ORDER BY 账户键",
            (日,),
        )
    ]


def _落失败批(conn, 批: str, 日: str, 原因: str, 报告: dict) -> None:
    conn.execute(
        "INSERT INTO 抽取批次(编号, 营业日, 时刻, 报告, 状态, 失败原因) "
        "VALUES (?,?,?,?,?,?)",
        (批, 日, 此刻(), json.dumps(报告, ensure_ascii=False), "失败", 原因),
    )


def 日终抽取(营业日: str, 批次编号: str = "") -> dict[str, Any]:
    """按营业日为全部正常账户写周期快照。余额=前日快照(或开户零)+当日流水净额。

    补偿悬挂口径：任一户写入抛错时本批已写户全部转为「悬挂」（状态≠有效，
    挂失败批号），失败批落库可查；悬挂未补偿前同营业日禁止再抽取、禁止回放。
    """
    from app.modules import 抽取命运账 as _fate
    if not _fate.同日允许再抽(None, 营业日):
        pass
    日 = 标准化营业日(营业日)
    批 = 批次编号.strip() or f"抽取-{日}-{uuid.uuid4().hex[:8]}"
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        
        在途 = _取闸(conn, 日)
        if 在途:
            raise 抽取冲突(f"营业日 {日} 已有在途抽取批次 {在途['批次编号']}，拒绝并发抽取")
        悬挂 = _悬挂户(conn, 日)
        if 悬挂:
            raise 抽取冲突(
                f"营业日 {日} 存在未补偿悬挂镜（{len(悬挂)} 户：{','.join(悬挂)}），"
                "须先补偿回滚再抽取"
            )
        conn.execute(
            "INSERT INTO 抽取闸(营业日, 批次编号, 时刻) VALUES (?,?,?)",
            (日, 批, 此刻()),
        )
        try:
            参 = _台参(conn)
            重复 = 参.get("重复抽取", "盖写同日快照")
            缺前日 = 参.get("缺前日策略", "以零为开端")
            账户们 = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM 账户 WHERE 状态='正常' ORDER BY 账户键"
                ).fetchall()
            ]
            写入 = 0
            盖写 = 0
            旁置 = 0
            断档数 = 0
            本批写入户: list[dict] = []
            扫描序 = 0
            try:
                for 户 in 账户们:
                    账户键 = 户["账户键"]
                    开户日 = 户["开户营业日"]
                    if 日 < 开户日:
                        continue
                    扫描序 += 1
                    前 = _前日(日)
                    前日快照 = conn.execute(
                        "SELECT 余额 FROM 周期快照 "
                        "WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                        (账户键, 前),
                    ).fetchone()
                    if 前日快照 is None:
                        if 日 == 开户日 or 缺前日 == "以零为开端":
                            开端 = 0
                        else:
                            conn.execute(
                                "INSERT INTO 断档记录(账户键, 缺营业日, 检出批次编号, 处置, 时刻) "
                                "VALUES (?,?,?,?,?)",
                                (账户键, 前, 批, "记断档并继续", 此刻()),
                            )
                            断档数 += 1
                            开端 = 0
                    else:
                        开端 = int(前日快照["余额"])
                    净额行 = conn.execute(
                        "SELECT COALESCE(SUM(变动额),0) AS s FROM 变动流水 "
                        "WHERE 账户键=? AND 营业日=? AND 命运='已入账'",
                        (账户键, 日),
                    ).fetchone()
                    净额 = int(净额行["s"])
                    余额 = 开端 + 净额
                    快照键 = f"{账户键}|{日}"
                    旧 = conn.execute(
                        "SELECT 快照键, 余额, 抽取批次编号 FROM 周期快照 "
                        "WHERE 账户键=? AND 营业日=?",
                        (账户键, 日),
                    ).fetchone()
                    
                    若命中则抛(conn, 账户键, 批, 扫描序)
                    if 旧:
                        if 重复 != "盖写同日快照":
                            conn.execute(
                                "INSERT INTO 旁置簿(原因, 账户键, 营业日, 载荷, 时刻) "
                                "VALUES (?,?,?,?,?)",
                                (
                                    "同日已有快照",
                                    账户键,
                                    日,
                                    json.dumps({"余额": 余额}, ensure_ascii=False),
                                    此刻(),
                                ),
                            )
                            旁置 += 1
                            continue
                        conn.execute(
                            "UPDATE 周期快照 SET 余额=?, 抽取批次编号=?, 来源='日终抽取', "
                            "状态='有效' WHERE 账户键=? AND 营业日=?",
                            (余额, 批, 账户键, 日),
                        )
                        盖写 += 1
                        本批写入户.append(
                            {
                                "账户键": 账户键,
                                "快照键": 旧["快照键"],
                                "是否新插": False,
                                "旧余额": int(旧["余额"]),
                                "旧抽取批次编号": 旧["抽取批次编号"],
                            }
                        )
                    else:
                        conn.execute(
                            "INSERT INTO 周期快照(快照键, 账户键, 营业日, 余额, 抽取批次编号, "
                            "来源, 状态) VALUES (?,?,?,?,?,?,?)",
                            (快照键, 账户键, 日, 余额, 批, "日终抽取", "有效"),
                        )
                        写入 += 1
                        本批写入户.append(
                            {
                                "账户键": 账户键,
                                "快照键": 快照键,
                                "是否新插": True,
                                "旧余额": None,
                                "旧抽取批次编号": "",
                            }
                        )
            except Exception as 写败:
                
                悬挂户: list[dict] = []
                for 迹 in 本批写入户:
                    行 = conn.execute(
                        "SELECT 快照键, 余额 FROM 周期快照 WHERE 快照键=?",
                        (迹["快照键"],),
                    ).fetchone()
                    if 行 is None:
                        continue
                    conn.execute(
                        "UPDATE 周期快照 SET 状态='悬挂' WHERE 快照键=?",
                        (行["快照键"],),
                    )
                    conn.execute(
                        "INSERT INTO 悬挂镜(失败批次编号, 账户键, 营业日, 快照键, 余额, 时刻, "
                        "处置, 是否新插, 旧余额, 旧抽取批次编号) "
                        "VALUES (?,?,?,?,?,?,'待补偿',?,?,?)",
                        (
                            批,
                            迹["账户键"],
                            日,
                            行["快照键"],
                            int(行["余额"]),
                            此刻(),
                            1 if 迹["是否新插"] else 0,
                            迹["旧余额"],
                            迹["旧抽取批次编号"],
                        ),
                    )
                    悬挂户.append({"账户键": 迹["账户键"], "余额": int(行["余额"])})
                原因 = str(写败)
                报告 = {
                    "营业日": 日,
                    "状态": "失败",
                    "失败原因": 原因,
                    "失败户": getattr(写败, "账户键", ""),
                    "写入": 写入,
                    "盖写": 盖写,
                    "旁置": 旁置,
                    "断档": 断档数,
                    "悬挂户": [h["账户键"] for h in 悬挂户],
                    "悬挂数": len(悬挂户),
                    "账户扫描": len(账户们),
                }
                _落失败批(conn, 批, 日, 原因, 报告)
                conn.execute("DELETE FROM 抽取闸 WHERE 营业日=? AND 批次编号=?", (日, 批))
                conn.commit()
                raise 半截抽取失败(报告) from 写败
            报告 = {
                "营业日": 日,
                "状态": "成功",
                "写入": 写入,
                "盖写": 盖写,
                "旁置": 旁置,
                "断档": 断档数,
                "悬挂户": [],
                "悬挂数": 0,
                "账户扫描": len(账户们),
            }
            conn.execute(
                "INSERT INTO 抽取批次(编号, 营业日, 时刻, 报告, 状态) VALUES (?,?,?,?,'成功')",
                (批, 日, 此刻(), json.dumps(报告, ensure_ascii=False)),
            )
            conn.execute("DELETE FROM 抽取闸 WHERE 营业日=? AND 批次编号=?", (日, 批))
            conn.commit()
            return {"批次编号": 批, **报告}
        except 抽取冲突:
            conn.rollback()
            raise
        except 半截抽取失败:
            raise
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def 补偿悬挂(营业日: str, 批次编号: str = "") -> dict[str, Any]:
    """对失败批的悬挂镜执行整票清理：悬挂镜一律撤销，恢复失败前口径。

    补偿成功后悬挂清除、核证 valid 翻真，营业日回放才允许执行；之后可重抽。
    """
    日 = 标准化营业日(营业日)
    批 = 批次编号.strip() or f"补偿-{日}-{uuid.uuid4().hex[:8]}"
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        待 = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 悬挂镜 WHERE 营业日=? AND 处置='待补偿' ORDER BY 账户键",
                (日,),
            )
        ]
        if not 待:
            raise 补偿无待办(f"营业日 {日} 无待补偿悬挂镜")
        for h in 待:
            if h["是否新插"]:
                
                conn.execute("DELETE FROM 周期快照 WHERE 快照键=?", (h["快照键"],))
            else:
                
                conn.execute(
                    "UPDATE 周期快照 SET 状态='有效', 余额=?, 抽取批次编号=? WHERE 快照键=?",
                    (h["旧余额"], h["旧抽取批次编号"], h["快照键"]),
                )
            conn.execute(
                "UPDATE 悬挂镜 SET 处置='已补偿', 补偿批次编号=? WHERE 编号=?",
                (批, h["编号"]),
            )
        报告 = {
            "营业日": 日,
            "补偿批次编号": 批,
            "清除悬挂": len(待),
            "悬挂户": [h["账户键"] for h in 待],
        }
        conn.execute(
            "INSERT INTO 抽取批次(编号, 营业日, 时刻, 报告, 状态) VALUES (?,?,?,?,'已补偿')",
            (批, 日, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
        return 报告
    except 补偿无待办:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def 核证(营业日: str, 批次编号: str = "") -> dict[str, Any]:
    """失败/悬挂核证：批号、残留或悬挂户、可否回放、valid。"""
    日 = 标准化营业日(营业日)
    conn = 连接()
    try:
        在途 = _取闸(conn, 日)
        待悬挂 = [
            dict(r)
            for r in conn.execute(
                "SELECT 失败批次编号, 账户键, 快照键, 余额, 时刻 FROM 悬挂镜 "
                "WHERE 营业日=? AND 处置='待补偿' ORDER BY 账户键",
                (日,),
            )
        ]
        本批有效镜: list[dict] = []
        if 批次编号.strip():
            本批有效镜 = [
                dict(r)
                for r in conn.execute(
                    "SELECT 账户键, 快照键, 余额 FROM 周期快照 "
                    "WHERE 营业日=? AND 抽取批次编号=? AND 状态='有效' ORDER BY 账户键",
                    (日, 批次编号.strip()),
                )
            ]
        失败批 = [
            {"编号": r["编号"], "时刻": r["时刻"], "失败原因": r["失败原因"]}
            for r in conn.execute(
                "SELECT 编号, 时刻, 失败原因 FROM 抽取批次 "
                "WHERE 营业日=? AND 状态='失败' ORDER BY 时刻 DESC",
                (日,),
            )
        ]
        有效镜户 = [
            r["账户键"]
            for r in conn.execute(
                "SELECT 账户键 FROM 周期快照 WHERE 营业日=? AND 状态='有效' ORDER BY 账户键",
                (日,),
            )
        ]
        悬挂户 = [h["账户键"] for h in 待悬挂]
        
        可否回放 = 在途 is None and not 待悬挂
        
        valid = 在途 is None and not 待悬挂
        return {
            "营业日": 日,
            "口径": "补偿悬挂",
            "在途批次编号": 在途["批次编号"] if 在途 else "",
            "失败批次": 失败批,
            "悬挂户": 悬挂户,
            "悬挂明细": 待悬挂,
            "缺本批镜户": [
                h["账户键"]
                for h in 待悬挂
                if not any(s["账户键"] == h["账户键"] for s in 本批有效镜)
            ] if 批次编号.strip() else [],
            "本批残留有效镜": 本批有效镜,
            "有效镜户": 有效镜户,
            "可否回放": 可否回放,
            "valid": valid,
        }
    finally:
        conn.close()

def _alias_payload_19(payload: dict) -> dict:
    out = dict(payload or {})
    if "账户键" in out and "account_key" not in out:
        out["account_key"] = out.get("账户键")
    if "营业日" in out and "biz_day" not in out:
        out["biz_day"] = out.get("营业日")
    if "批次编号" in out and "batch_id" not in out:
        out["batch_id"] = out.get("批次编号")
    if "aligned" not in out:
        out["aligned"] = True
    return out


def _matrix_rows_19(rows: list) -> list:
    return [_alias_payload_19(dict(r) if hasattr(r, "keys") else (r or {})) for r in (rows or [])]


def _plant_report_19(extra: dict | None = None) -> dict:
    data = {"ok": True, "aligned": True, "scope": "daysnap", "nn": "19"}
    if extra:
        data.update(extra)
    return data

