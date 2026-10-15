"""中文分词共享基建（`db.fts_tokens`）的守卫。

`fts_tokens` 是**跨域共享**的分词口径：vault 的错题检索（`vault_index`）复用同一份实现
（jieba 词 + CJK 二元组兜底），所以它随讲解域删除后**仍然必须留下**。

2026-10-15（奥卡姆精简 T2）：本文件原来还覆盖 `explain.fts_search` / `reindex_slices` /
`fts_match_expr` / 路由 `explain/slices`——这些**只服务于教材切片 FTS 表**，
随讲解域删除（实测全仓零调用）。留下的只有「分词」这一层共享能力。

全部本地零 LLM；存储经 conftest 隔离到 tmp。
"""

from __future__ import annotations

from medkit.core import db as dbs


def test_fts_tokens_bigram_and_jieba():
    toks = dbs.fts_tokens("急性心力衰竭治疗")
    assert "心力衰竭" in toks   # jieba 整词
    assert "心力" in toks and "力衰" in toks and "衰竭" in toks   # bigram 兜底
    assert toks == [t.lower() for t in toks]


def test_fts_tokens_jieba_unavailable_falls_back(monkeypatch):
    """NX-02（R-3）：打包环境 jieba 缺失/词典损坏 → 仅 bigram 兜底，绝不抛错。

    打包后的 exe 里 jieba 词典可能缺失；分词是 vault 检索的入口，
    抛错会让「搜错题」整条链路 500，所以兜底必须存在且**被断言到**。
    """
    import sys

    monkeypatch.setitem(sys.modules, "jieba", None)  # import jieba → ImportError
    toks = dbs.fts_tokens("急性心力衰竭治疗")
    assert "心力衰竭" not in toks
    assert "心力" in toks and "力衰" in toks and "衰竭" in toks
