"""多轮网络检索（设计文档 §5.4 · 2026-08 落地）。

可插拔后端（默认关）：
- zhipu_tool / qwen_tool：与用户所选服务商的 LLM web_search 工具能力匹配
- bocha：博查 AI 搜索 REST API（独立计费，用户填 Key）
- manual：用户手动粘贴（终极兜底，任何情况下可用）

多轮循环（LLM 驱动，3 轮封顶）：
 Round 1  输入 {科目}{章节}{教师重点关键词} → LLM 生成 ≤3 条检索词（考纲/真题/指南）
          → 后端检索 → 摘要快照（title/url/snippet）
 Round 2  LLM 审阅 Round 1 → 缺口 → ≤2 条补充检索词 → 检索
 Round 3  冲突核查：检索到的答案/数值与教材切片冲突 → conflict 标记（绝不自动改写）

防护：检索词条数硬上限、按 URL 去重、视频/社交站白名单跳过、错误单后端隔离（不崩管线）。
输出：{materials: [{title,url,snippet,round,conflict}], logs, errors}
"""

import re
import time
from typing import Any, Callable, Optional

import httpx

BOCHA_URL = "https://api.bochaai.com/v1/web-search"
ZHIPU_WEB_SEARCH_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"
DEEPSEEK_RESPONSES_URL = "https://api.deepseek.com/responses"
QWEN_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"

ROUNDS_MAX = 3
QUERIES_PER_ROUND = 3
QUERIES_ROUND2 = 2
SNIPPET_LIMIT = 300
MATERIALS_LIMIT = 12          # 进入出题管线的素材条数上限
# 2026-09-01 实测：DeepSeek Responses web_search（服务端托管搜索）单次 20~60s
# （2 次检索词调用 + 推理），25s 必超 → 「测试后端」偶发 ReadTimeout。放宽到 75s。
MAX_HTTP_TIMEOUT = 75.0

BANNED_HOSTS = ("bilibili.com", "douyin.com", "youtube.com", "weibo.com",
                "tieba.baidu.com", "xiaohongshu.com", "kuaishou.com",
                "sohu.com", "sina.com.cn")

# WP-6：可信医学来源（后缀/域名白名单，用户可自定义追加）
TRUSTED_SUFFIXES = (
    "gov.cn", "edu.cn", "org.cn", "ac.cn", "who.int", "nih.gov",
    "nhs.uk", "medlineplus.gov", "gov.hk", "cmap.org.cn", "cma.org.cn",
)
TRUSTED_DOMAINS = (
    "msdmanuals.cn", "dayi.org.cn", "cnki.net", "wanfangdata.com.cn",
    "pubmed.ncbi.nlm.nih.gov", "nmpa.gov.cn", "nhc.gov.cn",
)

BACKEND_LABELS = {
    "zhipu_tool": "智谱 GLM（Web Search API）",
    "qwen_tool": "通义千问（enable_search）",
    "deepseek_tool": "DeepSeek 内置联网搜索（Responses API）",
    "bocha": "博查 AI 搜索",
    "manual": "手动粘贴（兜底）",
}

# 后端注册表（UI 数据源）：builtin=True 自带网络搜索 / False 需外部 / None 无在线检索
BACKENDS: list[dict[str, Any]] = [
    {"id": "deepseek_tool", "label": "DeepSeek 内置联网搜索", "builtin": True,
     "note": "自带：官方 Responses API web_search 工具（服务端托管搜索，无需第三方 Key；"
             "deepseek-v4-flash / v4-pro / v4-flash-vision-exp）"},
    {"id": "zhipu_tool", "label": "智谱 GLM 网络搜索", "builtin": True,
     "note": "自带：专用 Web Search API（open.bigmodel.cn/api/paas/v4/web_search；"
             "用你的智谱 Key；检索计费见官网）"},
    {"id": "qwen_tool", "label": "通义千问联网搜索", "builtin": True,
     "note": "自带：DashScope enable_search（2026-08 官方：qwen3-max 系列已支持联网；"
             "现行代际至 Qwen3.8 Max/Plus/Flash 均可。用你的千问 Key）"},
    {"id": "bocha", "label": "博查 AI 搜索", "builtin": False,
     "note": "外部搜索 API（独立计费，需博查 Key）；自定义 OpenAI 兼容端点选这个"},
    {"id": "manual", "label": "手动粘贴", "builtin": None,
     "note": "终极兜底：自行粘贴标题/URL/文本，不上传任何检索词"},
]

# 自带搜索 ↔ 所选服务商自动匹配；其余（bocha/manual）均属「需外部/无」
BUILTIN_BACKEND_BY_PROVIDER = {"zhipu": "zhipu_tool", "qwen": "qwen_tool",
                               "deepseek": "deepseek_tool"}   # 自动匹配
EXTERNAL_BACKENDS = {"bocha"}                                 # 需外部搜索
NO_SEARCH_BACKENDS = {"manual"}                               # 无在线检索


class SearchError(Exception):
    pass


def _clip(s: str, n: int = SNIPPET_LIMIT) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _banned(url: str) -> bool:
    return any(h in url.lower() for h in BANNED_HOSTS)


def _is_trusted(url: str, trusted_domains: Optional[list[str]] = None) -> bool:
    """WP-6：可信来源判定——内置后缀/域名白名单 + 用户自定义域名（精确/二级子域）。"""
    host = (url or "").lower()
    for scheme in ("https://", "http://"):
        if host.startswith(scheme):
            host = host[len(scheme):]
    host = host.split("/")[0].split(":")[0].split("?")[0].strip(".")
    if not host:
        return False
    domains = set(TRUSTED_DOMAINS)
    domains.update(str(d or "").strip().lower() for d in (trusted_domains or []) if str(d or "").strip())
    for d in domains:
        if d and (host == d or host.endswith("." + d)):
            return True
    for s in TRUSTED_SUFFIXES:
        if host == s or host.endswith("." + s):
            return True
    return False


def trusted_filter(materials: list[dict[str, Any]], trusted_only: bool = False,
                   trusted_domains: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """WP-6：标记 trusted 并按「可信优先」排序；trusted_only=True 时过滤掉不可信条目。"""
    for m in materials:
        m["trusted"] = _is_trusted(m.get("url", ""), trusted_domains)
    if trusted_only:
        materials = [m for m in materials if m.get("trusted")]
    materials.sort(key=lambda m: not bool(m.get("trusted")))
    return materials


def _dedup(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for m in materials:
        key = (m.get("url") or "").strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def _urls_from_text(text: str) -> list[str]:
    return re.findall(r"https?://[^\s\"'）)\]]+", text or "")


# ---------------------------------------------------------------- 后端（真实调用）
def search_bocha(query: str, api_key: str, count: int = 10) -> list[dict[str, Any]]:
    """博查 AI Web 搜索：https://docs.bochaai.com/（Bearer Key，返回 data.webPages.value）。"""
    if not api_key:
        raise SearchError("未配置博查 API Key（在「② 新建课题」检索设置或设置页填入）")
    with httpx.Client(timeout=MAX_HTTP_TIMEOUT) as c:
        r = c.post(BOCHA_URL,
                   headers={"Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json"},
                   json={"query": query, "summary": True, "count": count})
        r.raise_for_status()
        data = r.json()
    pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
    out = []
    for p in pages:
        url = (p.get("url") or "").strip()
        if not url or _banned(url):
            continue
        out.append({"title": _clip(p.get("name") or "", 80),
                    "url": url,
                    "snippet": _clip(p.get("summary") or p.get("snippet") or "")})
    return out


def search_zhipu(query: str, api_key: str, model: str = "glm-5.3") -> list[dict[str, Any]]:
    """智谱专用 Web Search API（2026-08 官方文档核查）：
    POST https://open.bigmodel.cn/api/paas/v4/web_search
    body {search_query, search_engine: search_std|search_pro|..., search_intent, count}
    → search_result[{title, content, link, media, icon, refer, publish_date}]
    """
    if not api_key:
        raise SearchError("未配置智谱 API Key")
    with httpx.Client(timeout=MAX_HTTP_TIMEOUT) as c:
        r = c.post(ZHIPU_WEB_SEARCH_URL,
                   headers={"Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json"},
                   json={"search_query": query, "search_engine": "search_std",
                         "search_intent": False, "count": 10})
        r.raise_for_status()
        data = r.json()
    if data.get("error"):
        raise SearchError(str(data["error"]))
    out: list[dict[str, Any]] = []
    for sr in (data.get("search_result") or []):
        url = (sr.get("link") or sr.get("url") or "").strip()
        if not url or _banned(url):
            continue
        out.append({"title": _clip(sr.get("title") or "", 80), "url": url,
                    "snippet": _clip(sr.get("content") or sr.get("snippet") or ""),
                    "media": sr.get("media", "")[:20]})
    return out


def _collect_urls(obj: Any, out: list[dict[str, Any]]) -> None:
    """递归收集形如 {url:...} 的结果节点（DeepSeek web_search_call 的 action 结构兜底解析）。"""
    if isinstance(obj, dict):
        url = obj.get("url") or obj.get("link")
        if isinstance(url, str) and url.startswith("http") and not _banned(url):
            out.append({"title": _clip(obj.get("title") or obj.get("name") or "", 80),
                        "url": url.rstrip(".,;"),
                        "snippet": _clip(obj.get("snippet") or obj.get("summary")
                                         or obj.get("content") or "")})
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _collect_urls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_urls(v, out)


def _normalize_deepseek_model(model: Optional[str]) -> str:
    """v0.5：DeepSeek 内置检索仅支持 v4 系列；非 v4（如旧默认 deepseek-chat）→ 回退 v4-flash。"""
    return model if model and str(model).startswith("deepseek-v4") else "deepseek-v4-flash"


def _push(out: list[dict[str, Any]], url: str, title: str = "",
          snippet: str = "") -> None:
    """去重追加一条素材（拦截空/非 http/被禁域名）。"""
    url = (url or "").strip().rstrip(".,;")
    if not url.startswith("http") or _banned(url):
        return
    if any(o.get("url") == url for o in out):
        return
    out.append({"title": _clip(title or "", 80), "url": url,
                "snippet": _clip(snippet or "")})


def search_deepseek(query: str, api_key: str, model: str = "deepseek-v4-flash") -> list[dict[str, Any]]:
    """DeepSeek 内置联网搜索（2026-08 官方文档核查）：
    官方 Responses API（POST https://api.deepseek.com/responses）web_search 工具，
    服务端托管搜索，无需第三方搜索 Key；仅 deepseek-v4 系列。

    2026-09-01 实测响应结构（补强提取）：
    - output[] 项 type=web_search_call → action:{type, url?} —— 带结果 URL（#ws_call_id 片段）；
      部分 call 的 action 仅有 queries（检索规划记录，无结果）；
    - type=message → content[] output_text（annotations[].url_citation 或正文 URL）。
    对消息段扫描注解与正文 URL（去重），不再只依赖「首个无结果的消息」兜底。
    """
    if not api_key:
        raise SearchError("未配置 DeepSeek API Key")
    # v0.5 防御：非 v4 系列模型（如旧默认 deepseek-chat）会 400 → 回退 deepseek-v4-flash
    model = _normalize_deepseek_model(model)
    body = {
        "model": model or "deepseek-v4-flash",
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": query}]}],
        "tools": [{"type": "web_search", "name": "web_search"}],
        "tool_choice": {"type": "web_search"},
        "stream": False,
    }
    with httpx.Client(timeout=MAX_HTTP_TIMEOUT) as c:
        r = c.post(DEEPSEEK_RESPONSES_URL,
                   headers={"Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json"},
                   json=body)
        if r.status_code == 400:  # 兼容工具版本变体
            body["tools"] = [{"type": "web_search_2025_08_26", "name": "web_search"}]
            r = c.post(DEEPSEEK_RESPONSES_URL,
                       headers={"Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json"},
                       json=body)
        r.raise_for_status()
        data = r.json()
    out: list[dict[str, Any]] = []
    items = data.get("output") or []
    for it in items:
        if it.get("type") == "web_search_call":
            _collect_urls(it.get("action") or {}, out)
        elif it.get("type") == "message":
            # ① annotations（annotations[].url_citation → url/title/snippet）
            for p in (it.get("content") or []):
                if not isinstance(p, dict):
                    continue
                for an in (p.get("annotations") or []):
                    if isinstance(an, dict) and str(an.get("url") or "").startswith("http"):
                        _push(out, an.get("url"),
                              an.get("title") or an.get("url_text") or "",
                              an.get("snippet") or an.get("text") or "")
            # ② 正文中的裸 URL（不含被禁域名；差量去重）
            text = "".join(str(p.get("text", "")) for p in (it.get("content") or [])
                           if isinstance(p, dict))
            for url in _urls_from_text(text):
                _push(out, url, query, text)
    # 去重兜底：action.url 带 #ws_call_id 片段、message 正文引用无片段——
    # 按「去片段」URL 合并（存储也一并去除 API 追踪片段），避免同一结果重复展示
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for m in out:
        key = (m.get("url") or "").split("#")[0].rstrip(".,;")
        if key in seen:
            continue
        seen.add(key)
        m["url"] = key
        merged.append(m)
    return merged


def search_qwen(query: str, api_key: str, model: str = "qwen-plus") -> list[dict[str, Any]]:
    """通义千问 DashScope 原生 enable_search（2026-08 官方文档核查）：
    messages + parameters.enable_search=True, search_options.enable_source=True
    → output.search_info.search_results[{index, title, url, site_name, ...}]
    注意：需支持联网搜索的模型（2026-08 官方：qwen3-max 系列已支持，现行代际至 Qwen3.8）。
    """
    if not api_key:
        raise SearchError("未配置通义千问 API Key")
    with httpx.Client(timeout=MAX_HTTP_TIMEOUT) as c:
        r = c.post(QWEN_URL,
                   headers={"Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json"},
                   json={"model": model or "qwen-plus",
                         "input": {"messages": [{"role": "user", "content": f"检索：{query}"}]},
                         "parameters": {"enable_search": True, "temperature": 0.3,
                                        "search_options": {"enable_source": True}},
                         "stream": False})
        r.raise_for_status()
        data = r.json()
    if (data.get("code") or 0) != 0:
        raise SearchError(str(data.get("message") or "调用失败"))
    out = []
    info = ((data.get("output") or {}).get("search_info") or {})
    results = info.get("search_results") or []
    # OpenAI 兼容模式结构回退
    if not results:
        results = ((data.get("choices") or [{}])[0].get("message") or {}
                   ).get("search_results") or []
    for sr in results:
        url = (sr.get("url") or "").strip()
        if not url or _banned(url):
            continue
        out.append({"title": _clip(sr.get("title") or "", 80), "url": url,
                    "snippet": _clip(sr.get("content") or sr.get("snippet") or ""),
                    "site": (sr.get("site_name") or "")[:20]})
    if not out:
        text = ((data.get("output") or {}).get("text") or "")
        for url in _urls_from_text(text):
            if _banned(url):
                continue
            out.append({"title": _clip(query, 60), "url": url.rstrip(".,;"),
                        "snippet": _clip(text)})
    return out


def parse_manual(text: str) -> list[dict[str, Any]]:
    """手动词条：一行「标题 URL」或「URL」或任意文本行 → 素材快照。"""
    lines = (text or "").splitlines()
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        urls = _urls_from_text(ln)
        if urls:
            title = ln[: ln.find("http")].strip(" ·：:") or ln[:40]
            for u in urls:
                if _banned(u):
                    continue
                out.append({"title": _clip(title, 80), "url": u.rstrip(".,;"),
                            "snippet": _clip(ln)})
        elif len(ln) >= 8:
            out.append({"title": "", "url": "", "snippet": _clip(ln)})
    return out


def resolve_backend(backend: str, provider: str, bocha_key: str) -> str:
    """后端解析（2026-08 核查版）：
    - auto：按所选服务商自动匹配（智谱/千问/DeepSeek 均自带 → 工具式；否则 bocha→manual）
    - deepseek / deepseek_tool：DeepSeek 自带联网搜索（Responses API）
    """
    if backend and backend != "auto":
        if backend == "deepseek":   # 兼容旧保存值
            return "deepseek_tool"
        return backend
    if provider in BUILTIN_BACKEND_BY_PROVIDER:
        return BUILTIN_BACKEND_BY_PROVIDER[provider]
    return "bocha" if bocha_key else "manual"


def build_backend_fn(backend: str, api_key: str, model: str,
                     ) -> Callable[[str], list[dict[str, Any]]]:
    """后端 → 可注入的 search_fn（测试可替换）。"""
    if backend == "deepseek" or backend == "deepseek_tool":
        return lambda q: search_deepseek(q, api_key, model or "deepseek-v4-flash")
    if backend == "zhipu_tool":
        return lambda q: search_zhipu(q, api_key, model or "glm-5.3")
    if backend == "qwen_tool":
        return lambda q: search_qwen(q, api_key, model or "qwen-plus")
    if backend == "bocha":
        return lambda q: search_bocha(q, api_key)
    # manual 兜底：无 search_fn 时返回空（由上层提示用户粘贴）
    raise SearchError("手动粘贴模式不需要在线检索；请直接在「手动素材」粘贴内容")


# ---------------------------------------------------------------- 多轮循环（LLM 驱动）
def _llm_json(client: Any, system: str, user: str) -> dict[str, Any]:
    """调用 LLM 并稳健解析 JSON（失败返回空 dict）。"""
    try:
        out = client.chat_json([{"role": "system", "content": system},
                                {"role": "user", "content": user}], temperature=0.3)
        return out if isinstance(out, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _gen_queries(client: Any, subject: str, chapter: str, keywords: str,
                 round_no: int, prev_summary: str) -> list[str]:
    system = ("你是医学考试资料检索策划。只输出 JSON：{\"queries\": [\"检索词\", ...]}。"
              "检索词应覆盖：考纲要求、历年真题、指南/共识；简洁（≤20字）。")
    if round_no == 1:
        user = (f"科目：{subject}\n章节：{chapter}\n教师重点关键词：{keywords[:300]}\n"
                "请给出 ≤3 条检索词。")
    else:
        user = (f"科目：{subject}\n章节：{chapter}\n教师重点关键词：{keywords[:300]}\n"
                f"已检索结果摘要：\n{prev_summary[:1200]}\n"
                "请审阅缺口（如：数值标准未覆盖、某年真题缺失），给出 ≤2 条补充检索词。")
    data = _llm_json(client, system, user)
    qs = data.get("queries") or []
    return [str(q).strip()[:40] for q in qs if str(q).strip()][:QUERIES_PER_ROUND]


def _conflict_flags(client: Any, subject: str, slices_digest: str,
                    materials: list[dict[str, Any]]) -> list[int]:
    """Round 3：LLM 核查冲突（检索到的答案/数值 vs 教材切片）→ 返回 conflict 索引。"""
    if not materials:
        return []
    payload = [{"idx": i, "title": m.get("title"), "url": m.get("url"),
                "snippet": m.get("snippet")} for i, m in enumerate(materials)]
    system = ("你是医学资料一致性核查员。比对【教材切片】与【网络检索素材】，"
              "只输出 JSON：{\"conflict\": [素材序号]}——"
              "仅当网络素材的答案/数值与教材切片**直接矛盾**时列入；"
              "教材未覆盖的内容不应标记冲突。")
    user = (f"科目：{subject}\n教材切片（节选）：\n{slices_digest[:2500]}\n\n"
            f"网络检索素材：\n{payload}")
    data = _llm_json(client, system, user)
    idxs = data.get("conflict") or []
    return [int(i) for i in idxs if isinstance(i, (int, float)) or str(i).isdigit()]


def run_search_rounds(client: Any, subject: str, chapter: str, keywords: str,
                      backend: str, api_key: str = "", model: str = "",
                      search_fn: Optional[Callable[[str], list[dict[str, Any]]]] = None,
                      slices_digest: str = "",
                      cancel: Optional[Any] = None,
                      max_rounds: int = ROUNDS_MAX,
                      trusted_only: bool = False,
                      trusted_domains: Optional[list[str]] = None) -> dict[str, Any]:
    """多轮检索主循环（LLM 驱动，≤3 轮）。search_fn 可注入（测试/离线）。"""
    def _trusted(mats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return trusted_filter(mats, trusted_only=trusted_only,
                              trusted_domains=trusted_domains)
    if cancel is not None and cancel.is_set():
        return {"materials": [], "logs": ["已取消"], "errors": []}
    logs: list[str] = []
    errors: list[str] = []
    materials: list[dict[str, Any]] = []
    fn = search_fn
    if fn is None:
        try:
            fn = build_backend_fn(backend, api_key, model)
        except SearchError as e:
            return {"materials": [], "logs": [str(e)], "errors": [str(e)]}

    def _do_search(query: str) -> list[dict[str, Any]]:
        try:
            return fn(query) or []
        except Exception as e:  # noqa: BLE001 单后端错误隔离
            errors.append(f"[{backend}] 检索失败：{e}")
            logs.append(f"  ⚠️ 检索失败：{e}")
            return []

    # Round 1：考纲/真题/指南 三路检索词
    q1 = _gen_queries(client, subject, chapter, keywords, 1, "")
    if not q1:
        q1 = [f"{subject} {chapter} 考试大纲", f"{subject} 真题 {chapter}", f"{chapter} 诊疗指南"]
    logs.append(f"① 检索词：{q1}")
    for q in q1:
        if cancel is not None and cancel.is_set():
            break
        for m in _do_search(q):
            m["round"] = 1
            materials.append(m)
        time.sleep(0.2)

    # Round 2：缺口补充
    if cancel is not None and cancel.is_set():
        logs.append("⏹ 已取消")
        return {"materials": _dedup(materials)[:MATERIALS_LIMIT], "logs": logs, "errors": errors}
    prev = "\n".join(f"- {m.get('title', '')}: {m.get('snippet', '')[:80]}"
                     for m in _dedup(materials)[:MATERIALS_LIMIT])
    q2 = _gen_queries(client, subject, chapter, keywords, 2, prev)
    if max_rounds >= 2 and q2:
        logs.append(f"② 补充检索词：{q2}")
        for q in q2[:QUERIES_ROUND2]:
            if cancel is not None and cancel.is_set():
                break
            for m in _do_search(q):
                m["round"] = 2
                materials.append(m)
            time.sleep(0.2)

    materials = _dedup(materials)[:MATERIALS_LIMIT]
    # WP-6：可信标记 + 可信优先排序（trusted_only 时先过滤再排序）
    before_trusted = len(materials)
    materials = _trusted(materials)
    if trusted_only and len(materials) < before_trusted:
        logs.append(f"⚠️ 仅保留可信来源：过滤掉 {before_trusted - len(materials)} 条不可信素材")
    logs.append(f"已收集 {len(materials)} 条素材（去重后"
                + ("，可信优先" if any(m.get("trusted") for m in materials) else "") + "）")

    # Round 3：conflict 核查
    if max_rounds >= 3 and materials and slices_digest:
        conflict = _conflict_flags(client, subject, slices_digest, materials)
        if conflict:
            for c in conflict:
                if 0 <= c < len(materials):
                    materials[c]["conflict"] = True
            logs.append(f"③ 冲突核查：{len(conflict)} 条与教材矛盾（已标记，不自动改写）")
    return {"materials": materials, "logs": logs, "errors": errors}


def digest_for_prompt(materials: list[dict[str, Any]]) -> str:
    """进入 MedGen 的参考素材文本（题目引用标注 [源:网 URL]）。"""
    if not materials:
        return ""
    lines = ["## 网络检索参考素材（考纲/真题/指南，供选题与答案校准；"
             "conflict 条目不得作为正确答案依据）"]
    for i, m in enumerate(materials, 1):
        tag = "【与教材冲突-勿用答案】" if m.get("conflict") else ("【可信】" if m.get("trusted") else "")
        lines.append(f"{i}. {tag}{m.get('title', '')} · {m.get('url', '')}\n"
                     f"   {m.get('snippet', '')}")
    return "\n".join(lines)
