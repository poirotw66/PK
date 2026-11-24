(function () {
    'use strict';

    var csrf = '';
    var control = null;
    var score = null;
    var busy = false;
    var resultMatch = null;
    var signin = document.getElementById('signin');

    function byId(id) { return document.getElementById(id); }

    function message(text, kind) {
        byId('feedback').textContent = text;
        byId('feedback').setAttribute('data-kind', kind || '');
        if (resultMatch) {
            byId('result-feedback').textContent = text;
            byId('result-feedback').setAttribute('data-kind', kind || '');
        }
    }

    function request(path, payload, done) {
        var xhr = new XMLHttpRequest();
        xhr.open(payload ? 'POST' : 'GET', '/api/' + path, true);
        xhr.timeout = 20000;
        xhr.setRequestHeader('Accept', 'application/json');
        if (payload) {
            xhr.setRequestHeader('Content-Type', 'application/json');
            xhr.setRequestHeader('X-CSRF-Token', csrf);
        }
        xhr.onload = function () {
            var value;
            if (xhr.status === 401) signin.hidden = false;
            try { value = JSON.parse(xhr.responseText); }
            catch (error) { done('無法讀取回應，請先重新整理。'); return; }
            if (xhr.status >= 200 && xhr.status < 300) done(null, value);
            else done(value.error || '請重新整理後再試。');
        };
        xhr.onerror = xhr.ontimeout = function () {
            done('連線未完成，請先重新整理確認是否已儲存。');
        };
        xhr.send(payload ? JSON.stringify(payload) : null);
    }

    function court() {
        if (!control) return null;
        return control.courts.filter(function (item) { return item.id === byId('court').value; })[0] || null;
    }

    function activeMatch() {
        var selected = court();
        return selected ? selected.current : null;
    }

    function addOption(select, id, label, disabled) {
        var option = document.createElement('option');
        option.value = id;
        option.textContent = label;
        option.disabled = !!disabled;
        select.appendChild(option);
    }

    function localTime(value) {
        return value ? new Date(Date.parse(value) + 8 * 3600000).toISOString().slice(0, 16) : '';
    }

    function fillQueue() {
        var select = byId('match');
        var previous = select.value;
        var date = byId('date').value;
        var term = byId('match-search').value.trim().toLowerCase();
        var number = /^(?:第\s*)?(\d+)(?:\s*場)?$/.exec(term);
        var matches = control ? control.queue.filter(function (item) {
            if (number) return item.code === number[1];
            if (term) return [item.code, item.side_a, item.side_b, item.division].some(function (value) {
                return value.toLowerCase().indexOf(term) !== -1;
            });
            return !date || localTime(item.planned_start).slice(0, 10) === date;
        }) : [];
        select.textContent = '';
        addOption(select, '', matches.length ? '選擇場次' : '沒有符合條件的待賽場次');
        matches.forEach(function (item) {
            var time = localTime(item.planned_start);
            addOption(select, item.id, '第 ' + item.code + ' 場 · ' + time.slice(5).replace('T', ' ') + ' · ' + item.side_a + ' / ' + item.side_b + (item.blocked_reason ? '（' + item.blocked_reason + '）' : ''), !item.eligible);
        });
        select.value = term && matches.length === 1 && matches[0].eligible ? matches[0].id : previous;
        if (select.selectedIndex < 0 || select.options[select.selectedIndex].disabled) select.value = '';
    }

    function queuedMatch() {
        return control && control.queue.filter(function (item) {
            return item.id === byId('match').value && item.eligible;
        })[0];
    }

    function fillStartCourts() {
        var select = byId('start-court');
        var previous = select.value;
        var available = control.courts.filter(function (item) { return !item.current; });
        select.textContent = '';
        addOption(select, '', available.length ? '選擇空場' : '目前沒有空場');
        available.forEach(function (item) { addOption(select, item.id, item.name); });
        select.value = previous;
        if (select.selectedIndex < 0) select.value = '';
    }

    function render() {
        var current = activeMatch();
        var selected = court();
        var summary = score && score.summary;
        byId('operator').hidden = !control;
        byId('queue').hidden = !control || !!current;
        byId('live').hidden = !current;
        byId('court').disabled = busy || !control;
        byId('date').disabled = busy;
        byId('match').disabled = busy;
        byId('refresh').disabled = busy;
        byId('logout').disabled = busy || !csrf;
        byId('read-score').disabled = busy;
        var canAct = !busy && !resultMatch && control && control.published_revision && queuedMatch();
        byId('match-search').disabled = busy;
        byId('start-placement').hidden = !queuedMatch();
        byId('start-court').disabled = !canAct;
        byId('start').disabled = !canAct || !byId('start-court').value;
        byId('point-a').disabled = byId('point-b').disabled = busy || !!resultMatch || !summary || !!summary.winner;
        byId('undo').disabled = busy || !!resultMatch || !score || !score.score.points.length;
        byId('record-queued').disabled = !canAct;
        byId('record-live').disabled = busy || !!resultMatch || !current || !score;
        byId('submit-result').disabled = busy || !resultMatch || (!!current && !score);
        byId('close-result').disabled = busy;
        byId('result-entry').hidden = !resultMatch;
        var fields = byId('result-games').getElementsByTagName('fieldset');
        for (var index = 0; index < fields.length; index++) fields[index].disabled = busy;
        byId('finish').disabled = busy || !!resultMatch || !summary || !summary.winner;
        byId('cancel').disabled = busy || !!resultMatch || !current;
        if (current) {
            byId('match-name').textContent = '第 ' + current.code + ' 場 · ' + selected.name;
            byId('names').textContent = 'A：' + current.side_a + '　B：' + current.side_b;
            byId('game').textContent = summary ? '局數 ' + summary.wins.a + ' : ' + summary.wins.b + (summary.winner ? ' · 請確認完賽' : ' · 第 ' + (summary.games.length + 1) + ' 局') : '請先讀取最新比分';
            byId('points').textContent = summary ? summary.current.side_a + ' : ' + summary.current.side_b : '—';
        }
    }

    function send(button, path, payload, done) {
        if (busy) return;
        var text = button.textContent;
        busy = true;
        button.textContent = payload ? '傳送中…' : '讀取中…';
        render();
        message(button.textContent);
        request(path, payload, function (error, value) {
            busy = false;
            button.textContent = text;
            if (error) {
                score = null;
                message('❌ ' + error, 'error');
                render();
                return;
            }
            done(value);
            render();
        });
    }

    function updateControl(value) {
        var previous = byId('court').value;
        var date = byId('date').value;
        control = value;
        byId('court').textContent = '';
        byId('date').textContent = '';
        addOption(byId('court'), '', control.courts.some(function (item) { return !!item.current; }) ? '選擇進行中場次' : '目前沒有進行中的比賽');
        addOption(byId('date'), '', '全部日期');
        control.courts.forEach(function (item) { if (item.current) addOption(byId('court'), item.id, '第 ' + item.current.code + ' 場 · ' + item.name); });
        control.dates.forEach(function (day) { addOption(byId('date'), day, day.slice(5).replace('-', '/')); });
        byId('court').value = previous;
        if (byId('court').selectedIndex < 0) byId('court').value = '';
        byId('date').value = date;
        if (byId('date').selectedIndex < 0) byId('date').value = '';
        fillQueue();
        fillStartCourts();
    }

    function refresh() {
        send(byId('refresh'), 'ipad', null, function (value) {
            csrf = value.csrf;
            signin.hidden = true;
            resultMatch = null;
            score = null;
            updateControl(value);
            message(value.published_revision ? '賽程已更新。' : '請先在完整管理台發布賽程。', 'success');
        });
    }

    function loadScore() {
        var current = activeMatch();
        if (!current) return;
        send(byId('read-score'), 'score?match_id=' + encodeURIComponent(current.id), null, function (value) {
            score = value;
            message(value.sync_pending ? '已讀取比分。公開站同步尚待完成。' : '已讀取最新比分。', 'success');
        });
    }

    function point(button, action) {
        var current = activeMatch();
        if (!score || !current) return;
        send(button, 'score', { match_id: current.id, version: score.version, action: action }, function (value) {
            score = value;
            message('✅ 比分已成功儲存。公開站由伺服器同步。', 'success');
        });
    }

    function matchAction(button, action) {
        if (busy || !control) return;
        var starting = action === 'start';
        var match = starting ? queuedMatch() : activeMatch();
        var courtId = starting ? byId('start-court').value : byId('court').value;
        if (!match || !courtId) return;
        var payload = { revision: control.revision, sequence: control.sequence, action: action, court_id: courtId, match_id: match.id };
        if (action === 'cancel') {
            var reason = window.prompt('請填寫撤銷開賽的原因。');
            if (!reason || !reason.trim()) return;
            payload.reason = reason.trim();
        } else {
            if (action === 'end' && (!score || !score.summary.winner)) return;
            if (!window.confirm(action === 'start' ? '已核對雙方選手，確定開賽？' : '確定送出完賽成績？')) return;
            if (action === 'end') payload.score_version = score.version;
        }
        send(button, 'live', payload, function (value) {
            score = null;
            updateControl(value);
            message(value.pending ? '✅ 管理台已儲存，公開站同步尚待完成。' : '✅ 操作已儲存並同步。', 'success');
            if (starting) { byId('court').value = courtId; render(); loadScore(); }
        });
    }

    function openResult() {
        var current = activeMatch();
        if (busy) return;
        var match = current || queuedMatch();
        if (!match || (current && !score)) return;
        var rules = match.scoring_rules;
        resultMatch = match;
        byId('result-name').textContent = '第 ' + match.code + ' 場 · 直接登錄完賽';
        byId('result-rules').textContent = rules.best_of + ' 局制，每局 ' + rules.target + ' 分，須領先 ' + rules.margin + ' 分。';
        byId('result-feedback').textContent = '';
        var games = byId('result-games');
        games.textContent = '';
        for (var index = 0; index < rules.best_of; index++) {
            var row = document.createElement('fieldset');
            row.className = 'game-row';
            var legend = document.createElement('legend');
            legend.textContent = '第 ' + (index + 1) + ' 局';
            row.appendChild(legend);
            [['a', match.side_a], ['b', match.side_b]].forEach(function (side) {
                var label = document.createElement('label');
                label.textContent = side[1];
                var input = document.createElement('input');
                input.type = 'text';
                input.setAttribute('inputmode', 'numeric');
                input.setAttribute('pattern', '[0-9]*');
                input.maxLength = 3;
                input.id = 'result-' + index + '-' + side[0];
                label.appendChild(input);
                row.appendChild(label);
            });
            games.appendChild(row);
        }
        render();
        byId('result-entry').scrollIntoView();
    }

    function submitResult() {
        var current = activeMatch();
        if (!resultMatch || busy || (current && (current.id !== resultMatch.id || !score))) return;
        var games = [];
        var blank = false;
        for (var index = 0; index < resultMatch.scoring_rules.best_of; index++) {
            var a = byId('result-' + index + '-a').value.trim();
            var b = byId('result-' + index + '-b').value.trim();
            if (!a && !b) { blank = true; continue; }
            if (blank || !/^\d{1,3}$/.test(a) || !/^\d{1,3}$/.test(b)) {
                message('❌ 請依序填寫各局的兩個分數，未打的局留空。', 'error');
                return;
            }
            games.push({ side_a: Number(a), side_b: Number(b) });
        }
        if (games.length < 2) {
            message('❌ 請輸入整場的完賽局分。', 'error');
            return;
        }
        var summary = games.map(function (game) { return game.side_a + ':' + game.side_b; }).join('、');
        if (!window.confirm('確定送出完賽結果：' + summary + '？')) return;
        var payload = { revision: control.revision, sequence: control.sequence, action: 'record-result', match_id: resultMatch.id, games: games };
        if (current) payload.score_version = score.version;
        send(byId('submit-result'), 'live', payload, function (value) {
            score = null;
            resultMatch = null;
            updateControl(value);
            message(value.pending ? '✅ 完賽結果已儲存，公開站同步尚待完成。' : '✅ 完賽結果已儲存並同步。', 'success');
            byId('feedback').scrollIntoView();
        });
    }

    byId('logout').onclick = function () {
        send(this, 'logout', {}, function () { window.location.assign('/temporary-login'); });
    };
    byId('refresh').onclick = refresh;
    byId('court').onchange = function () { resultMatch = null; score = null; fillQueue(); render(); if (activeMatch()) loadScore(); };
    byId('date').onchange = function () { byId('match-search').value = ''; resultMatch = null; fillQueue(); render(); };
    byId('match-search').oninput = function () { resultMatch = null; byId('start-court').value = ''; fillQueue(); render(); };
    byId('start-court').onchange = render;
    byId('match').onchange = function () { resultMatch = null; byId('start-court').value = ''; render(); };
    byId('read-score').onclick = loadScore;
    byId('point-a').onclick = function () { point(this, 'a'); };
    byId('point-b').onclick = function () { point(this, 'b'); };
    byId('undo').onclick = function () { point(this, 'undo'); };
    byId('start').onclick = function () { matchAction(this, 'start'); };
    byId('record-queued').onclick = byId('record-live').onclick = openResult;
    byId('submit-result').onclick = submitResult;
    byId('close-result').onclick = function () { resultMatch = null; render(); };
    byId('finish').onclick = function () { matchAction(this, 'end'); };
    byId('cancel').onclick = function () { matchAction(this, 'cancel'); };
    refresh();
}());
