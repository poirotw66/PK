// The reader consumes this public contract, never operator state or credentials.
const required = ['schema_version', 'version', 'workspace_id', 'revision_id', 'updated_at'];

function descriptor(value) {
  if (!value || required.some(key => typeof value[key] !== 'string') ||
      value.schema_version !== '1.0.0' || !Number.isFinite(Date.parse(value.updated_at))) {
    throw new Error('賽況資料格式不相容，請稍後重試。');
  }
  return value;
}

function snapshot(value, head) {
  descriptor(value);
  if (required.some(key => value[key] !== head[key])) throw new Error('賽況正在更新，稍後會再次同步。');
  if (typeof value.name !== 'string' || typeof value.timezone !== 'string' ||
      !['ready', 'pending_review'].includes(value.publication_status) ||
      ['divisions', 'stages', 'courts', 'matches'].some(key => !Array.isArray(value[key]))) {
    throw new Error('無法辨識這份公開賽況。');
  }
  new Intl.DateTimeFormat('zh-TW', {timeZone: value.timezone});
  for (const key of ['divisions', 'stages', 'courts']) {
    if (value[key].some(row => !row || typeof row.id !== 'string' || typeof row.name !== 'string')) {
      throw new Error('賽況分類資料不完整。');
    }
  }
  if (value.stages.some(stage => stage.bye_names !== undefined && (!Array.isArray(stage.bye_names) || stage.bye_names.some(name => typeof name !== 'string'))) ||
      value.divisions.some(division => (division.entry_count !== undefined && division.entry_count !== null && (!Number.isInteger(division.entry_count) || division.entry_count < 0)) ||
        (division.single_entry_name !== undefined && division.single_entry_name !== null && typeof division.single_entry_name !== 'string'))) {
    throw new Error('組別或輪空資料不完整。');
  }
  const ids = new Set();
  for (const match of value.matches) {
    if (!match || typeof match.id !== 'string' || ids.has(match.id) || typeof match.code !== 'string' ||
        typeof match.division_id !== 'string' || typeof match.stage_id !== 'string' ||
        !match.side_a || !match.side_b ||
        typeof match.side_a.name !== 'string' || typeof match.side_b.name !== 'string' ||
        !Number.isInteger(match.round) || match.round < 1 ||
        (match.planned_start_at !== null && !Number.isFinite(Date.parse(match.planned_start_at))) ||
        (match.result && (!Array.isArray(match.result.games) || match.result.games.some(g =>
          !g || !Number.isInteger(g.side_a) || !Number.isInteger(g.side_b) || g.side_a < 0 || g.side_b < 0)))) {
      throw new Error('對戰資料不完整，保留上次成功讀取的內容。');
    }
    ids.add(match.id);
  }
  if (value.standings !== undefined) {
    if (!Array.isArray(value.standings) || value.standings.some(group => !group ||
      typeof group.stage_id !== 'string' || typeof group.name !== 'string' ||
      typeof group.notice !== 'string' || typeof group.scoring_label !== 'string' ||
      !['provisional','manual_review','final'].includes(group.status) || !Array.isArray(group.rows) ||
      group.rows.some(row => !row || typeof row.entry_id !== 'string' || typeof row.name !== 'string' ||
        !Number.isFinite(row.points) || (row.rank !== null && (!Number.isInteger(row.rank) || row.rank < 1)) ||
        ['wins','losses','forfeits','played_count','adjudicated_count','games_for','games_against'].some(key => !Number.isInteger(row[key]) || row[key] < 0) ||
        typeof row.qualifies !== 'boolean'))) throw new Error('積分資料不完整，保留上次成功讀取的內容。');
  }
  return value;
}

export class HttpJsonSource {
  constructor(config, baseUrl = globalThis.location?.href, fetcher = globalThis.fetch) {
    if (typeof config.version_url !== 'string' || !config.version_url) throw new Error('未設定賽況資料來源。');
    this.versionUrl = new URL(config.version_url, baseUrl);
    this.fetcher = fetcher.bind(globalThis);
    this.url(this.versionUrl.href);
  }

  url(value) {
    const url = new URL(value, this.versionUrl);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
        url.origin !== this.versionUrl.origin) throw new Error('賽況資料來源網址無效。');
    return url;
  }

  async load(previous = null, {signal} = {}) {
    const response = await this.fetcher(this.versionUrl.href, {
      signal, cache: 'no-store', credentials: 'omit',
      headers: previous ? {'If-None-Match': `"${previous.version}"`} : {},
    });
    if (response.status === 304 && previous) return {descriptor: previous, snapshot: null};
    if (!response.ok) throw new Error('暫時無法取得最新賽況。');
    const head = descriptor(await response.json());
    if (previous && head.version === previous.version) return {descriptor: previous, snapshot: null};
    if (typeof head.snapshot_url !== 'string') throw new Error('未提供賽況資料位置。');
    const result = await this.fetcher(this.url(head.snapshot_url).href, {
      signal, cache: 'no-store', credentials: 'omit',
    });
    if (result.status === 409) throw new Error('賽況正在更新，稍後會再次同步。');
    if (!result.ok) throw new Error('暫時無法讀取對戰資料。');
    return {descriptor: head, snapshot: snapshot(await result.json(), head)};
  }
}

export function createSource(config, baseUrl, fetcher) {
  if (config?.type !== 'http-json') throw new Error('不支援這個賽況資料來源。');
  return new HttpJsonSource(config, baseUrl, fetcher);
}

export class LiveReader {
  constructor({source, onSnapshot, onStatus, interval = 3000, timeout = 8000}) {
    Object.assign(this, {source, onSnapshot, onStatus, interval, timeout});
    this.active = false;
    this.busy = false;
    this.generation = 0;
    this.head = null;
    this.lastSuccess = null;
  }

  start() {
    if (this.active) return;
    this.active = true;
    this.generation++;
    void this.refresh();
  }

  stop() {
    this.active = false;
    this.generation++;
    clearTimeout(this.timer);
    this.controller?.abort();
  }

  async refresh() {
    if (!this.active || this.busy) return;
    this.busy = true;
    clearTimeout(this.timer);
    const generation = this.generation;
    const controller = new AbortController();
    this.controller = controller;
    const deadline = setTimeout(() => controller.abort(), this.timeout);
    try {
      const next = await this.source.load(this.head, {signal: controller.signal});
      if (!this.active || generation !== this.generation) return;
      if (next.snapshot) {
        if (this.head?.workspace_id === next.descriptor.workspace_id &&
            Date.parse(next.descriptor.updated_at) < Date.parse(this.head.updated_at)) {
          throw new Error('資料來源回傳較舊版本，保留目前賽況並稍後重試。');
        }
        this.onSnapshot(next.snapshot);
        this.head = next.descriptor;
      }
      this.lastSuccess = new Date();
      this.onStatus({state: 'live', checkedAt: this.lastSuccess, hasData: !!this.head});
    } catch (error) {
      if (this.active && generation === this.generation) {
        this.onStatus({state: this.head ? 'stale' : 'error', checkedAt: this.lastSuccess,
          hasData: !!this.head, message: error.name === 'AbortError' ? '連線逾時，稍後會自動重試。' : error.message});
      }
    } finally {
      clearTimeout(deadline);
      this.busy = false;
      if (this.active) this.timer = setTimeout(() => void this.refresh(), this.interval);
    }
  }
}
