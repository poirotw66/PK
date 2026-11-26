'use strict';

const formatNames = {
    round_robin: '全組循環',
    pools_knockout: '小組循環後淘汰',
    single_elimination: '直接單淘汰',
};

function formatEntryCount(group) {
    return group.format.entry_count ?? data.entries.filter(
        entry => entry.division_id === group.id && entry.status === 'active',
    ).length;
}

function formatEstimate(plan, entries) {
    const isPools = plan.template_id === 'pools_knockout';
    const isElimination = plan.template_id === 'single_elimination';
    const qualifiers = isPools
        ? plan.pools.reduce((total, pool) => total + pool.advances, 0)
        : entries;
    const poolMatches = plan.pools.reduce((total, pool) => total + pool.size * (pool.size - 1) / 2, 0);
    const mainMatches = isPools || isElimination
        ? poolMatches + qualifiers - 1 + Number(plan.bronze)
        : entries * (entries - 1) / 2;
    const firstRoundPairs = isElimination ? entries - 2 ** (Math.ceil(Math.log2(entries)) - 1) : 0;
    const encouragementMax = plan.encouragement ? Math.max(0, firstRoundPairs - 1) : 0;
    return {main_matches: mainMatches, encouragement_max: encouragementMax, qualifiers, poolMatches};
}

function preferredPoolSizes(entries) {
    const fourPlayerPools = entries % 3;
    const threePlayerPools = (entries - 4 * fourPlayerPools) / 3;
    if (threePlayerPools < 0 || fourPlayerPools + threePlayerPools < 2) return [];
    return [...Array(fourPlayerPools).fill(4), ...Array(threePlayerPools).fill(3)];
}

function distributeQualifiers(sizes, qualifiers) {
    const pools = sizes.map(size => ({size, advances: 1}));
    let remaining = qualifiers - pools.length;
    for (let advances = 2; remaining > 0; advances += 1) {
        for (const pool of pools) {
            if (remaining > 0 && advances <= pool.size) {
                pool.advances = advances;
                remaining -= 1;
            }
        }
    }
    return pools;
}

function updateFormatTotals(output, editedId, estimate) {
    let main = 0;
    let reserve = 0;
    let unplanned = 0;
    for (const group of data.divisions) {
        if (!group.format || formatEntryCount(group) < 2) continue;
        const planned = group.id === editedId ? estimate : group.format;
        if (!planned || !Number.isInteger(planned.main_matches)) {
            unplanned += 1;
            continue;
        }
        main += planned.main_matches;
        reserve += planned.encouragement_max;
    }
    const combined = main + reserve;
    const status = unplanned ? `另有 ${unplanned} 組尚未完成估算。`
        : combined > 240 ? `預留後超出 240 場目標 ${combined - 240} 場。`
        : combined < 220 ? '預留後低於 220 場目標。' : '預留後在 220 至 240 場目標內。';
    output.replaceChildren(
        node('strong', `主賽 ${main} 場 · 鼓勵賽最多預留 ${reserve} 場 · 合計最多 ${combined} 場`),
        node('p', status, combined > 240 ? 'format-warning' : 'muted'),
    );
}

function appendFormatReview(summary, root) {
    const plan = summary.format;
    const group = data.divisions.find(item => item.id === summary.division_id);
    root.append(node('h3', group?.name || '組別賽制', 'review-section'));
    root.append(node('p', `${formatNames[plan.template_id]} · 主賽 ${plan.main_matches} 場 · 鼓勵賽最多預留 ${plan.encouragement_max} 場`));
    if (plan.pools.length) {
        root.append(table(['池別', '人數', '晉級人數'], plan.pools.map((pool, index) => [
            `第 ${index + 1} 池`, pool.size, pool.advances,
        ])));
    }
    root.append(node('p', plan.bronze ? '含季殿賽。' : '不設季殿賽。', 'muted'));
}

function formatsView() {
    const root = node('div', null, 'stack format-planner');
    root.append(groupCreationPanel());
    const groups = data.divisions.filter(group => group.format);
    const group = division();
    const totals = panel('全賽事場數試算', '鼓勵賽場數為預留上限，實際以首輪成績及資格確認為準。');
    const totalOutput = node('div', null, 'format-estimate');
    totalOutput.setAttribute('role', 'status');
    totals.append(totalOutput);
    updateFormatTotals(totalOutput);
    root.append(totals);

    if (!group?.format) {
        const overview = panel('各組賽制', '先確認有效名單與各組賽制，再到種子設定與抽籤。');
        const rows = groups.map(item => {
            const plan = item.format;
            const name = formatNames[plan.template_id] || '尚未設定';
            const pending = plan.template_id === 'round_robin' ? '賽制已確認 · 待產生對戰'
                : item.draw?.status === 'approved' ? '籤表已確認 · 待產生對戰' : '待抽籤';
            return [
                item.name, formatEntryCount(item), plan.pending_draw ? `${name} · ${pending}` : name,
                plan.main_matches ?? '—', plan.encouragement_max ?? '—',
                button(plan.editable ? '設定' : '查看', () => {
                    $('division').value = item.id;
                    render();
                    $('format-template')?.focus();
                }),
            ];
        });
        overview.append(table(['組別', '有效人數', '賽制', '主賽', '鼓勵賽上限', '操作'], rows, 'format-overview'));
        root.append(overview);
        return root;
    }

    const saved = group.format;
    const entries = formatEntryCount(group);
    const editor = panel(`${group.name} · ${entries} 人`, '調整組別賽制會先預覽，確認後才儲存。');
    if (saved.pending_draw) {
        const notice = saved.template_id === 'round_robin' ? '賽制已確認，待其他組完成後產生對戰。'
            : group.draw?.status === 'approved' ? '籤表已確認，待其他組完成後產生對戰。'
            : '賽制已確認，下一步是確認種子與抽籤。';
        editor.append(node('p', notice, 'notice'));
    }
    if (!saved.editable) editor.append(node('p', '目前無法變更此組賽制。', 'notice'));
    const form = node('form', null, 'format-editor');
    const controls = node('fieldset', null, 'format-fields');
    controls.disabled = !saved.editable;
    const mode = select([['', '選擇賽制'], ...Object.entries(formatNames)], saved.template_id || '');
    mode.id = 'format-template';
    mode.required = true;
    controls.append(field('賽制', mode));

    const bronze = input('checkbox');
    bronze.checked = saved.bronze;
    const bronzeRow = field('安排季殿賽', bronze);
    bronzeRow.className = 'format-toggle';
    controls.append(bronzeRow);
    const encouragement = input('checkbox');
    encouragement.checked = saved.encouragement;
    const encouragementRow = field('開設首輪敗者鼓勵賽', encouragement);
    encouragementRow.className = 'format-toggle';
    controls.append(encouragementRow);

    const poolEditor = node('div', null, 'format-pool-editor');
    const poolList = node('div', null, 'format-pools');
    const poolActions = node('div', null, 'format-actions');
    const pools = saved.pools.map(pool => ({...pool}));
    const preferredSizes = preferredPoolSizes(entries);
    const savedQualifiers = saved.pools.reduce((total, pool) => total + pool.advances, 0);
    const suggestionText = node('p', null, 'muted');
    const suggestionButton = button('套用建議配池', () => {
        const suggested = suggestedPools();
        if (!suggested) return;
        pools.splice(0, pools.length, ...suggested);
        renderPools(0);
        refresh();
    });
    const suggestion = node('div', null, 'format-actions');
    suggestion.append(suggestionText, suggestionButton);

    function suggestedPools() {
        if (!preferredSizes.length) return null;
        const qualifiers = savedQualifiers || Math.max(preferredSizes.length, bronze.checked ? 4 : 2);
        if (qualifiers < preferredSizes.length || qualifiers > entries || (bronze.checked && qualifiers < 4)) {
            return null;
        }
        return distributeQualifiers(preferredSizes, qualifiers);
    }

    function renderPools(focusIndex) {
        poolList.replaceChildren();
        pools.forEach((pool, index) => {
            const row = node('div', null, 'format-pool');
            const title = `第 ${index + 1} 池`;
            const size = select([...(pool.size === 2 ? [[2, '2 人（既有）']] : []), [3, '3 人'], [4, '4 人']], pool.size);
            const advances = select(Array.from({length: pool.size}, (_, i) => [i + 1, `${i + 1} 人`]), pool.advances);
            size.setAttribute('aria-label', `${title}人數`);
            advances.setAttribute('aria-label', `${title}晉級人數`);
            size.addEventListener('change', () => {
                pool.size = Number(size.value);
                pool.advances = Math.min(pool.advances, pool.size);
                renderPools(index);
                refresh();
            });
            advances.addEventListener('change', () => {
                pool.advances = Number(advances.value);
                refresh();
            });
            const remove = button('移除', () => {
                pools.splice(index, 1);
                renderPools(Math.min(index, pools.length - 1));
                if (!pools.length) poolActions.querySelector('button').focus();
                refresh();
            });
            remove.setAttribute('aria-label', `移除${title}`);
            row.append(field(title, size), field('晉級', advances), remove);
            poolList.append(row);
            if (index === focusIndex) size.focus();
        });
    }
    for (const [size, label] of [[3, '新增三人池'], [4, '新增四人池']]) {
        poolActions.append(button(label, () => {
            pools.push({size, advances: 1});
            renderPools(pools.length - 1);
            refresh();
        }));
    }
    poolEditor.append(node('h3', '配池'), suggestion, poolList, poolActions);
    controls.append(poolEditor);
    const estimateOutput = node('div', null, 'format-estimate');
    estimateOutput.setAttribute('role', 'status');
    const submit = node('button', '預覽賽制', 'primary');
    submit.type = 'submit';
    const actions = node('div', null, 'format-actions');
    actions.append(submit);
    controls.append(estimateOutput, actions);
    form.append(controls);
    editor.append(form);
    root.append(editor);

    function requestFormat() {
        const isPools = mode.value === 'pools_knockout';
        const isElimination = mode.value === 'single_elimination';
        return {
            template_id: mode.value,
            pools: isPools ? pools.map(pool => ({...pool})) : [],
            bronze: (isPools || isElimination) && bronze.checked,
            encouragement: isElimination && Boolean(saved.encouragement_allowed) && encouragement.checked,
        };
    }

    function refresh() {
        const plan = requestFormat();
        const isPools = plan.template_id === 'pools_knockout';
        const isElimination = plan.template_id === 'single_elimination';
        poolEditor.hidden = !isPools;
        bronzeRow.hidden = !isPools && !isElimination;
        encouragementRow.hidden = !isElimination || !saved.encouragement_allowed;
        const suggested = isPools ? suggestedPools() : null;
        suggestion.hidden = !isPools || !preferredSizes.length;
        const matchesPreferred = plan.pools.map(pool => pool.size).sort((a, b) => b - a).join(',')
            === preferredSizes.join(',');
        suggestionButton.hidden = !suggested || matchesPreferred;
        if (suggested) {
            const estimate = formatEstimate({...plan, pools: suggested}, entries);
            const baseline = Number.isInteger(saved.main_matches) ? `（已儲存 ${saved.main_matches} 場）` : '';
            suggestionText.textContent = `建議 ${preferredSizes.join('＋')} 人，${estimate.qualifiers} 人晉級，主賽 ${estimate.main_matches} 場${baseline}。`;
        } else {
            suggestionText.textContent = `優先配法為 ${preferredSizes.join('＋')} 人。請手動確認各池晉級人數。`;
        }
        const poolSize = plan.pools.reduce((total, pool) => total + pool.size, 0);
        const qualifiers = plan.pools.reduce((total, pool) => total + pool.advances, 0);
        let problem = '';
        if (!plan.template_id) problem = '請先選擇賽制。';
        else if (entries < 2) problem = '有效選手不足 2 人。';
        else if (isPools && poolSize !== entries) {
            problem = `已配 ${poolSize} 人，共 ${entries} 人。${poolSize < entries ? '還差' : '超出'} ${Math.abs(entries - poolSize)} 人。`;
        } else if (isPools && plan.pools.some(pool => ![3, 4].includes(pool.size))) {
            problem = '調整分池時，請使用三人池或四人池。';
        } else if (isPools && qualifiers < 2) problem = '小組賽至少需要 2 人晉級。';
        else if (plan.bronze && (isPools ? qualifiers : entries) < 4) {
            problem = '季殿賽至少需要 4 人進入淘汰賽。';
        }
        submit.disabled = !saved.editable || !!problem;
        if (problem) {
            estimateOutput.replaceChildren(node('p', problem, 'format-warning'));
            updateFormatTotals(totalOutput, group.id, null);
            return;
        }
        const estimate = formatEstimate(plan, entries);
        const detail = isPools ? `已配 ${poolSize} 人。循環 ${estimate.poolMatches} 場，${estimate.qualifiers} 人晉級。` : '';
        estimateOutput.replaceChildren(
            node('strong', `本組主賽 ${estimate.main_matches} 場 · 鼓勵賽最多預留 ${estimate.encouragement_max} 場`),
            ...(detail ? [node('p', detail, 'muted')] : []),
        );
        updateFormatTotals(totalOutput, group.id, estimate);
    }

    mode.addEventListener('change', () => {
        if (mode.value !== 'pools_knockout' || pools.length) return;
        const suggested = suggestedPools();
        if (suggested) pools.push(...suggested);
        renderPools();
    });
    form.addEventListener('change', refresh);
    form.addEventListener('submit', event => {
        event.preventDefault();
        if (!submit.disabled) void preview({action: 'format', division_id: group.id, format: requestFormat()});
    });
    renderPools();
    refresh();
    root.append(button('下一步：產生與調整籤表', () => go('seeds')));
    return root;
}

function groupCreationPanel() {
    const box = panel('逐組建賽', '輸入本組名單 → 確認賽制 → 產生初步籤表 → 調整籤位。各組完成後到賽程安排微調整體順序。');
    const details = node('details'), title = node('summary', '新增一組選手名單');
    const form = node('form', null, 'form');
    const name = input('text', ''), names = node('textarea');
    name.required = true; name.placeholder = '例如 公開男子組';
    names.required = true; names.rows = 7; names.placeholder = '每行一位選手\n王小明\n陳大文';
    const category = select([['competitive', '競技組'], ['school', '學校組']], 'competitive');
    const gender = select([['male', '男子'], ['female', '女子'], ['mixed', '混合']], 'mixed');
    form.append(field('組別名稱', name), field('選手名單', names), field('組別分類', category), field('性別分類', gender));
    const reuse = input('checkbox');
    form.append(field('沿用已確認的同名選手（跨組報名）', reuse));
    const submit = node('button', '預覽新增名單', 'primary'); submit.type = 'submit'; form.append(submit);
    form.addEventListener('submit', event => {
        event.preventDefault();
        void preview({action: 'division_roster', roster: {name: name.value, names: names.value.split(/\r?\n/).map(n => n.trim()).filter(Boolean), category: category.value, gender_category: gender.value, reuse_existing: reuse.checked}});
    });
    details.append(title, form); box.append(details);
    if (!data.coach_competition_rules) {
        box.append(node('p', '新競賽規則需先預覽確認。冠軍賽五戰三勝，其餘三戰兩勝；直接淘汰首輪敗者含種子可參加鼓勵賽。', 'muted'), button('預覽教練競賽規則', () => preview({action: 'coach_rules'})));
    } else box.append(node('p', '已採用教練競賽規則。鼓勵賽僅取冠軍；小組循環後淘汰不適用鼓勵賽。', 'muted'));
    box.append(button('調整本組籤位', () => go('seeds')), button('前往賽程安排', () => go('schedule')));
    return box;
}

function drawPositionEditor(group) {
    const draw = group.draw;
    if (!draw?.output?.slots || !['draft', 'review_required'].includes(draw.status)) return null;
    const box = panel('初步賽程與籤位調整', '先選兩個籤位交換，預覽確認後再確認籤表。種子、固定籤位及輪空限制仍然適用。');
    const positions = draw.output.slots.map(slot => slot.entry_id);
    const rows = node('div'), first = select(positions.map((entry, i) => [i, `${i + 1} · ${entry ? entryName(entry) : '輪空'}`]), '0');
    const second = select(positions.map((entry, i) => [i, `${i + 1} · ${entry ? entryName(entry) : '輪空'}`]), positions.length > 1 ? '1' : '0');
    const display = () => {
        rows.replaceChildren(table(['籤位', '選手'], positions.map((entry, i) => [i + 1, entry ? entryName(entry) : '輪空'])));
        if (group.format.template_id === 'round_robin') {
            const pairs = [];
            positions.forEach((entry, i) => positions.slice(i + 1).forEach(other => pairs.push([pairs.length + 1, entryName(entry), entryName(other)])));
            rows.append(table(['循環對戰', '選手一', '選手二'], pairs));
        } else if (draw.output.pools?.length) rows.append(table(['小組', '選手'], draw.output.pools.map(pool => [pool.label, pool.entry_ids.map(entryName).join('、')])));
        else if (draw.output.bracket_entry_ids?.length) rows.append(table(['首輪', '選手一', '選手二'], Array.from({length: positions.length / 2}, (_, i) => [i + 1, positions[2 * i] ? entryName(positions[2 * i]) : '輪空', positions[2 * i + 1] ? entryName(positions[2 * i + 1]) : '輪空'])));
    };
    box.append(rows, field('籤位一', first), field('籤位二', second), button('交換並預覽籤位', () => {
        const swapped = [...positions]; const a = Number(first.value), b = Number(second.value);
        [swapped[a], swapped[b]] = [swapped[b], swapped[a]];
        void preview({action: 'draw_positions', draw_id: draw.id, positions: swapped});
    }));
    display(); return box;
}
