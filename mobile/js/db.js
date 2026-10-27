/* =========================================================================
   IndexedDB 薄封装（无依赖，Promise 化）
   -------------------------------------------------------------------------
   为什么不用 localStorage：题库千题级 + 作答流水会无上限增长，
   localStorage 只有 ~5MB 且同步阻塞主线程。IndexedDB 容量按配额给（通常数百 MB）。

   为什么手写而不是引 Dexie：这个 App 的纪律是「零构建、零 CDN」——
   多一个运行时依赖就要多一个构建步骤或一次外部请求，换来的只是几十行胶水。
   ========================================================================= */
(function (global) {
  "use strict";

  const DB_NAME = "medkit-mobile";
  const DB_VER = 1;
  const STORES = {
    banks: { keyPath: "id" },
    items: { keyPath: "id", indexes: [["bank_id", "bank_id"], ["subtopic", "subtopic"]] },
    attempts: { keyPath: "seq", autoIncrement: true, indexes: [["item_id", "item_id"]] },
    mistakes: { keyPath: "id", indexes: [["due", "due"], ["subtopic", "subtopic"]] },
    meta: { keyPath: "k" },
  };

  let _db = null;

  function open() {
    if (_db) return Promise.resolve(_db);
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VER);
      req.onupgradeneeded = () => {
        const db = req.result;
        for (const [name, cfg] of Object.entries(STORES)) {
          if (db.objectStoreNames.contains(name)) continue;
          const os = db.createObjectStore(name, {
            keyPath: cfg.keyPath,
            autoIncrement: !!cfg.autoIncrement,
          });
          for (const [idxName, path] of cfg.indexes || []) {
            os.createIndex(idxName, path, { unique: false });
          }
        }
      };
      req.onsuccess = () => {
        _db = req.result;
        // 另一个标签页升级了库 → 主动关掉，避免 "blocked" 卡死后续请求
        _db.onversionchange = () => { _db.close(); _db = null; };
        resolve(_db);
      };
      req.onerror = () => reject(req.error || new Error("无法打开本地数据库"));
      req.onblocked = () => reject(new Error("数据库被其他页面占用，请关闭其他标签页后重试"));
    });
  }

  function tx(store, mode, fn) {
    return open().then(db => new Promise((resolve, reject) => {
      const t = db.transaction(store, mode);
      const os = t.objectStore(store);
      let out;
      try {
        out = fn(os);
      } catch (e) {
        reject(e);
        return;
      }
      t.oncomplete = () => resolve(out && out.__req ? out.__req.result : out);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error || new Error("事务被中止"));
    }));
  }

  /* 把 IDBRequest 包成「事务完成后再取值」的小壳：
     直接 resolve(req.result) 会在事务提交前就读，遇到竞态时拿不到数据。 */
  function wrap(req) { return { __req: req }; }

  const api = {
    open,

    get(store, key) { return tx(store, "readonly", os => wrap(os.get(key))); },

    all(store) { return tx(store, "readonly", os => wrap(os.getAll())); },

    count(store) { return tx(store, "readonly", os => wrap(os.count())); },

    put(store, value) { return tx(store, "readwrite", os => { os.put(value); return true; }); },

    del(store, key) { return tx(store, "readwrite", os => { os.delete(key); return true; }); },

    clear(store) { return tx(store, "readwrite", os => { os.clear(); return true; }); },

    /* 批量写：单个事务内 put 多条（逐条开事务在千题级会慢到肉眼可见） */
    bulkPut(store, rows) {
      return tx(store, "readwrite", os => {
        for (const r of rows) os.put(r);
        return rows.length;
      });
    },

    /* 按索引取（如 mistakes 的 due） */
    byIndex(store, indexName, query) {
      return tx(store, "readonly", os => wrap(os.index(indexName).getAll(query)));
    },

    async getMeta(key, fallback) {
      const row = await api.get("meta", key);
      return row ? row.v : fallback;
    },
    setMeta(key, v) { return api.put("meta", { k: key, v }); },

    /* 一次性清空全部业务数据（保留 meta 里的设置） */
    async clearAll() {
      for (const s of ["banks", "items", "attempts", "mistakes"]) await api.clear(s);
    },
  };

  global.DB = api;
})(window);
