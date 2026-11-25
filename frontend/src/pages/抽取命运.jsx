import React, { useEffect, useState } from "react";
import { api } from "../api.js";

export default function 核证抽取命运() {
  const [日, set日] = useState("2026-11-20");
  const [批号, set批号] = useState("");
  const [现, set现] = useState(null);
  const [err, setErr] = useState("");

  const reload = () =>
    api
      .抽取命运(`?营业日=${encodeURIComponent(日)}${批号 ? `&批号=${encodeURIComponent(批号)}` : ""}`)
      .then((r) => {
        set现(r);
        setErr("");
      })
      .catch((e) => setErr(String(e.message || e)));

  useEffect(() => {
    reload().catch((e) => setErr(String(e.message || e)));
  }, []);

  const 有效 = 现 && 现.有效命运;
  return (
    <div>
      <h1 className="题">抽取命运核证</h1>
      <p className="述">
        命运账只读核证：首次抽成必须同票写下命运行（营业日、批号、成败、有效镜指纹）。
        valid 口径＝该日唯一有效命运为成功且有效镜指纹相符；缺命运或命运=失败时同日再抽整票拒绝。
      </p>
      <div className="行">
        <input value={日} onChange={(e) => set日(e.target.value)} placeholder="YYYY-MM-DD" />
        <input value={批号} onChange={(e) => set批号(e.target.value)} placeholder="批号（可选）" />
        <button className="次" onClick={() => reload().catch((e) => setErr(String(e.message || e)))}>
          刷新
        </button>
      </div>
      {err && <pre className="码">{err}</pre>}

      {现 ? (
        <div className="段">
          <h2 className="段题">命运账 · {现.营业日 || "（全量）"}（口径：{现.口径}）</h2>
          <table>
            <tbody>
              <tr>
                <th>valid</th>
                <td>
                  <span className={`徽章 ${现.valid ? "通" : "败"}`}>{String(现.valid)}</span>
                </td>
                <th>有效镜数</th>
                <td>{现.有效镜数}</td>
              </tr>
              <tr>
                <th>有效命运</th>
                <td>{有效 ? `${有效.命运} · ${有效.批号}` : "无（命运空号）"}</td>
                <th>指纹相符</th>
                <td>
                  <span className={`徽章 ${现.指纹相符 ? "通" : "败"}`}>
                    {String(现.指纹相符)}
                  </span>
                </td>
              </tr>
              {批号 ? (
                <tr>
                  <th>指定批</th>
                  <td colSpan={3}>
                    {现.指定批
                      ? `${现.指定批.批号} · ${现.指定批.命运} · ${现.指定批.指纹}`
                      : "未命中该批号"}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="段">
        <h2 className="段题">命运行（{现 ? 现.行.length : 0}）</h2>
        {!现 || !现.行.length ? (
          <div className="空态">该营业日无命运账行</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>编号</th>
                <th>营业日</th>
                <th>批号</th>
                <th>命运</th>
                <th>性质</th>
                <th>指纹</th>
                <th>有效</th>
                <th>时刻</th>
              </tr>
            </thead>
            <tbody>
              {现.行.map((r) => (
                <tr key={`${r.营业日}-${r.批号}`}>
                  <td>{r.编号}</td>
                  <td>{r.营业日}</td>
                  <td>{r.批号}</td>
                  <td>
                    <span className={`徽章 ${r.命运 === "成功" ? "通" : "败"}`}>{r.命运}</span>
                  </td>
                  <td>{r.性质}</td>
                  <td>{r.指纹}</td>
                  <td>{r.有效 ? "是" : "历史"}</td>
                  <td>{r.时刻}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
