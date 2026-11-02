'use strict';

// Operation drafts only. No actor, token, match names or public-feed data is persisted.
globalThis.ScheduleDraftStore = class ScheduleDraftStore {
  static prefix = 'squash.schedule-draft.v1:';
  static uniqueId() { return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`; }
  static error(code, message) { return Object.assign(new Error(message), {code}); }
  static decode(raw, workspaceId) {
    if (raw === null) return null;
    let value;
    try { value = JSON.parse(raw); } catch { throw this.error('DRAFT_STORAGE_INVALID','本機草案格式損壞，請丟棄後重新編輯。'); }
    const keys = ['version','workspace_id','base_revision_id','saved_at','writer_id','generation','updates','reason'];
    const short = v=>typeof v==='string' && v.length>0 && v.length<=200;
    if (!value || Array.isArray(value) || typeof value!=='object' || Object.keys(value).some(k=>!keys.includes(k)) || keys.some(k=>!Object.hasOwn(value,k)) ||
        value.version!==1 || value.workspace_id!==workspaceId || !short(value.workspace_id) || !short(value.base_revision_id) || !short(value.writer_id) || !short(value.generation) ||
        typeof value.saved_at!=='string' || !Number.isFinite(Date.parse(value.saved_at)) || typeof value.reason!=='string' || value.reason.length>2000 ||
        !Array.isArray(value.updates) || value.updates.length<1 || value.updates.length>500) {
      throw this.error('DRAFT_STORAGE_INVALID','本機草案格式不相容，請檢視提示並丟棄後重新編輯。');
    }
    const seen = new Set();
    for (const u of value.updates) {
      if (!u || typeof u!=='object' || Array.isArray(u) || Object.keys(u).sort().join(',')!=='court_id,local_start,match_id' ||
          !short(u.match_id) || !short(u.court_id) || seen.has(u.match_id) || typeof u.local_start!=='string' ||
          !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(u.local_start)) throw this.error('DRAFT_STORAGE_INVALID','本機草案包含無效或重複場次，請丟棄後重新編輯。');
      const time = new Date(u.local_start+':00Z');
      if (!Number.isFinite(time.getTime()) || time.toISOString().slice(0,16)!==u.local_start) throw this.error('DRAFT_STORAGE_INVALID','本機草案包含無效時間，請丟棄後重新編輯。');
      seen.add(u.match_id);
    }
    return value;
  }

  constructor({workspaceId, storage, locks, writerId}) {
    this.workspaceId = workspaceId;
    this.key = ScheduleDraftStore.prefix+encodeURIComponent(workspaceId);
    this.storage = storage;
    this.locks = locks;
    this.writerId = writerId;
    this.raw = null;
    this.record = null;
    this.epoch = 0;
    this.queue = Promise.resolve();
  }
  read() {
    try { this.raw = this.storage.getItem(this.key); }
    catch { throw ScheduleDraftStore.error('DRAFT_STORAGE_UNAVAILABLE','瀏覽器無法讀取本機草案；請保留目前網頁。'); }
    this.record = ScheduleDraftStore.decode(this.raw,this.workspaceId);
    return this.record;
  }
  peek() {
    try { return this.storage.getItem(this.key); }
    catch { throw ScheduleDraftStore.error('DRAFT_STORAGE_UNAVAILABLE','瀏覽器無法讀取本機草案；請保留目前網頁。'); }
  }
  enqueue(job) {
    const operation = this.queue.catch(()=>{}).then(()=>{
      if (!this.locks?.request) throw ScheduleDraftStore.error('DRAFT_STORAGE_UNAVAILABLE','此瀏覽器不支援安全的草案保存，編輯仍保留在目前網頁。');
      return this.locks.request(this.key,job);
    });
    this.queue = operation.catch(()=>{});
    return operation;
  }
  compare() {
    if (this.peek()!==this.raw) throw ScheduleDraftStore.error('DRAFT_STORAGE_CONFLICT','其他分頁已更新本機草案，已停止自動覆寫；本頁編輯仍保留。');
  }
  save({baseRevisionId, updates, reason}) {
    const epoch = this.epoch;
    const content = {version:1,workspace_id:this.workspaceId,base_revision_id:baseRevisionId,saved_at:new Date().toISOString(),writer_id:this.writerId,generation:ScheduleDraftStore.uniqueId(),updates:updates.map(u=>({...u})),reason};
    ScheduleDraftStore.decode(JSON.stringify(content),this.workspaceId);
    return this.enqueue(()=>{
      if (epoch!==this.epoch) return false;
      this.compare();
      const raw = JSON.stringify(content);
      try { this.storage.setItem(this.key,raw); }
      catch { throw ScheduleDraftStore.error('DRAFT_STORAGE_UNAVAILABLE','本機草案保存失敗（可能空間不足或儲存被封鎖），請保留目前網頁。'); }
      this.raw = raw; this.record = content;
      return true;
    });
  }
  clear() {
    this.epoch++;
    return this.enqueue(()=>{
      this.compare();
      try { this.storage.removeItem(this.key); }
      catch { throw ScheduleDraftStore.error('DRAFT_STORAGE_UNAVAILABLE','無法清除本機草案，請稍後重試。'); }
      this.raw = null; this.record = null; return true;
    });
  }
};
