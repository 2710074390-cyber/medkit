"""D4 守卫：知识点别名表（`core/kp_aliases.py`）+ 与 `errsearch` 的集成。

## 这个守卫在防什么

别名表最容易坏的方式**不是**「写错一个词」，而是**结构性质被破坏**：

| 结构性质 | 破坏后果 | 为什么难发现 |
|---|---|---|
| **对称性**（组是等价关系） | 记录写全称、查缩写能中；反过来中不了 | 只测一个方向会全绿 |
| **无冗余**（成员不是子串关系） | 多一条永远用不上的映射 = 多一处漂移源 | 表能用，只是脏了 |
| **同词只属一组** | 展开歧义（同一词两组，选哪个？） | 查询仍返回结果，只是不稳定 |
| **不稀释覆盖率** | 别名成员拍平进 token 列表 ⇒ 精确匹配的排序被拉低 | 结果集没变，只是**顺序**悄悄变差 |

⇒ 本文件**不锁字面条目数**（那是魔数锁定，加一组词就要改断言），
而是锁**函数级结构性质** + **行为**（走 `search()` 真身）。

## 判据设计（可证伪）

- 每条结构断言都配 `_mutate` 系列辅助**自证能命中**（构造一个违反性质的样本，
  断言检测器判红）——否则结构断言可能是恒绿的。
- 行为断言走 `search()` 真身（不是 `expand()`），因为「别名能召回」这件事
  只有端到端才算数；`expand()` 返回对但 `search()` 没接上 = 假绿。

运行：`pytest tests/test_kp_aliases.py -q`（零网络 · 零文件）
"""
from __future__ import annotations

import pytest

from medkit.core import errsearch as es
from medkit.core import kp_aliases as ka

# ------------------------------------------------------------------ 结构：表本身

def test_groups_are_nonempty_and_stripped():
    """每组至少 1 个成员，成员非空、无首尾空白。"""
    assert ka.ALIAS_GROUPS, "别名表为空 ⇒ 整块功能静默消失"
    for gi, group in enumerate(ka.ALIAS_GROUPS):
        assert group, f"组 {gi} 为空"
        for m in group:
            assert m == m.strip(), f"组 {gi} 成员 {m!r} 有首尾空白"
            assert m, f"组 {gi} 含空成员"


def test_no_duplicate_member_across_groups():
    """同一成员**只能属于一个组**——否则 `expand()` 的选择有歧义。

    模块 import 时已 fail-fast（`_INDEX` 构建会抛 ValueError），
    这里再断言一次，是为了让「有人把 fail-fast 改成 warning」时**守卫仍在**。
    """
    seen: dict[str, int] = {}
    for gi, group in enumerate(ka.ALIAS_GROUPS):
        for m in group:
            key = m.strip().lower()
            assert key not in seen, (
                f"成员 {m!r} 同时在组 {seen.get(key)} 与组 {gi}")
            seen[key] = gi


def test_symmetry_holds_for_every_member():
    """**对称性**：组内任意成员 `alias_of` 都返回同一个完整的组。

    这是「双向召回」的结构保证——若哪天有人把表改成单向映射，这条会红。
    """
    for group in ka.ALIAS_GROUPS:
        for m in group:
            got = ka.alias_of(m)
            assert set(got) == set(group), (
                f"{m!r} 的等价组 {got} 不等于其所在组 {group}（对称性被破坏）")


def test_no_member_shadows_another():
    """**无冗余（正确判据）**：组内**不得**存在一个成员，含另一个成员为**子串**。

    ## 推导（我前两版都写错了，这里给出最终正确的）

    成员的召回面 = 「所有含该成员的文本」。设 `a` 含 `b` 为子串，
    则「文本含 `a`」⟹「文本含 `b`」⟹ **`a` 的召回面 ⊆ `b` 的召回面**
    ⟹ `a` 不新增任何召回 ⟹ **`a` 冗余，应删**（保留 `b` 这一更短的形态）。

    ⚠️ **注意方向**：是**长串冗余**，不是短串。`心肌梗死 ⊂ 急性心肌梗死`
    ⇒ 删「急性心肌梗死」（短串 `心肌梗死` 能命中含长串的文本）。

    ⚠️ **`心衰` 不是子串**：`"心衰" in "心力衰竭"` 为 **False**（前两字「心力」）
    ⇒ 两者都留。这正是我前面**两版都搞错**的地方：
    第一版写「不得互为子串」（太松，漏掉单向冗余），
    举例又用了 `("心力衰竭","心衰")`（**根本不是子串关系**，把「检测器没错」误读成「检测器恒绿」）。
    """
    for gi, group in enumerate(ka.ALIAS_GROUPS):
        for a in group:
            for b in group:
                if a == b:
                    continue
                assert b.lower() not in a.lower(), (
                    f"组 {gi} ({group[0]}): 「{a}」含子串「{b}」⇒ 「{a}」冗余"
                    "（其召回面被「{b}」完全覆盖，应删）".replace("{b}", b))


def test_shadowing_detector_actually_fires():
    """元守卫：把**真冗余**（长串含短串）注入合成组，检测器必须判红。

    真冗余样本：`("急性心肌梗死", "心肌梗死")` —— 长串含短串，长串该删。
    """
    def _shadowed(group):
        for a in group:
            for b in group:
                if a != b and b.lower() in a.lower():
                    return True
        return False

    assert _shadowed(["急性心肌梗死", "心肌梗死"]) is True, \
        "检测器漏判冗余（长串含短串）"
    # 心衰 / 心力衰竭 **不是**子串关系 ⇒ 不该判冗余（我第一版的错误样本）
    assert _shadowed(["心力衰竭", "心衰"]) is False, \
        "误判：心衰 不是 心力衰竭 的子串（前两字是「心力」）"
    assert _shadowed(["copd", "慢阻肺"]) is False


def test_shadowing_detector_matches_real_recall_sets():
    """**行为级**自证：检测器的判据与「召回面包含」这一语义**真的一致**（不只是同名）。

    做法：对若干成员对，先按检测器判「是否遮蔽」，再实测召回面是否真含
    —— 两者必须一致，否则检测器守的不是它声称的东西。
    """
    def _shadowed(a: str, b: str) -> bool:
        return b.lower() in a.lower()

    def _recall(a: str, texts: list[str]) -> set[str]:
        return {t for t in texts if a in t}

    texts = ["急性心肌梗死处理", "心肌梗死定位", "心衰失代偿", "心力衰竭机制"]
    cases = [("急性心肌梗死", "心肌梗死"), ("心衰", "心力衰竭"),
             ("心力衰竭", "心衰"), ("心肌梗死", "急性心肌梗死")]
    for a, b in cases:
        detected = _shadowed(a, b)
        actual = _recall(a, texts) <= _recall(b, texts)   # a 的召回面 ⊆ b 的
        assert detected == actual, (
            f"检测器判 {a!r} 被 {b!r} 遮蔽={detected}，"
            f"但实测召回面包含关系={actual}（判据与语义不符）")


def test_symmetry_detector_actually_fires():
    """元守卫：单向映射必须被对称性检测器判红。"""
    def _is_symmetric(mapping: dict[str, tuple[str, ...]]) -> bool:
        for k, vs in mapping.items():
            for v in vs:
                if k not in mapping.get(v, ()) and v != k:
                    return False
        return True

    assert _is_symmetric({"copd": ("copd", "慢阻肺"),
                          "慢阻肺": ("copd", "慢阻肺")}) is True
    assert _is_symmetric({"copd": ("copd", "慢阻肺"),
                          "慢阻肺": ("慢阻肺",)}) is False, "单向映射未被判红"


# ------------------------------------------------------------------ 结构：expand / match_query 契约

def test_expand_preserves_order_and_length():
    """`expand` 契约：输出与输入**等长逐位对应**（无别名 token → 单元素组）。"""
    got = ka.expand(["copd", "湿啰音", "氧疗"])
    assert len(got) == 3
    assert got[0] == list(ka.alias_of("copd")), "别名词未展开成整组"
    assert got[1] == ["湿啰音"], "非别名词不应被改动"
    assert got[2] == ["氧疗"]


def test_expand_dedupes_same_group():
    """同组的多个 token 只保留一次——否则覆盖率会被重复计。"""
    got = ka.expand(["copd", "慢阻肺", "COPD"])
    assert len(got) == 1, f"同组多 token 未去重：{got}"
    assert got[0] == list(ka.alias_of("copd"))


def test_expand_unknown_terms_pass_through_unchanged():
    """正面断言：**非别名词必须原样透传**（防止「一律展开成空」把普通检索掏空）。"""
    for t in ["湿啰音", "氧疗", "后壁导联", "xyz_not_an_alias"]:
        assert ka.expand([t]) == [[t]], f"{t!r} 被别名表改动了"


def test_alias_of_unknown_returns_self():
    """无别名 → 只含自身的元组（不是空、不是 None）。"""
    assert ka.alias_of("湿啰音") == ("湿啰音",)
    assert ka.alias_of("") == ()


def test_match_query_only_hits_whole_string():
    """`match_query` 只在**整串**等于某成员时命中（子串不算）。"""
    assert ka.match_query("COPD") == list(ka.alias_of("copd"))
    assert ka.match_query("copd") == list(ka.alias_of("copd"))
    assert ka.match_query(" 慢阻肺 ") == list(ka.alias_of("慢阻肺")), "未做空白归一"
    assert ka.match_query("慢阻肺急性加重") is None, "长串不该整串命中"
    assert ka.match_query("") is None


# ------------------------------------------------------------------ 行为：端到端召回（走 search 真身）

# 夹具：记录**只含全称/常用形**，查询**只含缩写/简称**（纯别名场景）
_CARDS = [
    {"id": "1", "subject": "内科",
     "question": "慢性阻塞性肺疾病急性加重期氧疗原则", "error_tag": "概念混淆"},
    {"id": "2", "subject": "内科",
     "question": "急性心肌梗死的定位诊断", "error_tag": "知识点不清"},
    {"id": "3", "subject": "内科",
     "question": "高血压合并糖尿病的降压目标", "error_tag": "数值记错"},
    {"id": "4", "subject": "内科",
     "question": "心房颤动患者的心率控制目标", "error_tag": "概念混淆"},
]


@pytest.mark.parametrize("query, expect_id", [
    # 英文缩写 ↔ 中文全称（核心场景，此前 0 命中）
    ("COPD", "1"), ("copd", "1"), ("慢性阻塞性肺疾病", "1"), ("慢性阻塞性肺病", "1"),
    ("AMI", "2"), ("心梗", "2"), ("心肌梗死", "2"), ("急性心肌梗死", "2"),
    ("DM", "3"), ("糖尿病", "3"), ("HTN", "3"), ("高血压", "3"),
    ("AF", "4"), ("房颤", "4"), ("心房颤动", "4"),
])
def test_alias_recall_both_directions(query, expect_id):
    """**双向**：无论查缩写还是全称，都必须召回对应记录。

    这是 D4 的**主判据**——此前 `COPD`/`慢阻肺`/`心梗` 一律 0 命中。
    """
    got = [i["id"] for i in es.search(_CARDS, query)["items"]]
    assert expect_id in got, f"「{query}」未召回 card {expect_id}（实得 {got}）"


@pytest.mark.parametrize("short", ["慢阻肺", "心衰", "心梗", "房颤", "高血压"])
def test_alias_covers_chinese_short_form_tokenized_by_jieba(short):
    """中文简称会被 jieba 切碎（「慢阻肺」→ 慢阻/阻肺），`query_tokens` 须兜住。

    判据：查中文简称时，token 列表里**必须出现用户输入的整串**——
    否则它会被二元组打散，覆盖率分母被撑大。
    """
    assert ka.match_query(short) is not None, f"{short!r} 不在别名表里"
    toks = es.query_tokens(short)
    assert short in toks, (
        f"查「{short}」时整词未进 token（实得 {toks}）⇒ jieba 切碎后无法查表")


def test_alias_does_not_dilute_coverage():
    """**别名表不得稀释覆盖率**（`kp_aliases` docstring 的核心承诺）。

    构造：一张精确匹配的卡 + 一张只沾了噪音 bigram 的卡。
    若别名成员被**拍平**追加进 token 列表，总 token 数膨胀 ⇒ 精确匹配那张的
    覆盖率也被拉低，两张卡可能并列 ⇒ 排序变差。按**组**计则不会。
    """
    cards = [
        {"id": "exact", "subject": "内科",
         "question": "慢性阻塞性肺疾病稳定期吸入治疗", "error_tag": "x"},
        {"id": "noise", "subject": "内科",
         "question": "慢性咳嗽的鉴别诊断", "error_tag": "x"},
    ]
    res = es.search(cards, "慢性阻塞性肺疾病")
    ids = [i["id"] for i in res["items"]]
    assert ids and ids[0] == "exact", f"精确匹配未排首（实得 {ids}）"
    covs = {i["id"]: i["coverage"] for i in res["items"]}
    assert covs["exact"] == 1.0, f"精确匹配覆盖率应满（实得 {covs['exact']}）"
    assert covs["exact"] > covs["noise"], "精确匹配覆盖率未与噪音拉开"


def test_xinshuai_is_a_real_alias_not_substring():
    """**勘误钉住**：`心衰` **不是** `心力衰竭` 的子串 ⇒ 它**必须**在别名表里。

    2026-10-02 实测 `"心衰" in "心力衰竭"` 为 **False**（前两字是「心力」）。
    此前 `errsearch.py` / `kp_aliases.py` / `db.fts_tokens` 三处文档都把它记成
    「子串能命中、无需别名」——**三处都错**。本条把这个事实钉死，
    防止有人再把「心衰不需别名」当结论写回文档。

    同时正面断言：**端到端真的能召回**（记录只写全称、查询只写简称）。
    """
    assert "心衰" not in "心力衰竭", (
        "若此断言失败说明 Python 语义变了，整条推理要重做")
    assert "心衰" in {m for g in ka.ALIAS_GROUPS for m in g}, (
        "心衰 是别名表的典型场景，必须收录（它不是子串，靠子串匹配中不了）")
    cards = [{"id": "h", "subject": "内科",
              "question": "心力衰竭失代偿期的处理", "error_tag": "x"}]
    assert [i["id"] for i in es.search(cards, "心衰")["items"]] == ["h"], \
        "查「心衰」未召回只写「心力衰竭」的记录 ⇒ 别名表没接上"


def test_single_direction_substring_does_not_need_alias():
    """**单向**子串场景：`心肌梗死` ⊂ `急性心肌梗死` ⇒ 查短串能子串命中长文本。

    即「别名表不是唯一召回途径」——子串匹配独立成立。若哪天有人把
    `心肌梗死` 从表里删掉，查 `心肌梗死` 仍应命中含长串的记录（子串路径）。
    """
    # 前提：查长串命中不了只写短串的文本（否则本用例测不到「单向」这一点）
    assert "急性心肌梗死" not in "心肌梗死的定位诊断", "前提变了"
    # 方向正确的那一侧：查**短串**、文本是**长串** ⇒ 子串命中成立
    cards2 = [{"id": "n", "subject": "内科",
               "question": "急性心肌梗死的定位诊断", "error_tag": "x"}]
    assert [i["id"] for i in es.search(cards2, "心肌梗死")["items"]] == ["n"], \
        "单向子串（短串查长文本）应能命中"


def test_non_alias_query_behaviour_unchanged():
    """**非别名查询的行为与改动前逐字相同**（回归护栏）。

    判据：对每个非别名词，`expand` 出来是单元素组、`search` 结果集不变。
    """
    cards = [
        {"id": "1", "subject": "内科", "question": "肺部湿啰音的听诊特点", "error_tag": "x"},
        {"id": "2", "subject": "内科", "question": "后壁心肌梗死的导联选择", "error_tag": "x"},
    ]
    for q in ["湿啰音", "后壁", "听诊"]:
        toks = es.query_tokens(q)
        groups = ka.expand(toks)
        assert all(len(g) == 1 for g in groups), (
            f"非别名查询 {q!r} 的 token 被展开成组（不该）：{groups}")
    assert [i["id"] for i in es.search(cards, "湿啰音")["items"]] == ["1"]
    assert [i["id"] for i in es.search(cards, "后壁")["items"]] == ["2"]


def test_snippet_uses_matched_member_not_alias():
    """片段必须用**实际命中的成员**定位（拿别名去原文 `find` 会失败）。

    判据：查 `COPD`（记录里只有「慢性阻塞性肺疾病」）时，
    片段里必须含**记录里的原词**，而不是 `COPD`。
    """
    res = es.search(_CARDS, "COPD")
    assert res["count"] == 1
    snippet = res["items"][0]["snippet"]
    assert "慢性阻塞性肺疾病" in snippet, (
        f"片段未用命中原词定位（实得 {snippet!r}）")
