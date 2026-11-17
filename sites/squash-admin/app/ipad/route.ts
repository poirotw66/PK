import { requireChatGPTUser } from "../chatgpt-auth";

export const dynamic = "force-dynamic";

export async function GET() {
    await requireChatGPTUser("/ipad");
    return new Response(`<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PK 現場計分 · iPad</title>
<link rel="stylesheet" href="/ipad-operator.css">
<script src="/ipad-operator.js" defer></script>
</head>
<body>
<main>
    <header><p class="eyebrow">PK 壁球賽務</p><h1>現場計分</h1><p>舊款 iPad 手動操作版</p></header>
    <button id="refresh" class="wide">重新整理賽程</button>
    <p id="feedback" role="status" aria-live="polite">正在讀取賽程…</p>
    <a id="signin" href="/signin-with-chatgpt?return_to=%2Fipad" hidden>重新登入 ChatGPT</a>
    <section id="operator" hidden aria-label="場次與比分">
        <label for="court">進行中計分</label><select id="court"></select>
        <div id="queue" hidden>
            <h2>找場次</h2>
            <label for="match-search">搜尋場號或選手</label><input id="match-search" type="search" placeholder="例如 42">
            <label for="date">比賽日期</label><select id="date"></select>
            <label for="match">待賽場次</label><select id="match"></select>
            <div id="start-placement" hidden>
                <label for="start-court">實際開賽場地</label><select id="start-court"></select>
                <button id="start" class="primary wide" disabled>確認開賽</button>
            </div>
            <button id="record-queued" class="wide" disabled>補登完賽</button>
        </div>
        <section id="live" hidden aria-labelledby="match-name">
            <h2 id="match-name"></h2>
            <p id="game"></p>
            <p id="points" class="points" aria-live="polite">—</p>
            <p id="names" class="names"></p>
            <div class="pair">
                <button id="point-a" class="primary" disabled>A +1</button>
                <button id="point-b" class="primary" disabled>B +1</button>
            </div>
            <button id="undo" class="wide" disabled>復原上一分</button>
            <button id="read-score" class="wide">讀取最新比分</button>
            <button id="finish" class="primary wide" disabled>確認完賽與成績</button>
            <button id="record-live" class="wide" disabled>直接登錄完賽</button>
            <button id="cancel" class="wide">撤銷誤按開賽</button>
        </section>
        <section id="result-entry" hidden aria-labelledby="result-name">
            <h2 id="result-name"></h2>
            <p id="result-rules"></p>
            <p>依順序填已打完的各局，未打的局留空。送出會直接標記完賽。</p>
            <div id="result-games"></div>
            <p id="result-feedback" role="status" aria-live="polite"></p>
            <button id="submit-result" class="primary wide">送出完賽結果</button>
            <button id="close-result" class="wide">返回</button>
        </section>
    </section>
    <p class="note">加分會立即存入管理台。公開比分由伺服器同步；這個頁面不會自動更新。</p>
    <p><a href="/live">返回完整管理台</a></p>
    <noscript>請在 Safari 設定中啟用 JavaScript，才能使用計分。</noscript>
</main>
</body>
</html>`, { headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    } });
}
