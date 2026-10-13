"""模型目录（三层来源合并）与「获取模型列表」的守卫。

## 这批用例在防什么

2026-10-02 实测出三件真事，每一件都对应下面的一组用例：

1. **上游返回富元数据，MedKit 全丢了**——`llm.list_models_detailed` 必须
   保留 `model_dump()` 的全部字段（含落在 `model_extra` 的私有扩展）。
2. **「默认选最新」是假承诺**——前端文案写「最新」，实现却只是 `list[0]`。
   现在默认项由 `modelcatalog.pick_default` 给出，并**返回理由**供 UI 使用；
   本文件的 `test_no_unbacked_claims_in_ui` 是**防复发**守卫：UI 里出现
   「最新」措辞就必须有对应实现，否则红。
3. **视觉能力有权威字段却猜名字**——`deepseek-flash` 官方声明支持图像，
   却因名字没有 `vision`/`vl` 被误判 ⇒ 白跑一次 OCR。

## 假绿防范（照 `gate-falsifiability`）

- **不测「函数返回了东西」测「返回的东西对不对」**：`merge` 的每条断言都
  落到具体字段值（`ctx_label == "1M"`、`caps.vision is True`），不是 `assert items`。
- **`None`（不知道）与 `False`（确定不支持）必须能区分**：`_modalities_caps`
  的用例专门钉这一点——把 `None` 写成 `False` 会让「字段缺失」误判成
  「确定不支持视觉」，而这两者的后续行为完全不同。
- **降级路径要有正面断言**：`source_ok=False` 时**仍然**必须有条目，
  且必须带 `msg`（不许静默）——两个方向都量，不只量「失败了」。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from medkit.core import modelcatalog as mc

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ============================================================ 格式化
@pytest.mark.parametrize("value,expect", [
    (1_048_576, "1M"),      # 恰好 1M（厂商宣称口径）
    (2_097_152, "2M"),
    (393_216, "384K"),
    (204_800, "200K"),
    (131_072, "128K"),
    (262_144, "256K"),
    (8_192, "8K"),
    (500, "500"),           # 不足 1K 原样
    (0, ""),                # 无值不显示
    (None, ""),
    ("", ""),
    ("abc", ""),
])
def test_format_ctx_golden_values(value, expect):
    """**黄金值**锁定格式化——不是「返回了非空串」这种恒真断言。"""
    assert mc.format_ctx(value) == expect


def test_format_ctx_does_not_round_to_lie():
    """四舍五入不许把不同容量压成同一个显示值。

    `1M` 与 `1.5M` 必须能区分；`1024*1024` 与 `1024*1024+1` 允许同显示
    （差 1 token 无意义），但 `1.5M` 不许显示成 `2M`。
    """
    assert mc.format_ctx(1_572_864) == "1.5M"      # 1.5 * 1024 * 1024
    assert mc.format_ctx(1_048_576) != mc.format_ctx(1_572_864)


# ============================================================ 能力判定：None vs False
def test_modalities_caps_distinguishes_missing_from_negative():
    """**`None`（不知道）≠ `False`（确定不支持）** —— 本模块最容易被改坏的一处。

    把 `None` 写成 `False` 的后果：服务商没给这个字段时，
    被当成「确定不支持视觉」⇒ 直接走 OCR（多一次联网调用），
    而正确行为是**继续往下一层找**（目录 / 名字启发式）。
    """
    assert mc._modalities_caps(["text", "image"]) is True
    assert mc._modalities_caps(["text"]) is False           # 给了字段、明确没有 image
    assert mc._modalities_caps(None) is None                # 没给 → 不知道
    assert mc._modalities_caps("text,image") is None        # 类型不对 → 不知道（不是 False）
    assert mc._modalities_caps({}) is None
    assert mc._modalities_caps(["TEXT", "IMAGE"]) is True   # 大小写不敏感


def test_caps_of_upstream_overrides_catalog():
    """上游字段**优先于**内置目录（服务商自己说的比我们记的准）。

    注入式验证：目录里 `deepseek-v4-pro` 记为不支持视觉，
    但若上游说 `input_modalities` 含 image，则**必须**以上游为准。
    """
    entry = mc._catalog_entry("deepseek-v4-pro", "deepseek")
    assert entry and entry["caps"]["vision"] is False, "目录基线变了，先更新本用例的前提"

    up = {"id": "deepseek-v4-pro", "input_modalities": ["text", "image"]}
    assert mc.caps_of("deepseek-v4-pro", "deepseek", up)["vision"] is True


def test_caps_of_falls_back_to_catalog_then_name():
    """三层降级顺序：上游 → 目录 → 名字启发式。每次只**去掉一层**看行为是否下沉。"""
    # ① 只有名字线索（不在目录里）
    assert mc.caps_of("my-super-vision-model", "custom", None)["vision"] is True
    # ② 上游明确说不支持 ⇒ 即使名字像也不认（上游最权威）
    up_neg = {"input_modalities": ["text"]}
    assert mc.caps_of("my-super-vision-model", "custom", up_neg)["vision"] is False
    # ③ 目录命中即用目录（deepseek-flash 名字不带 vision，目录/上游说带）
    assert mc.caps_of("deepseek-flash", "deepseek", None)["vision"] is True


def test_caps_unknown_vision_defaults_conservative():
    """认不出就 False（保守方向）——与 `vision.py` 的正面判据一致。

    反了（认不出当支持）的代价是用户传图吃 400；现在的代价只是多走一次 OCR。
    """
    assert mc.caps_of("eval-model", "custom", None)["vision"] is False
    assert mc.caps_of("vlsi-parser", "custom", None)["vision"] is False


# ============================================================ merge
_UPSTREAM_FIXTURE = [
    {"id": "deepseek-flash", "name": "DeepSeek-V4.1-Flash",
     "context_window": 1_048_576, "max_output_tokens": 393_216,
     "input_modalities": ["text", "image"], "effort": {"default_level": "high"}},
    {"id": "deepseek-v4-pro", "name": "DeepSeek-V4-Pro",
     "context_window": 1_048_576, "max_output_tokens": 393_216,
     "input_modalities": ["text"], "effort": {"default_level": "high"}},
    {"id": "deepseek-v5-exp", "name": "V5 Exp",
     "context_window": 2_097_152, "input_modalities": ["text", "image"]},
]


def test_merge_marks_sources_for_grouping():
    """`source` 决定前端分组：目录里有且上游确认 = `both`；上游独有 = `remote`。"""
    items = {it["id"]: it for it in mc.merge("deepseek", _UPSTREAM_FIXTURE)}
    assert items["deepseek-flash"]["source"] == "both"
    assert items["deepseek-v4-pro"]["source"] == "both"
    assert items["deepseek-v5-exp"]["source"] == "remote"


def test_merge_uses_upstream_fields_over_catalog():
    """显示名/上下文**以服务商实际返回为准**（上游优先）。"""
    items = {it["id"]: it for it in mc.merge("deepseek", _UPSTREAM_FIXTURE)}
    assert items["deepseek-flash"]["name"] == "DeepSeek-V4.1-Flash"   # 不是目录里的 "DeepSeek V4.1 Flash"
    assert items["deepseek-flash"]["ctx_label"] == "1M"
    assert items["deepseek-v5-exp"]["ctx_label"] == "2M"


def test_merge_keeps_catalog_order_for_catalog_entries():
    """目录内条目保持**目录顺序**（= 新鲜度序）——这是 `pick_default` 的唯一依据。"""
    ids = mc.ids_of(mc.merge("deepseek", _UPSTREAM_FIXTURE))
    cat_ids = [m["id"] for m in mc.catalog_for("deepseek")]
    assert ids[:len(cat_ids)] == cat_ids, (ids, cat_ids)


def test_merge_without_upstream_returns_catalog():
    """**离线可用**：不传上游数据（或传空）时仍返回内置目录条目。"""
    items = mc.merge("deepseek", None)
    assert [it["id"] for it in items] == [m["id"] for m in mc.catalog_for("deepseek")]
    assert all(it["source"] == "catalog" for it in items)
    assert all(it["ctx_label"] for it in items), "目录条目必须自带上下文（否则展示为空）"


def test_merge_tolerates_malformed_upstream():
    """上游数据脏（缺 id / 非 dict）不许炸，也不许把坏条目塞进结果。"""
    items = mc.merge("custom", [
        {"id": "good-model"},
        {"no_id": True},
        "just-a-string",
        None,
        {"id": ""},
    ])
    assert [it["id"] for it in items] == ["good-model"]


def test_merge_unknown_provider_is_not_empty_if_upstream_has_data():
    """自定义端点（`custom` 无目录）全靠上游——**必须能列出东西**。"""
    items = mc.merge("custom", [{"id": "gpt-4o-mini"}, {"id": "my-model"}])
    assert [it["id"] for it in items] == ["gpt-4o-mini", "my-model"]
    assert all(it["source"] == "remote" for it in items)


def test_merge_name_falls_back_to_id():
    """上游不给 `name`（多数 OpenAI 兼容端点如此）⇒ `name == id`。

    前端据此决定要不要渲染第二行——**同一串显示两遍**是明确的坏体验。
    """
    items = mc.merge("custom", [{"id": "plain-model"}])
    assert items[0]["name"] == items[0]["id"] == "plain-model"


# ============================================================ pick_default
def test_pick_default_respects_saved():
    """**对齐 A-新4**：切服务商/重拉列表都不得静默覆盖用户手填的模型名。"""
    items = mc.merge("deepseek", _UPSTREAM_FIXTURE)
    got, reason = mc.pick_default(items, saved="deepseek-v5-exp")
    assert got == "deepseek-v5-exp"
    assert reason == "已保存"


def test_pick_default_saved_survives_absence():
    """已保存的模型**不在**新列表里时也要原样返回（前端会把它作为附加项追加）。

    这条防的是「用户手填了一个私有模型名，点一次『获取模型列表』就被换掉」。
    """
    items = mc.merge("deepseek", _UPSTREAM_FIXTURE)
    got, reason = mc.pick_default(items, saved="my-private-model")
    assert got == "my-private-model"
    assert reason == "已保存"


def test_pick_default_prefers_catalog_first_entry():
    """目录内第一条 = 推荐档。**理由文案必须是「推荐档」而不是「最新」**
    （见 `modelcatalog` docstring：`created` 实测为 null，「最新」算不出来）。"""
    items = mc.merge("deepseek", _UPSTREAM_FIXTURE)
    got, reason = mc.pick_default(items, saved="")
    assert got == mc.catalog_for("deepseek")[0]["id"]
    assert reason == "推荐档"
    assert "最新" not in reason


def test_pick_default_reason_is_honest_for_custom_endpoint():
    """目录为空时只能取上游第一条 ⇒ **理由必须照实说**，不许沿用「推荐档」。

    这是「文案与动作一致」在代码层的落点——文案由 reason 驱动，
    改了动作就必须改文案，否则用例红。
    """
    items = mc.merge("custom", [{"id": "a-model"}, {"id": "b-model"}])
    got, reason = mc.pick_default(items, saved="")
    assert got == "a-model"
    assert reason == "服务商列表首项"
    assert reason != "推荐档", "自定义端点没有目录，不能声称「推荐档」"


def test_pick_default_empty():
    assert mc.pick_default([], saved="") == ("", "")


# ============================================================ 视觉：真漏判已闭合
def test_vision_authoritative_source_closes_deepseek_flash_misjudgment():
    """**本批最重要的一条**（2026-10-02 实测的真漏判）。

    `deepseek-flash` 官方 `input_modalities` 含 `"image"`，但名字里
    既没有 `vision` 也没有 `vl` ⇒ 只靠名字会判 False ⇒ **白跑一次 OCR**。
    现在目录提供权威判据，必须判 True。

    2026-10-13（奥卡姆精简 P2）：判据由 `core/vision.py` 下沉到本模块
    （`looks_like_vision`），**口径不变**——目录优先、名字兜底。
    """
    assert mc.looks_like_vision("deepseek-flash") is True
    assert mc.caps_of("deepseek-flash", "deepseek", None)["vision"] is True


def test_vision_negative_from_catalog_is_trusted():
    """目录明确写 `vision: False` 的（deepseek-v4-pro）不许被名字启发式翻盘。"""
    assert mc.looks_like_vision("deepseek-v4-pro") is False
    assert mc.caps_of("deepseek-v4-pro", "deepseek", None)["vision"] is False


def test_vision_name_heuristic_still_works_for_unknown_models():
    """目录未收录的模型仍走名字启发式——**改造不许把老能力砍掉**。"""
    for name, expect in (("qwen-vl-max", True), ("gpt-4o", True),
                         ("my-vision-model", True),
                         ("eval-model", False), ("vlsi-parser", False)):
        assert mc.looks_like_vision(name) is expect, name
        assert mc.caps_of(name, "custom", None)["vision"] is expect, name


def test_vision_catalog_lookup_has_no_circular_import():
    """`modelcatalog` ↔ 视觉判据曾是**循环依赖**（靠函数内延迟 import 打破）。

    2026-10-13（P2）把判据并入本模块后循环消失。判据：本模块可独立 import，
    且目录查表真的能命中。
    """
    assert mc._catalog_entry("deepseek-flash", "")["caps"]["vision"] is True
    assert mc._catalog_entry("deepseek-v4-pro", "")["caps"]["vision"] is False
    assert mc._catalog_entry("never-heard-of-it", "") is None   # 目录没有 → 不知道


def test_catalog_entry_matches_across_providers():
    """同一模型被别家转售（provider=custom）时也要能认出来。

    按服务商找会落空（custom 无目录），但模型名完全一致 ⇒ 我们**确实知道**它的能力。
    """
    assert mc._catalog_entry("deepseek-v4-pro", "custom") is not None
    assert mc.caps_of("deepseek-v4-pro", "custom", None)["vision"] is False


# ============================================================ 目录自身健全性
def test_catalog_entries_are_well_formed():
    """结构守卫：目录条目必须有 id/name/tier，且 **id 在服务商内不重复**。

    重复 id 会让 `merge` 产出两条同 id 条目 ⇒ 下拉里出现两个同名项，
    且 `pick_default` 的「目录顺序」语义失真。
    """
    for pid, entries in mc.CATALOG.items():
        ids = [m["id"] for m in entries]
        assert len(ids) == len(set(ids)), f"{pid} 目录内 id 重复：{ids}"
        for m in entries:
            assert m.get("id"), f"{pid} 有条目缺 id"
            assert m.get("name"), f"{pid}.{m.get('id')} 缺 name（下拉要显示它）"
            assert m.get("tier"), f"{pid}.{m['id']} 缺 tier"
            assert isinstance(m.get("caps"), dict), f"{pid}.{m['id']} 缺 caps"


def test_catalog_covers_every_preset_provider_with_default_model():
    """每个**预置服务商**的 `default_model` 必须在目录里 —— 否则会出现
    「默认模型在下拉里找不到、显示成『已保存』」这种自相矛盾的状态。"""
    from medkit.core.providers import PROVIDERS

    for p in PROVIDERS:
        dm = p.get("default_model") or ""
        if not dm:
            continue   # custom 无默认模型
        found = mc._catalog_entry(dm, p["id"])
        assert found is not None, (
            f"服务商 {p['id']} 的 default_model={dm!r} 不在目录里——"
            "补进 modelcatalog.CATALOG（否则首屏默认模型会被标成「已保存」）"
        )


def test_catalog_for_returns_copy():
    """`catalog_for` 必须给副本：调用方就地改动不许污染模块级常量。"""
    a = mc.catalog_for("deepseek")
    a[0]["name"] = "TAMPERED"
    assert mc.catalog_for("deepseek")[0]["name"] != "TAMPERED"


# ============================================================ 防复发：文案与实现绑定
def _ui_claims_latest() -> bool:
    """UI 里是否出现「默认选最新」这类**承诺**措辞（扫控件文本，不扫注释）。"""
    src = (ROOT / "medkit" / "web" / "js" / "review-desk.js").read_text(encoding="utf-8")
    # 剥注释：多行 /* */ 先于单行 //
    src = src.replace("//", "\n//")            # 保守：先断开，避免 // 在字符串里误判
    src = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("//"))
    for claim in ("默认选最新", "默认选中最新", "默认选最新模型"):
        if claim in src:
            return True
    return False


def test_no_unbacked_claims_in_ui():
    """**防复发守卫（本方案的自省项）**。

    2026-10-02 发现：`review-desk.js` 写「已加载 N 个模型，默认选最新」，
    而实现是 `value = saved || list[0]`（服务商返回顺序第一条，**无任何排序**）。
    README 同款。**两处声明、零处实现、零处守卫。**

    光把文案改对只是治标。这条把「承诺」与「实现」绑起来：
    出现「最新」措辞 ⇒ 实现里必须存在真正的排序或目录查序调用。

    ⚠️ 本用例**不得 skip**（R30/`gate-falsifiability`：skip 会让用例静默消失，
    CI 里 skip 与 pass 同退出码）。故写成**双向判定**：无论 UI 有没有该措辞，
    本用例都给出一个确定的绿/红——有措辞就必须有实现背书，无措辞则显式记录
    「承诺已撤下」这个事实（用断言表达，而不是 skip 掉）。
    """
    src = (ROOT / "medkit" / "web" / "js" / "review-desk.js").read_text(encoding="utf-8")
    backed = any(tok in src for tok in (".sort(", "pick_default", "default_reason"))
    claims = _ui_claims_latest()
    if claims:
        assert backed, (
            "UI 里有「默认选最新」这类承诺，但实现中找不到任何排序/目录查序/"
            "后端默认项取用（`.sort(` / `pick_default` / `default_reason`）。\n"
            "要么把承诺改成能兑现的说法（如「推荐档」），要么真的实现「最新」判定。"
        )
    else:
        # 无「最新」承诺是**期望态**：此时必须确实走了「推荐档」口径，
        # 否则等于「承诺删了、替代口径也没上」——比原来更糟。
        assert backed, (
            "UI 已撤下「默认选最新」措辞，但也没看到任何替代默认口径"
            "（`pick_default` / `default_reason` / `.sort(`）——"
            "承诺删了却没给能兑现的说法，属于回归。"
        )


def test_backend_default_is_used_by_frontend():
    """前端必须**采用**后端给的默认项，而不是自己再挑一次 `list[0]`。

    自己挑 = 两处口径，迟早漂移（后端「推荐档」与前端「第一条」会不一致）。
    """
    src = (ROOT / "medkit" / "web" / "js" / "review-desk.js").read_text(encoding="utf-8")
    assert "r.default" in src, "前端未取用后端 `default`"
    assert "default_reason" in src, "前端未取用后端 `default_reason`（理由文案会与动作脱节）"


def test_frontend_calls_models_endpoint_with_provider():
    """前端调 `/api/llm/models` 必须带 `provider`。

    不带 ⇒ 后端只能从配置里猜（`cfg.load().provider`），
    而用户可能正在切换服务商但还没保存 ⇒ 拿到的是**上一个**服务商的目录。
    """
    src = (ROOT / "medkit" / "web" / "js" / "review-desk.js").read_text(encoding="utf-8")
    i = src.find('api("/api/llm/models"')
    assert i >= 0, "找不到模型列表调用点"
    seg = src[i:i + 500]
    assert "provider" in seg, "调用未带 provider（切换中的服务商会拿错目录）"


# ============================================================ 结构守卫（AST）
def test_router_keeps_backward_compatible_models_field():
    """`models: list[str]` **必须保留**（旧前端 / 旧测试依赖它）。

    AST 判据：`llm_models` 的返回字典里要有 `models` 键——
    用 AST 而不是文本子串，避免注释里写 `"models"` 就算数。
    """
    tree = ast.parse((ROOT / "medkit" / "routers" / "config.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "llm_models")
    keys: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    assert "models" in keys, "响应体丢了向后兼容的 `models` 字段"
    assert "items" in keys, "响应体缺少富条目 `items`"
    assert "default_reason" in keys, "响应体缺少默认项理由（文案与动作须同源）"


def test_router_uses_detailed_lister():
    """路由必须调 `list_models_detailed`（只取 id 的旧路径会丢元数据）。

    **打了旧名字的测试会静默失效**——这正是本用例存在的理由：
    失败注入打在 `list_models` 上，路由却调 `list_models_detailed`，
    注入根本没进被测路径，用例变成测空气。
    """
    src = (ROOT / "medkit" / "routers" / "config.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "llm_models")
    called = {
        n.func.attr for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "list_models_detailed" in called, "路由未走保留元数据的 list_models_detailed"


def test_list_models_detailed_reads_full_dump():
    """`list_models_detailed` 必须读 `model_dump()`（含 `model_extra` 私有扩展）。

    AST 判据：函数体里存在 `model_dump` 调用。
    只读 `m.id` 的话，`context_window` / `input_modalities` 全丢——
    整个「展示最新可用模型」就退化成「展示一串 id」。
    """
    tree = ast.parse((ROOT / "medkit" / "core" / "llm.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "list_models_detailed")
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "model_dump" in attrs, "未读 model_dump —— 服务商扩展字段（含视觉能力）会全部丢失"


def test_list_models_still_returns_plain_ids():
    """旧 API `list_models()` 的**契约不变**（`list[str]`）——下游与测试都依赖它。"""
    tree = ast.parse((ROOT / "medkit" / "core" / "llm.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "list_models")
    ret = next(n for n in ast.walk(fn) if isinstance(n, ast.Return))
    # 返回的是列表推导 → 元素必须是 `m.get("id")` 这类取 id 的表达式
    assert isinstance(ret.value, ast.ListComp), "list_models 的返回形态变了（应为 id 列表推导）"
