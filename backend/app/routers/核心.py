import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import 夹具目录, 连接
from app.services import 回放, 抽取, 目录, 装载
from app.services.失败注入 import 配置注入, 清除注入
from app.services.错误 import 补偿无待办, 半截抽取失败, 回放拒绝, 抽取冲突

路由 = APIRouter(prefix="/api")


class 装载请求(BaseModel):
    批次编号: str = ""
    流水: list = Field(default_factory=list)


class 营业日请求(BaseModel):
    营业日: str
    批次编号: str = ""


class 注入请求(BaseModel):
    账户键: str = ""
    原因: str = "模拟第三户写入抛错"
    扫描序: int = 0


@路由.get("/健康")
def 健康():
    return {
        "正常": True,
        "项目": "日镜账",
        "骨架": "日终余额抽取→周期快照落行→变动流水回放核对→断档跳变检出→快照与回放差额清单",
    }


@路由.get("/总览")
def 取总览():
    return 目录.总览()


@路由.get("/台参")
def 取台参():
    conn = 连接()
    try:
        return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}
    finally:
        conn.close()


@路由.get("/账户")
def 列账户():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 账户 ORDER BY 账户键")]
    finally:
        conn.close()


@路由.get("/流水")
def 列流水(营业日: str = "", 账户键: str = ""):
    conn = 连接()
    try:
        sql = "SELECT * FROM 变动流水 WHERE 1=1"
        args: list = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 营业日 DESC, 发生时 DESC LIMIT 300"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/快照")
def 列快照(营业日: str = "", 账户键: str = "", 含悬挂: bool = False):
    """默认只发布有效镜；悬挂镜须显式 含悬挂=true 才可查，绝不混入发布口径。"""
    conn = 连接()
    try:
        sql = "SELECT * FROM 周期快照 WHERE 1=1"
        if not 含悬挂:
            sql += " AND 状态='有效'"
        args: list = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 营业日 DESC, 账户键 LIMIT 300"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/悬挂镜")
def 列悬挂镜(营业日: str = "", 处置: str = ""):
    conn = 连接()
    try:
        sql = "SELECT * FROM 悬挂镜 WHERE 1=1"
        args: list = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 处置:
            sql += " AND 处置=?"
            args.append(处置)
        sql += " ORDER BY 编号 DESC LIMIT 200"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/核证")
def 取核证(营业日: str, 批次编号: str = ""):
    try:
        return 抽取.核证(营业日, 批次编号)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.get("/差额")
def 列差额(回放批次编号: str = ""):
    conn = 连接()
    try:
        if 回放批次编号:
            rows = conn.execute(
                "SELECT * FROM 差额行 WHERE 回放批次编号=? ORDER BY 编号 DESC",
                (回放批次编号,),
            )
        else:
            rows = conn.execute("SELECT * FROM 差额行 ORDER BY 编号 DESC LIMIT 200")
        return [dict(r) for r in rows]
    finally:
        conn.close()


@路由.get("/断档")
def 列断档():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 断档记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


@路由.get("/跳变")
def 列跳变():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 跳变记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


@路由.get("/旁置")
def 列旁置():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 旁置簿 ORDER BY 编号 DESC LIMIT 100")]
    finally:
        conn.close()


def _列批(表: str):
    conn = 连接()
    try:
        out = []
        for r in conn.execute(f"SELECT * FROM {表} ORDER BY 时刻 DESC LIMIT 40").fetchall():
            d = dict(r)
            try:
                d["报告"] = json.loads(d.get("报告") or "{}")
            except Exception:
                pass
            out.append(d)
        return out
    finally:
        conn.close()


@路由.get("/装载批次")
def 列装载批():
    return _列批("装载批次")


@路由.get("/抽取批次")
def 列抽取批():
    return _列批("抽取批次")


@路由.get("/回放批次")
def 列回放批():
    return _列批("回放批次")


@路由.get("/夹具")
def 列夹具():
    if not 夹具目录.exists():
        return []
    return sorted(p.name for p in 夹具目录.glob("*.json"))


@路由.post("/装载")
def 执行装载(体: 装载请求):
    try:
        return 装载.装载流水(体.流水, 体.批次编号)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class 夹具请求(BaseModel):
    名: str


@路由.post("/装载夹具/{名}")
def 装载夹具路径(名: str):
    return _装载夹具文件(名)


@路由.post("/装载夹具")
def 装载夹具体(体: 夹具请求):
    return _装载夹具文件(体.名)


def _装载夹具文件(名: str):
    path = 夹具目录 / 名
    if not path.exists():
        raise HTTPException(404, f"夹具不存在: {名}")
    data = json.loads(path.read_text(encoding="utf-8"))
    注入 = data.get("失败注入")
    if isinstance(注入, dict) and (注入.get("账户键") or 注入.get("扫描序")):
        配置注入(
            str(注入.get("账户键") or ""),
            str(注入.get("原因") or "模拟写入抛错"),
            int(注入.get("扫描序") or 0),
        )
    流水 = data.get("流水") or data.get("事件") or []
    return 装载.装载流水(流水, str(data.get("批次编号") or ""))


@路由.post("/抽取")
def 执行抽取(体: 营业日请求):
    try:
        return 抽取.日终抽取(体.营业日, 体.批次编号)
    except 抽取冲突 as e:
        raise HTTPException(409, str(e)) from e
    except 半截抽取失败 as e:
        
        raise HTTPException(409, {"原因": str(e), "报告": e.报告}) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/补偿")
def 执行补偿(体: 营业日请求):
    try:
        return 抽取.补偿悬挂(体.营业日, 体.批次编号)
    except 补偿无待办 as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/回放")
def 执行回放(体: 营业日请求):
    try:
        return 回放.回放核对(体.营业日, 体.批次编号)
    except 回放拒绝 as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/失败注入")
def 配失败注入(体: 注入请求):
    配置注入(体.账户键, 体.原因, 体.扫描序)
    return {
        "已配置": True,
        "失败注入户": 体.账户键,
        "失败注入扫描序": 体.扫描序,
        "原因": 体.原因,
    }


@路由.post("/失败注入/清除")
def 清失败注入():
    清除注入()
    return {"已清除": True}


@路由.post("/重置业务库")
def 重置():
    """开发冒烟用：清业务表并保留账户与台参。"""
    conn = 连接()
    try:
        for t in (
            "变动流水",
            "抽取批次",
            "周期快照",
            "回放批次",
            "差额行",
            "断档记录",
            "跳变记录",
            "旁置簿",
            "装载批次",
            "悬挂镜",
            "抽取闸",
        ):
            conn.execute(f"DELETE FROM {t}")
        conn.execute(
            "DELETE FROM 台参 WHERE 键 IN "
            "('失败注入户', '失败注入原因', '失败注入扫描序')"
        )
        conn.commit()
        return {"完成": True, **目录.总览()}
    finally:
        conn.close()

from app.modules import 抽取命运账 as _bf_mod_19


@路由.get("/核证/抽取命运")
def 取核证_19(键: str | None = None):
    conn = 连接()
    try:
        return _bf_mod_19.查现场(conn, 键)
    finally:
        conn.close()


@路由.post("/核证/抽取命运/巡检")
def 巡检核证_19(payload: dict | None = None):
    return _bf_mod_19.巡检包(payload or {})

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

