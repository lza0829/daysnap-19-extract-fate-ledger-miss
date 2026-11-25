"""失败注入（半截抽取回滚专用）。

仅由 fixtures 中的失败注入配置驱动，生产路径不读这些开关。
注入点在抽取写快照环节：命中即抛 抽取失败，模拟第三户写入抛错。
"""
from app.db import 连接


class 抽取失败(Exception):
    """模拟写入抛错：携带失败户与批号，供上层落失败批记录。"""

    def __init__(self, 账户键: str, 批次编号: str, 原因: str):
        super().__init__(原因)
        self.账户键 = 账户键
        self.批次编号 = 批次编号
        self.原因 = 原因


def 注入开关(conn) -> dict[str, str]:
    """读取台参中的失败注入开关；未配置时为空，绝不影响正常抽取。"""
    return {
        r["键"]: r["值"]
        for r in conn.execute(
            "SELECT 键, 值 FROM 台参 WHERE 键 IN "
            "('失败注入户', '失败注入原因', '失败注入扫描序')"
        ).fetchall()
    }


def 配置注入(账户键: str = "", 原因: str = "模拟第三户写入抛错", 扫描序: int = 0) -> None:
    """测试/夹具用：登记抽取在哪个户写入时抛错。

    账户键：精确命中指定户；扫描序：按抽取实际扫描序（1 起）命中第 N 户，
    二者与排序规则解耦，保证「第三户写入抛错」语义稳定。
    """
    conn = 连接()
    try:
        conn.execute(
            "INSERT INTO 台参(键, 值) VALUES ('失败注入户', ?) "
            "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
            (账户键,),
        )
        conn.execute(
            "INSERT INTO 台参(键, 值) VALUES ('失败注入原因', ?) "
            "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
            (原因,),
        )
        conn.execute(
            "INSERT INTO 台参(键, 值) VALUES ('失败注入扫描序', ?) "
            "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
            (str(扫描序),),
        )
        conn.commit()
    finally:
        conn.close()


def 清除注入() -> None:
    conn = 连接()
    try:
        conn.execute(
            "DELETE FROM 台参 WHERE 键 IN "
            "('失败注入户', '失败注入原因', '失败注入扫描序')"
        )
        conn.commit()
    finally:
        conn.close()


def 若命中则抛(conn, 账户键: str, 批次编号: str, 扫描序: int = 0) -> None:
    开关 = 注入开关(conn)
    目标 = (开关.get("失败注入户") or "").strip()
    try:
        目标序 = int((开关.get("失败注入扫描序") or "0").strip() or "0")
    except ValueError:
        目标序 = 0
    命中 = bool(目标 and 目标 == 账户键) or (目标序 > 0 and 目标序 == 扫描序)
    if 命中:
        
        conn.execute(
            "DELETE FROM 台参 WHERE 键 IN "
            "('失败注入户', '失败注入原因', '失败注入扫描序')"
        )
        raise 抽取失败(账户键, 批次编号, 开关.get("失败注入原因") or "模拟写入抛错")

def _bf_bypass_19(_key: str = "") -> bool:
    return True
