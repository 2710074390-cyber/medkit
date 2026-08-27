"""IMP-06 FTS5+jieba 检索接线测试（G-04 收口 · 决策 (a) 接线）。

覆盖：reindex_slices（SQL/JSON 两态）、分词命中「心衰」→「心力衰竭」、
BM25 排序、科目过滤、JSON 模式回退 bigram、路由 explain/slices 关键词路径。
全部本地零 LLM；存储经 conftest 隔离到 tmp。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import medkit.main as m
from medkit.core import config as cfgmod
from medkit.core import db as dbs
from medkit.core import explain as expl


@pytest.fixture
def fts_env(tmp_path, monkeypatch):
    """SQL 模式 + 一个内科学项目（两个教材切片）+ 索引与 FTS 重建。"""
    proj = tmp_path / "projects" / "p1"
    proj.mkdir(parents=True)
    (proj / "meta.json").write_text(json.dumps({"subject": "内科学"}, ensure_ascii=False),
                                    encoding="utf-8")
    slices = [
        {"sid": "S001", "title": "心衰概述", "role": "textbook",
         "text": "急性心力衰竭是临床急危重症，以左心衰竭为主，表现为肺淤血、呼吸困难。"
                 "治疗以利尿、扩血管为主。",
         "source": "内科学教材", "page": "p120"},
        {"sid": "S002", "title": "慢阻肺", "role": "textbook",
         "text": "慢性阻塞性肺疾病以持续气流受限为特征，与吸烟关系密切。",
         "source": "内科学教材", "page": "p231"},
    ]
    (proj / "slices.json").write_text(json.dumps(slices, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(expl, "_PROJ_ROOT", tmp_path / "projects")
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    cfgmod.CONFIG_FILE.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
    dbs.migrate()
    expl.index_slices()
    return tmp_path


def _cand(subject: str = "内科学") -> list[dict]:
    return expl._load_index().get("subjects", {}).get(subject, [])


# ---------------------------------------------------------------- 单元：分词与 MATCH 表达式
def test_fts_tokens_bigram_and_jieba():
    toks = dbs.fts_tokens("急性心力衰竭治疗")
    assert "心力衰竭" in toks   # jieba 整词
    assert "心力" in toks and "力衰" in toks and "衰竭" in toks   # bigram 兜底
    assert toks == [t.lower() for t in toks]


def test_fts_match_expr_singleton_filtered():
    assert dbs.fts_match_expr("心") == ""          # 单字前缀噪声大 → 空表达式（回退）
    expr = dbs.fts_match_expr("心衰")
    assert '"心衰"*' in expr and expr.count(" OR ") >= 0


def test_reindex_slices_json_mode_noop(tmp_path, monkeypatch):
    # 未建库（JSON 模式）→ 返回 0，不写 FTS
    monkeypatch.setattr(dbs, "DB_PATH", tmp_path / "nope" / "medkit.db")
    assert dbs.reindex_slices([{"subject": "内科学", "text": "x", "title": "t"}]) == 0


# ---------------------------------------------------------------- 检索：FTS 优先
def test_fts_search_token_recall(fts_env):
    """分词命中：「心衰」能匹配「心力衰竭」切片（jieba + bigram 兜底）。"""
    hits = expl.fts_search("内科学", "心衰", 5, _cand())
    assert hits and hits[0]["title"] == "心衰概述"


def test_fts_search_ranking_and_subject_filter(fts_env):
    hits = expl.fts_search("内科学", "急性心力衰竭", 5, _cand())
    assert hits and hits[0]["title"] == "心衰概述"
    assert all(h["title"] != "慢阻肺" for h in hits)     # 不相关切片不召回
    # 科目不匹配（FTS 有该科目数据但查询科目无）→ None（调用方回退）
    assert expl.fts_search("外科学", "心衰", 5, _cand()) is None


def test_fts_search_no_hit_returns_none(fts_env):
    assert expl.fts_search("内科学", "失语症", 5, _cand()) is None


# ---------------------------------------------------------------- 回退：JSON 模式 bigram
def test_retrieve_fallback_when_db_absent(fts_env, monkeypatch):
    monkeypatch.setattr(dbs, "DB_PATH", fts_env / "gone" / "medkit.db")
    hits = expl.retrieve(subject="内科学", query="心力衰竭")
    assert any(h["title"] == "心衰概述" for h in hits)


def test_retrieve_subject_mismatch_still_falls_back(fts_env):
    hits = expl.retrieve(subject="外科学", query="心衰")
    assert any(h["title"] == "心衰概述" for h in hits)


# ---------------------------------------------------------------- 路由：explain/slices 关键词
def test_router_explain_slices_uses_fts(fts_env):
    client = TestClient(m.app, base_url="http://127.0.0.1")
    r = client.get("/api/library/explain/slices",
                   params={"subject": "内科学", "query": "心衰"})
    assert r.status_code == 200
    j = r.json()
    assert j["slices"] and j["slices"][0]["title"] == "心衰概述"
    # 无命中 → 空列表（FTS 无命中回退 bigram 后仍空）
    r2 = client.get("/api/library/explain/slices",
                    params={"subject": "内科学", "query": "不存在的关键词xyz"})
    assert r2.json()["slices"] == []
