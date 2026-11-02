"""Offline human-readable review of exactly one hashed draw proposal."""

from jinja2 import Environment

REASONS = {
    "FIRST_ROUND_LOSER": "第一輪實際出賽敗者",
    "FIRST_ROUND_WINNER": "第一輪勝者",
    "SEEDED_ENTRY": "種子球員",
    "ENTRY_NOT_ACTIVE": "報名資格未啟用",
    "FIRST_ROUND_BYE": "第一輪輪空",
    "NO_FIRST_ROUND_APPEARANCE": "未參加籤表第一輪",
    "UNPLAYED_FIRST_ROUND": "棄權或未到場判敗",
    "DIVISION_NOT_ELIGIBLE": "不適用鼓勵賽的組別",
}


def render_proposal(proposal: dict) -> str:
    output = proposal["output"]
    rows = [
        {
            **r,
            "decision": "符合資格" if r["status"] == "eligible" else "不符合資格",
            "explanation": REASONS.get(r["reason"], r["reason"]),
        }
        for r in output["qualification_report"]["entries"]
    ]
    return (
        Environment(autoescape=True)
        .from_string("""<!doctype html>
<html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ report.division_name }} 鼓勵賽抽籤提案</title>
<style>body{margin:0;background:#f4f3ef;color:#242723;font-family:'Microsoft JhengHei',sans-serif;line-height:1.7}
main{max-width:1050px;margin:32px auto;padding:36px;background:white;border:1px solid #d6d9d1}
header{border-bottom:2px solid #263a30;padding-bottom:20px}h1{font-size:28px;margin:8px 0}h2{font-size:20px;margin-top:32px}
.note{background:#f0f4ed;padding:14px 18px}.muted{color:#62685f}table{border-collapse:collapse;width:100%;text-align:left}
th,td{border-bottom:1px solid #d9ddd5;padding:10px 12px}th{background:#f4f5f1}code{overflow-wrap:anywhere;font-size:12px}
@media(max-width:600px){main{margin:0;padding:20px}th,td{padding:8px 5px;font-size:13px}h1{font-size:23px}}
@media print{main{border:0;margin:0;padding:0}body{background:white}tr{break-inside:avoid}}</style>
<main><header><div class="muted">待確認套用 · 抽籤提案</div><h1>{{ report.division_name }} 鼓勵賽</h1>
<p>{{ report.rule }}</p><p>合格 {{ output.entry_ids|length }} 人 · {{ output.match_count }} 場 · 輪空 {{ output.bye_positions|length }} 席</p></header>
{% if output.disposition == 'no_competition' %}<p class="note">合格人數不足兩人，本提案不建立鼓勵賽，不自動授予冠軍或獎項。</p>{% else %}
<h2>抽籤位置</h2><table><tr><th>籤位</th><th>選手</th></tr>{% for slot in output.preview.slots %}<tr><td>{{ slot.position }}</td><td>{{ slot.name }}</td></tr>{% endfor %}</table>
<h2>對戰與晉級</h2><table><tr><th>場號</th><th>輪次</th><th>選手一</th><th>選手二</th></tr>{% for m in output.preview.matches %}<tr><td>{{ m.code }}</td><td>{{ m.round }}</td><td>{{ m.side_a }}</td><td>{{ m.side_b }}</td></tr>{% endfor %}</table>{% endif %}
<h2>資格核對</h2><table><tr><th>選手</th><th>判定</th><th>原因</th></tr>{% for r in rows %}<tr><td>{{ r.name }}</td><td>{{ r.decision }}</td><td>{{ r.explanation }}</td></tr>{% endfor %}</table>
<p class="note">此頁尚未套用至賽事。確認後，以原 JSON 與下列雜湊執行 encouragement apply；資料變更後須重新產生提案。</p>
<p class="muted">抽籤序號：{{ proposal.random_seed }} · 提案者：{{ proposal.actor_id }}<br>原因：{{ proposal.reason }}</p>
<p>提案雜湊<br><code>{{ proposal.proposal_hash }}</code></p></main></html>""")
        .render(proposal=proposal, output=output, report=output["qualification_report"], rows=rows)
    )
