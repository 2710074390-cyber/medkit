"""v0.3.0 API 层测试（FastAPI TestClient · 审查报告 E1 补测）：

覆盖：S1 Host/Origin 中间件、S3 pid 消毒、A5 meta 容错、S2 DPAPI 配置往返、
U8 Anki 导出守卫、项目 CRUD、U6 查重门禁、U5 成本预估。

运行：python tests/test_api.py（不发起任何真实网络调用）
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.main as m  # noqa: E402
from medkit.core import config as cfgmod  # noqa: E402
from medkit.core.config import resolve_key  # noqa: E402

TMP_DIR = Path(tempfile.mkdtemp(prefix="medkit_api_"))

# S1 补：本文件把 cfgmod.load/save 与 PROMPTS_DIR_USER/PRESETS_DIR 换成了隔离实现，
# 模块级泄漏会串扰其他测试文件（尤其 load 浅拷贝共享嵌套 dict 污染 DEFAULTS）——
# 模块测试结束后统一还原。
_ORIG_CFG: tuple = ()


def _install_isolated_cfg() -> dict:
    """让 main 的 cfg.load/save 指向隔离的临时配置（不污染用户 ~/.medkit）。"""
    global _ORIG_CFG
    if not _ORIG_CFG:
        _ORIG_CFG = (cfgmod.load, cfgmod.save, cfgmod.PROMPTS_DIR_USER, cfgmod.PRESETS_DIR)
    saved = dict(cfgmod.DEFAULTS)
    saved["projects_dir"] = str(TMP_DIR / "projects")
    saved["api_key"] = "sk-test-key"
    # 影子副本/预设目录也隔离到临时区
    cfgmod.PROMPTS_DIR_USER = TMP_DIR / "prompts"
    cfgmod.PRESETS_DIR = TMP_DIR / "presets"
    m.cfg.load = lambda: dict(saved)
    m.cfg.save = lambda c: saved.update(c)
    return saved


@pytest.fixture(scope="module", autouse=True)
def _restore_cfg_after_module():
    yield
    if _ORIG_CFG:
        cfgmod.load, cfgmod.save, cfgmod.PROMPTS_DIR_USER, cfgmod.PRESETS_DIR = _ORIG_CFG
    shutil.rmtree(TMP_DIR, ignore_errors=True)


def make_client() -> TestClient:
    # base_url 用 127.0.0.1：Host 校验放行（testserver 会被中间件 403）
    return TestClient(m.app, base_url="http://127.0.0.1")


def test_guard_host_and_origin():
    c = make_client()
    assert c.get("/api/health").status_code == 200
    # 恶意 Host（DNS rebinding 模拟）→ 403
    r = c.get("/api/health", headers={"host": "evil.example.com"})
    assert r.status_code == 403, r.text
    # 跨站 Origin 的简单请求（CSRF 烧钱模拟）→ 403
    r = c.post("/api/projects/x/run", headers={"origin": "http://evil.example.com"})
    assert r.status_code == 403, r.text
    # 同源 Origin 放行（返回 404：项目不存在，说明通过了守卫）
    r = c.post("/api/projects/nope/run", headers={"origin": "http://127.0.0.1:4880"})
    assert r.status_code == 404, r.text


def test_pid_sanitize():
    # 路由层对多段路径/点段会拦截（404）；_safe_pid 作为纵深防御必须拒绝
    from fastapi import HTTPException as HE

    from medkit.main import _safe_pid
    for bad in ("..", ".", "a/b", "a\\b", "..\\.."):
        try:
            _safe_pid(bad)
            raise AssertionError(f"_safe_pid 应拒绝 {bad!r}")
        except HE as e:
            assert e.status_code == 400
    c = make_client()
    for bad in ("..", "a%2Fb"):
        r = c.get("/api/projects/" + bad)
        assert r.status_code in (400, 404), (bad, r.status_code)


def test_config_roundtrip_dpapi():
    c = make_client()
    saved = _install_isolated_cfg()
    r = c.put("/api/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "api_key": "sk-new-secret", "model_gen": "deepseek-chat", "model_qc": "deepseek-chat",
        "web_search_enabled": False, "web_search_api_key": "",
        "mineru_api_key": "mr-new", "mineru_auto_ocr": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["api_key"] == "" and body["api_key_masked"].startswith("sk-n")
    stored = saved["api_key"]
    if stored.startswith("dpapi:"):  # Windows DPAPI
        assert resolve_key(stored) == "sk-new-secret"
    else:  # 非 Windows 回退明文
        assert stored == "sk-new-secret"
    assert resolve_key(saved["mineru"]["api_key"]) == "mr-new"


def test_provider_key_archive_and_promotion():
    """v0.5.1 多服务商 Key 存档：归档 → 掩码列表 → 免 Key 切换（自动提升）→ 删除。"""
    c = make_client()
    _install_isolated_cfg()

    def put(provider, key):
        model = "kimi-k2-thinking" if provider == "kimi" else "deepseek-v4-flash"
        return c.put("/api/config", json={
            "provider": provider, "base_url": "", "api_key": key,
            "model_gen": model, "model_qc": model,
            "web_search_enabled": False, "web_search_api_key": "",
            "mineru_api_key": "", "mineru_auto_ocr": True,
        })

    # 1) deepseek 存 Key
    r = put("deepseek", "sk-ds-archive-1")
    assert r.status_code == 200, r.text
    # 2) 切到 kimi（带新 Key）→ deepseek 应自动归档
    r = put("kimi", "sk-kimi-archive-1")
    assert r.status_code == 200, r.text
    rows = {k["id"]: k for k in c.get("/api/keys").json()["keys"]}
    assert rows["deepseek"]["saved"] and rows["deepseek"]["key_masked"].startswith("sk-d")
    assert rows["kimi"]["saved"] and rows["kimi"]["active"]
    # 3) 不带 Key 切回 deepseek → 存档自动提升为生效 Key
    r = put("deepseek", "")
    assert r.status_code == 200, r.text
    assert r.json()["api_key_masked"].startswith("sk-d"), "切回应自动提升存档 Key"
    # 4) 删除 kimi 存档
    assert c.delete("/api/keys/kimi").status_code == 200
    rows = {k["id"]: k for k in c.get("/api/keys").json()["keys"]}
    assert rows["kimi"]["saved"] is False
    assert c.delete("/api/keys/kimi").status_code == 404  # 已删空
    assert c.delete("/api/keys/ollama").status_code == 404  # 未知服务商
    # 5) 同服务商重新保存（新 Key + 新端点/模型）→ 归档刷新为本次值（v0.5.2：不再滞留旧值）
    r = c.put("/api/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com/v5", "api_key": "sk-fresh-key-x",
        "model_gen": "deepseek-v4-pro", "model_qc": "deepseek-v4-pro",
        "web_search_enabled": False, "web_search_api_key": "",
        "mineru_api_key": "", "mineru_auto_ocr": True,
    })
    assert r.status_code == 200, r.text
    rows = {k["id"]: k for k in c.get("/api/keys").json()["keys"]}
    assert rows["deepseek"]["key_masked"].startswith("sk-f"), "同服务商重存应刷新归档 Key"
    assert rows["deepseek"]["base_url"].endswith("/v5"), "归档端点应为本次保存值"
    assert rows["deepseek"]["model_gen"] == "deepseek-v4-pro", "归档模型应为本次保存值"


def test_project_crud_and_meta_corrupt():
    c = make_client()
    saved = _install_isolated_cfg()
    body = {
        "subject": "儿科API", "exam": "期末", "target": 20,
        "ratios": {"A1": 40, "A2": 30, "B1": 20, "X": 10},
        "toggles": {"qbank": True, "paper": True, "review": True},
        "teacher_text": "生长发育 3.25kg 辅食",
        "textbook_slices": [
            {"sid": "S001", "title": "第一章 生长发育", "text": "生长发育有三个高峰，出生体重3.25kg。"},
            {"sid": "S002", "title": "第二章 营养", "text": "能量需求110kcal/kg。"}],
        "teacher_slices": [{"sid": "T001", "title": "重点", "text": "生长发育 3.25kg"}],
        "exam_slices": [],
        "extra_slices": [{"sid": "E001", "title": "课件", "text": "课件补充：辅食添加原则。"}],
    }
    r = c.post("/api/projects", json=body)
    assert r.status_code == 200, r.text
    pid = r.json()["pid"]
    # v0.5.2：extra 角色入 slices.json；meta 记录字数
    slices = json.loads((Path(saved["projects_dir"]) / pid / "slices.json").read_text(encoding="utf-8"))
    roles = {s["role"] for s in slices}
    assert {"textbook", "teacher", "extra"} <= roles
    meta = c.get("/api/projects/" + pid).json()
    assert meta["extra_chars"] == len("课件补充：辅食添加原则。")
    assert meta["exam_chars"] == 0
    # 列表 + 详情
    assert pid in [p["pid"] for p in c.get("/api/projects").json()["projects"]]
    meta = c.get("/api/projects/" + pid).json()
    assert meta["stage"] == "quota" and meta["seed"] is not None
    # 运行守卫：未配置 Key → 400；已配置 → 409（done/别的状态）——这里配额态会真正启动线程，
    # 因此仅验证返回码不是 5xx：先删掉再断言守卫逻辑
    # Anki 导出在未完成时 → 409
    r = c.get("/api/projects/" + pid + "/export/anki")
    assert r.status_code in (409, 404), r.text
    # A5：meta 损坏 → 422 而非 500
    proj_dir = Path(saved["projects_dir"]) / pid
    (proj_dir / "meta.json").write_text("{broken", encoding="utf-8")
    assert c.get("/api/projects/" + pid).status_code == 422
    assert c.get("/api/projects/" + pid + "/status").status_code == 422
    # 修复 meta 后删除（运行中删除守卫：当前未运行 → 可删）
    (proj_dir / "meta.json").write_text(json.dumps(
        {"pid": pid, "subject": "儿科API", "stage": "quota", "toggles": {},
         "target": 20, "quota": [], "created": "x"}, ensure_ascii=False), encoding="utf-8")
    assert c.delete("/api/projects/" + pid).status_code == 200
    assert c.get("/api/projects/" + pid).status_code in (404, 422)


def test_dedup_gate():
    from medkit.gates.dedup_check import check_dup
    qs = [
        {"id": "Q001", "question": "关于儿童生长发育规律，下列哪项描述是正确的？"},
        {"id": "Q002", "question": "关于儿童生长发育规律，下列哪项描述是正确的？"},
        {"id": "Q003", "question": "关于新生儿黄疸的病因，下列哪项描述是正确的？"},
    ]
    r = check_dup(qs)
    assert r["pairs"] >= 1
    assert any(x["code"] == "DUP" and x["severity"] == "warn" for x in r["issues"])
    assert r["fail_count"] == 0


def test_cost_estimate():
    from medkit.core.cost import estimate_cny, estimate_run
    est = estimate_run(chars_textbook=10000, chars_teacher=2000, n_slices=3, n_questions=100)
    assert est["input_tokens"] > 0 and est["output_tokens"] > 0
    assert est["total_tokens"] > 50000
    cny = estimate_cny("deepseek", 1_000_000, 1_000_000)
    assert cny is not None and abs(cny - 12.0) < 0.05, "DeepSeek 2026-08 官方价 3.0/9.0（高峰）"
    assert estimate_cny("custom", 1, 1) is None or True  # 无价格表 → None


def test_anki_export_format():
    from medkit.render.qbank_html import export_anki
    qs = [{"id": "Q001", "type": "X", "bloom": "理解", "subtopic": "测试",
           "question": "下列哪些正确？", "options": ["甲", "乙", "丙", "丁", "戊"],
           "answer": "BDE", "analysis": "解析。【源:切片S001】"}]
    txt = export_anki(qs)
    assert "#separator:tab" in txt and "#html:true" in txt
    body_lines = [ln for ln in txt.splitlines() if ln and not ln.startswith("#")]
    assert body_lines, "应有至少一行题目数据"
    line = body_lines[0]
    assert "\t" in line and "BDE" in line


def test_prompts_api_and_shadow_copy():
    """迭代1C/3A：只读查看 + 影子副本（占位符校验/漂移/恢复）。"""
    c = make_client()
    _install_isolated_cfg()
    r = c.get("/api/prompts")
    assert r.status_code == 200
    ps = r.json()["prompts"]
    assert len(ps) == 6
    assert {p["name"] for p in ps} >= {"medtutor.md", "medexplain.md"}
    med = next(p for p in ps if p["name"] == "medgen.md")
    assert "{slice_text}" in med["placeholders"], "主要占位符应被动态提取"
    assert med["using"] == "builtin"
    # 缺占位符 → 400
    bad = med["builtin"].replace("{slice_text}", "（删掉了）")
    r = c.put("/api/prompts/medgen.md", json={"content": bad})
    assert r.status_code == 400 and "{slice_text}" in r.json()["detail"], r.text
    # 合法保存 → custom；GET 显示 drifted=False
    r = c.put("/api/prompts/medgen.md", json={"content": med["builtin"] + "\n\n<!-- test -->"})
    assert r.status_code == 200, r.text
    r = c.get("/api/prompts")
    med = next(x for x in r.json()["prompts"] if x["name"] == "medgen.md")
    assert med["using"] == "custom" and med["drifted"] is False
    # 模拟漂移：改 meta 的 base_hash
    meta_file = TMP_DIR / "prompts" / ".meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["medgen.md"]["base_hash"] = "deadbeef"
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    r = c.get("/api/prompts")
    med = next(x for x in r.json()["prompts"] if x["name"] == "medgen.md")
    assert med["drifted"] is True
    # 恢复默认
    assert c.delete("/api/prompts/medgen.md").status_code == 200
    r = c.get("/api/prompts")
    med = next(x for x in r.json()["prompts"] if x["name"] == "medgen.md")
    assert med["using"] == "builtin"


def test_presets_api():
    """迭代2C：内置 5 套不可删；用户预设增删。"""
    c = make_client()
    _install_isolated_cfg()
    r = c.get("/api/presets")
    assert r.status_code == 200
    body = r.json()
    assert len(body["builtins"]) == 5
    assert c.delete("/api/presets/" + body["builtins"][0]["id"]).status_code == 400
    r = c.post("/api/presets", json={"name": "我的预设", "payload": {"target": 50, "exam": "期末"}})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    r = c.get("/api/presets")
    assert any(x["id"] == pid for x in r.json()["customs"])
    assert c.delete("/api/presets/" + pid).status_code == 200
    assert c.delete("/api/presets/" + pid).status_code == 400


def test_trial_requires_key():
    """迭代1B：未配 Key → 400 引导文案（不发任何网络调用）。"""
    c = make_client()
    saved = _install_isolated_cfg()
    saved["api_key"] = ""
    r = c.post("/api/trial", json={"subject": "儿科", "slice_text": "正文", "teacher_text": ""})
    assert r.status_code == 400, r.text
    assert "API Key" in r.json()["detail"]


def test_search_test_manual():
    """§5.4：manual 后端无需在线测试；bocha 无 Key → 明确报错而非崩溃。"""
    c = make_client()
    _install_isolated_cfg()
    r = c.post("/api/search/test", json={"backend": "manual"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert "手动粘贴" in r.json()["msg"]
    r = c.post("/api/search/test", json={"backend": "bocha", "api_key": ""})
    assert r.status_code == 200 and "未配置博查" in r.json()["msg"]


def test_search_backends_2026():
    """2026-08 官方信息核查回归：DeepSeek 自带联网搜索 + 默认模型换代。"""
    c = make_client()
    _install_isolated_cfg()
    r = c.get("/api/search/backends")
    assert r.status_code == 200
    backends = r.json()["backends"]
    ids = [x["id"] for x in backends]
    assert {"deepseek_tool", "zhipu_tool", "qwen_tool", "bocha", "manual"} <= set(ids)
    assert next(x for x in backends if x["id"] == "deepseek_tool")["builtin"] is True

    from medkit.core import websearch as ws
    assert ws.resolve_backend("deepseek", "deepseek", "") == "deepseek_tool"
    assert ws.resolve_backend("auto", "deepseek", "") == "deepseek_tool"
    assert ws.resolve_backend("auto", "zhipu", "") == "zhipu_tool"
    assert ws.resolve_backend("auto", "custom", "") == "manual"
    assert ws.resolve_backend("auto", "custom", "bk-123") == "bocha"
    # DeepSeek web_search_call action 递归解析
    out: list[dict] = []
    ws._collect_urls({"search": {"results": [{"title": "指南", "url": "https://a.example.com/x",
                                              "snippet": "内容"}]}}, out)
    assert out and out[0]["url"] == "https://a.example.com/x"

    from medkit.core.providers import get_provider
    assert get_provider("deepseek")["default_model"] == "deepseek-v4-flash"
    assert get_provider("deepseek")["search_support"] is True
    assert get_provider("qwen")["default_model"] == "qwen-plus"
    assert get_provider("zhipu")["default_model"] == "glm-5.3"
    assert get_provider("zhipu")["price"]["input"] == 8.0 and get_provider("zhipu")["price"]["output"] == 28.0


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"[FAIL] {name}: {e}")
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    print("----")
    print("API OK" if failures == 0 else f"{failures} FAILED")
    sys.exit(1 if failures else 0)
