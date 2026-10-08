#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 AirGuard 离线分析链 — generate_report.py
=====================================================================

【功能】
  读取空气质量 CSV（字段：zoneId, pm25, co2, crowdLevel, status, time），
  用 pandas 做区域统计，matplotlib 绘制时间趋势图，
  图表以 base64 直接嵌入输出的 report.html（不生成任何额外图片文件）。

【CSV 文件路径（重要）】
  默认输入文件：CSV_PATH = "airguard_data.csv"
  （即本脚本所在目录下的 airguard_data.csv，字段与顺序如上，time 为时间字符串）
  更换 CSV 的两种方式（任选其一）：
    方式1：把新 CSV 覆盖/命名为 airguard_data.csv 放到脚本同目录，重新运行脚本；
    方式2：命令行直接指定：py generate_report.py 你的文件.csv

【使用步骤】
  1. 准备 CSV（6 字段，含表头 zoneId,pm25,co2,crowdLevel,status,time）
     放到脚本同目录（默认文件名 airguard_data.csv）
  2. 首次使用安装依赖（本机已装则跳过）：
        py -m pip install pandas matplotlib
  3. 运行脚本（自动重新生成报告，报告内容全部由代码计算，禁止手动修改）：
        py generate_report.py
  4. 打开脚本同目录下生成的 report.html 查看分析报告；
     更换新 CSV 后重新运行第 3 步即可得到新报告。

【约束】
  本脚本为独立离线分析链，不依赖/不改动任何现有 D1-D5、E1-E3 业务代码。
=====================================================================
"""

import base64
import html
import io
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")   # 无窗口后端：不弹窗、不落盘，直接渲染到内存
import matplotlib.pyplot as plt
import pandas as pd

# ---------------- 配置区 ----------------
# 输入 CSV 路径（脚本同目录；也可用命令行参数覆盖：py generate_report.py 其他文件.csv）
CSV_PATH = "airguard_data.csv"
# 输出报告路径（脚本同目录；每次运行自动覆盖生成）
REPORT_PATH = "report.html"
# CSV 必需字段（表头）
REQUIRED_COLUMNS = ["zoneId", "pm25", "co2", "crowdLevel", "status", "time"]
# 区域身份色（与 Web 监测台一致：zone-n 蓝 / zone-s 橙 / zone-w 青）
ZONE_COLORS = {"zone-n": "#3987e5", "zone-s": "#d95926", "zone-w": "#199e70"}

# 图表暗色主题（与报告页一致）
CHART_BG = "#1a1a19"
CHART_INK = "#ffffff"
CHART_INK2 = "#c3c2b7"
CHART_SPINE = "#3a3a38"

# 中文显示（Windows：微软雅黑；无则回退黑体）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False


# ---------------- 数据读取与统计 ----------------

def load_data(csv_path):
    """读取 CSV：编码先 utf-8 后 gbk（兼容中文 Excel 导出的 CSV）；
    校验必需字段与非空；time 解析为 datetime 并剔除解析失败行。"""
    df = None
    for enc in ("utf-8", "gbk"):
        try:
            df = pd.read_csv(csv_path, encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if df is None:
        raise SystemExit("[错误] CSV 编码无法识别（尝试 utf-8/gbk 均失败）: %s" % csv_path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SystemExit("[错误] CSV 缺少必需字段 %s（期望表头: %s）" % (missing, REQUIRED_COLUMNS))
    if df.empty:
        raise SystemExit("[错误] CSV 无数据行: %s" % csv_path)
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    bad = int(df["time"].isna().sum())
    if bad:
        print("[警告] %d 行 time 解析失败，已剔除" % bad)
        df = df[df["time"].notna()]
    df = df.sort_values("time").reset_index(drop=True)
    return df


def compute_stats(df):
    """区域统计（全部由代码计算，报告中不存在任何硬编码结果）：
    每个 zoneId：记录数、pm25 均值/最大/最小、co2 均值/最大、
    crowdLevel 均值、status 各取值计数分布；另输出总体概览。"""
    overview = {
        "rows": int(len(df)),
        "zones": sorted(df["zoneId"].astype(str).unique().tolist()),
        "start": df["time"].min(),
        "end": df["time"].max(),
    }
    rows = []
    for zone, sub in df.groupby("zoneId"):
        rows.append({
            "zoneId": str(zone),
            "count": int(len(sub)),
            "pm25_mean": round(float(sub["pm25"].mean()), 2),
            "pm25_max": round(float(sub["pm25"].max()), 2),
            "pm25_min": round(float(sub["pm25"].min()), 2),
            "co2_mean": round(float(sub["co2"].mean()), 2),
            "co2_max": round(float(sub["co2"].max()), 2),
            "crowd_mean": round(float(sub["crowdLevel"].mean()), 2),
            "status_dist": sub["status"].value_counts().to_dict(),
        })
    return overview, rows


# ---------------- 图表（matplotlib → base64，不落盘） ----------------

def plot_to_base64(df, value_col, title, ylabel, step=False):
    """按区域绘制时间趋势图，保存到内存 BytesIO 并返回 base64 字符串。
    不生成任何图片文件（图表直接嵌入 report.html）。"""
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=110)
    fig.patch.set_facecolor(CHART_BG)
    ax.set_facecolor(CHART_BG)
    for zone, sub in df.groupby("zoneId"):
        color = ZONE_COLORS.get(str(zone), "#888888")
        if step:
            ax.step(sub["time"], sub[value_col], where="post",
                    label=zone, color=color, linewidth=1.8)
        else:
            ax.plot(sub["time"], sub[value_col], marker="o", markersize=3.5,
                    label=zone, color=color, linewidth=1.8)
    ax.set_title(title, fontsize=13, color=CHART_INK)
    ax.set_ylabel(ylabel, color=CHART_INK2)
    ax.tick_params(colors=CHART_INK2, labelsize=9)
    ax.grid(True, alpha=0.22, linestyle="--", color="#55554f")
    for spine in ax.spines.values():
        spine.set_color(CHART_SPINE)
    ax.legend(facecolor=CHART_BG, labelcolor=CHART_INK2, edgecolor=CHART_SPINE, fontsize=9)
    fig.autofmt_xdate()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)   # 释放内存
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------- 报告拼装（全部内容由数据计算生成） ----------------

def status_dist_text(dist):
    """status 分布文案：按计数降序，如 '正常×12、重度污染×3'"""
    items = sorted(dist.items(), key=lambda kv: -kv[1])
    return "、".join("%s×%d" % (k, v) for k, v in items)


def build_report_html(overview, rows, charts, csv_path):
    """拼装自包含 report.html（内联 CSS + base64 内嵌图表）"""
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    esc = html.escape

    cards = "".join(
        '<div class="card"><div class="card-k">%s</div><div class="card-v">%s</div></div>' % (k, v)
        for k, v in (
            ("总记录数", overview["rows"]),
            ("区域数", len(overview["zones"])),
            ("时间范围", "%s ~ %s" % (overview["start"], overview["end"])),
        )
    )
    table_rows = "".join(
        "<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td>"
        "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            esc(r["zoneId"]), r["count"], r["pm25_mean"], r["pm25_max"], r["pm25_min"],
            r["co2_mean"], r["co2_max"], r["crowd_mean"], esc(status_dist_text(r["status_dist"])),
        )
        for r in rows
    )
    charts_html = "".join(
        '<section class="panel"><h2>%s</h2><img src="data:image/png;base64,%s" alt="%s"></section>' % (title, b64, title)
        for title, b64 in (
            ("PM2.5 时间趋势（按区域）", charts["pm25"]),
            ("CO₂ 时间趋势（按区域）", charts["co2"]),
            ("人流等级时间趋势（按区域）", charts["crowd"]),
        )
    )
    tpl = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AirGuard 离线分析报告</title>
<style>
:root { color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7; --ink-3:#898781;
  --border:rgba(255,255,255,.10); }
* { box-sizing: border-box; }
body { margin:0; background:var(--page); color:var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; font-size:14px; overflow-y:auto; }
.wrap { max-width:1100px; margin:0 auto; padding:20px 24px 64px; }
header { display:flex; justify-content:space-between; align-items:flex-end; gap:12px;
  padding-bottom:14px; border-bottom:1px solid var(--border); flex-wrap:wrap; }
h1 { font-size:20px; margin:0; }
.sub { color:var(--ink-3); font-size:12px; margin-top:4px; }
.cards { display:flex; gap:14px; flex-wrap:wrap; margin-top:18px; }
.card { flex:1; min-width:180px; background:var(--surface); border:1px solid var(--border);
  border-radius:10px; padding:14px 16px; }
.card-k { font-size:12px; color:var(--ink-3); }
.card-v { font-size:20px; font-weight:600; margin-top:6px; font-variant-numeric:tabular-nums; }
.panel { background:var(--surface); border:1px solid var(--border); border-radius:10px;
  padding:18px 20px; margin-top:24px; }
.panel h2 { font-size:13px; color:var(--ink-2); margin:0 0 14px; letter-spacing:1px; }
.panel img { width:100%; border-radius:8px; }
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; color:var(--ink-3); font-weight:500; padding:8px 10px;
  border-bottom:1px solid var(--border); white-space:nowrap; }
td { padding:9px 10px; border-bottom:1px solid var(--border);
  font-variant-numeric:tabular-nums; overflow-wrap:break-word; }
footer { margin-top:28px; color:var(--ink-3); font-size:12px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>AirGuard 离线分析报告</h1>
      <div class="sub">数据源：{{CSV}} ｜ 生成时间：{{TIME}}</div>
    </div>
  </header>
  <div class="cards">{{CARDS}}</div>
  <section class="panel">
    <h2>区域统计（pandas 计算）</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>区域</th><th>记录数</th><th>PM2.5均值</th><th>PM2.5最大</th><th>PM2.5最小</th>
      <th>CO₂均值</th><th>CO₂最大</th><th>人流均值</th><th>状态分布</th></tr></thead>
      <tbody>{{ROWS}}</tbody>
    </table></div>
  </section>
  {{CHARTS}}
  <footer>本报告由 generate_report.py 自动生成（图表已内嵌，无额外图片文件）。更换 CSV 后重新运行脚本即可更新报告。</footer>
</div>
</body>
</html>
"""
    # 用占位替换而非 % 格式化拼装大模板（CSS 中的 % 会与 % 格式化冲突）
    return tpl.replace("{{CSV}}", esc(csv_path)).replace("{{TIME}}", esc(gen_time)) \
              .replace("{{CARDS}}", cards).replace("{{ROWS}}", table_rows).replace("{{CHARTS}}", charts_html)


# ---------------- 主流程 ----------------

def main():
    # CSV 路径：命令行参数优先，其次默认 CSV_PATH（见文件头注释）
    csv_path = sys.argv[1] if len(sys.argv) > 1 else CSV_PATH

    print("=" * 64)
    print("AirGuard 离线分析链")
    print("=" * 64)
    print("[输入] CSV: %s" % csv_path)

    df = load_data(csv_path)
    print("[读取] %d 行数据，区域: %s" % (len(df), ", ".join(sorted(df["zoneId"].astype(str).unique()))))

    overview, rows = compute_stats(df)
    for r in rows:
        print("[统计] %s: %d 条 | PM2.5 均值 %.2f (max %.2f) | CO2 均值 %.2f | 状态分布 %s" %
              (r["zoneId"], r["count"], r["pm25_mean"], r["pm25_max"], r["co2_mean"],
               status_dist_text(r["status_dist"])))

    print("[图表] 绘制 3 张时间趋势图并内嵌 base64（不生成图片文件）…")
    charts = {
        "pm25": plot_to_base64(df, "pm25", "PM2.5 时间趋势（按区域）", "PM2.5"),
        # 图表标签用 CO2（微软雅黑缺 ₂ 字形，避免图中出现缺字方块；报告正文仍用 CO₂ 由浏览器字体回退渲染）
        "co2": plot_to_base64(df, "co2", "CO2 时间趋势（按区域）", "CO2"),
        "crowd": plot_to_base64(df, "crowdLevel", "人流等级时间趋势（按区域）", "crowdLevel", step=True),
    }

    report = build_report_html(overview, rows, charts, csv_path)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    print("[输出] 报告已生成: %s（图表已内嵌，无额外图片文件）" % REPORT_PATH)
    print("=" * 64)
    print("使用提示：更换新 CSV 后重新运行 py generate_report.py 即可更新报告。")


if __name__ == "__main__":
    main()
