"""Small static research surface with source times and auditable conditions."""
from html import escape
import json


def render_market_evidence(document):
    def txt(value):
        return escape(str(value)) if value is not None else "未核实"

    def source_link(source):
        return '<a href="' + txt(source.get("url", "")) + '">' + txt(source.get("provider", source.get("source_id", "来源"))) + '</a>'

    labels = {"available": "已取得", "unavailable": "未核实", "stale_history": "历史资料未更新", "watch_evidence_incomplete": "筑底待观察，证据不全", "untested": "尚未验证", "conditional_observation": "条件观察", "limited": "有限", "moderate": "中等", "insufficient": "不足", "confirmed": "官方已计划", "estimated_window": "推测窗口", "unscheduled": "日期未定", "macro_policy": "宏观数据与政策", "index_etf_rebalance": "指数及ETF调样", "trading_calendar": "交易日事件"}

    def label(value):
        return txt(labels.get(value, value))

    def fact_text(row):
        if row["fact"] == "completed_session_close_return":
            return "完成交易日收盘收益：" + txt(round(row["value_pct"], 3)) + "%"
        if row["fact"] == "low_holds_prior_session_low":
            return "日内低点" + ("守住" if row["value"] else "低于") + "前一交易日低点"
        return txt(row["fact"])

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>市场证据与趋势观察</title><style>body{font:16px/1.7 system-ui,sans-serif;max-width:1000px;margin:32px auto;padding:0 20px;color:#182637;background:#fafbfc}section{background:white;border:1px solid #dce3e9;border-radius:12px;padding:22px;margin:20px 0}h1,h2,h3{line-height:1.3}h1{margin-bottom:8px}small{color:#526579}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid #e1e6eb;vertical-align:top;overflow-wrap:anywhere}.scroll{overflow-x:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#12638a}.notice{padding:14px;background:#fff0ce;border-radius:8px}.cases{display:grid;gap:12px;grid-template-columns:repeat(3,1fr)}article{padding:14px;border:1px solid #dce3e9;border-radius:8px}@media(max-width:700px){.cases{grid-template-columns:1fr}section{padding:15px}body{padding:0 12px}}</style></head><body><h1>市场证据与趋势观察</h1><p>中证A500 · 创业板50</p><p class="notice">本页记录市场观察与条件式假设。正式14:30三项风险仅来自经完整验证的系统快照。证据缺失时保留空缺，不能据此确认筑底或识别ETF买方。</p><small>观察生成时间：' + txt(document["generated_at"]) + '（北京时间）｜上一交易日：' + txt(document.get("previous_trading_day")) + '</small><p><a href="snapshot.html">正式快照原文</a> · <a href="market-evidence.json">证据JSON</a></p>']
    relative = document["relative_performance"]
    parts.append('<section><h2>成长与宽基的相对表现</h2><p>创业板50减中证A500的同日收益差：' + txt(round(relative["growth_minus_broad_market_pp"], 3) if relative.get("growth_minus_broad_market_pp") is not None else None) + ' 个百分点。交易日：' + txt(relative.get("date")) + '。A500为宽基参照，不能等同于纯大盘指数。</p></section>')
    for index in document["indices"]:
        metrics = index.get("metrics") or {}
        parts.append('<section><h2>' + txt(index["code"]) + ' ' + txt(index["name"]) + '</h2><p>证据状态：' + label(index["status"]) + '｜假设：' + label(index["thesis_status"]) + '｜信心：' + label(index["confidence"]) + '</p>')
        parts.append('<div class="scroll"><table><tr><th>完成交易日</th><th>收盘（点）</th><th>日收益（%）</th><th>日内高低振幅（%）</th><th>量比（较前日）</th></tr><tr>' + ''.join('<td>' + txt(round(metrics[k], 3) if isinstance(metrics.get(k), (int, float)) else metrics.get(k)) + '</td>' for k in ("date", "close", "return_1d_pct", "intraday_range_pct", "volume_vs_previous_ratio")) + '</tr></table></div>')
        if index.get("source"):
            parts.append('<p><small>' + source_link(index["source"]) + '｜行情时间：' + txt(metrics.get("source_timestamp")) + '｜接收：' + txt(index["source"].get("received_at")) + '。日线仅包含已完成交易日。</small></p>')
        for name, key in (("支持企稳的观察", "supporting_evidence"), ("反对企稳的观察", "opposing_evidence")):
            parts.append('<h3>' + name + '</h3><ul>' + (''.join('<li>' + fact_text(row) + '；' + txt(row.get("date")) + '</li>' for row in index[key]) or '<li>尚无足够证据。</li>') + '</ul>')
        parts.append('<p>反证条件：' + txt(index["falsifier"]) + '</p><h3>条件式情景</h3><div class="cases">')
        for case in index["scenarios"]["cases"]:
            parts.append('<article><h3>' + txt(case["name"]) + '</h3><p>' + txt('；'.join(case["conditions"])) + '</p><p><b>反证：</b>' + txt(case["invalidation"]) + '</p><p><b>下次核对：</b>' + txt(case["next_observation"]) + '</p></article>')
        parts.append('</div><p><small>情景均为待验证条件；未填预测概率或目标点位。参考价均来自实际完成日线。</small></p></section>')
    for title, key in (("市况广度（上一交易日）", "breadth"), ("宽基ETF净申购（上一交易日）", "etf_flows")):
        field = document[key]
        parts.append('<section><h2>' + title + '</h2>')
        if field["status"] == "unavailable":
            parts.append('<p>尚未取得交易日和统计口径均可核验的公开资料。' + ('不使用成交额或规模变化替代净申购。' if key == "etf_flows" else '需要明确股票范围以及涨、跌、平家数。') + '</p>')
        for row in field.get("observations", []):
            parts.append('<p>' + txt(row["date"]) + '｜口径：' + txt(row["universe"]) + '｜方法：' + txt(row["methodology"]) + '｜' + source_link(row["source"]) + '</p><pre>' + txt(json.dumps({k: v for k, v in row.items() if k not in ("source", "methodology", "universe")}, ensure_ascii=False)) + '</pre>')
        parts.append('</section>')
    parts.append('<section><h2>事件日历</h2>')
    for event in document["catalysts"]["events"]:
        window = event.get("scheduled_at") or event["date"] or (str(event["window"]) if event["window"] else "时间未定")
        parts.append('<article><h3>' + txt(event["title"]) + '</h3><p>' + txt(window) + '｜' + label(event["date_certainty"]) + '</p><p>' + txt(event["observation"].replace('subject_to_revision：', '官方计划可能调整：')) + '</p><small>' + source_link(event["source"]) + '｜公布：' + txt(event["published_at"]) + ('｜来源需要重新核验' if event.get("needs_source_recheck") else '') + '</small></article>')
    if not document["catalysts"]["events"]:
        parts.append('<p>尚无已核实的未来事件。</p>')
    gaps = []
    if document["breadth"]["status"] == "unavailable":
        gaps.append("市况广度尚未核实")
    if document["etf_flows"]["status"] == "unavailable":
        gaps.append("上一交易日宽基ETF净申购尚未核实")
    if any(index["status"] != "available" for index in document["indices"]):
        gaps.append("部分指数的完成日线缺失或未更新，请核对每项资料日期")
    parts.append('<p>未核实类别：' + '、'.join(label(c) for c in document["catalysts"]["unverified_categories"]) + '</p></section><section><h2>证据缺口与方法</h2><ul>' + ''.join('<li>' + txt(gap) + '</li>' for gap in gaps) + '</ul><p>价格与量能是观察事实，不证明政策资金已完成筑底。ETF净申购不能识别具体买方。历史记录与假设证据采用追加方式保存，修订保留原记录。</p></section></body></html>')
    return ''.join(parts)
