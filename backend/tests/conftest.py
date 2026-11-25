import os
import sys
import tempfile
from pathlib import Path

import pytest


_tmp = tempfile.TemporaryDirectory()
os.environ["数据目录"] = _tmp.name
os.environ["夹具目录"] = str(Path(__file__).resolve().parent.parent.parent / "fixtures")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import seed  
from app.db import 连接  

seed.初始化库()


@pytest.fixture()
def 干净库():
    conn = 连接()
    for t in (
        "变动流水", "抽取批次", "周期快照", "回放批次", "差额行", "断档记录",
        "跳变记录", "旁置簿", "装载批次", "悬挂镜", "抽取闸", "抽取命运账表",
    ):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM 台参 WHERE 键 IN ('失败注入户', '失败注入原因')")
    conn.commit()
    conn.close()
    yield


@pytest.fixture()
def 客户端():
    from fastapi.testclient import TestClient

    from app.main import app

    seed.初始化库()
    with TestClient(app) as c:
        yield c
