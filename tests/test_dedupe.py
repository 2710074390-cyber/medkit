"""R4-01：进程内「在飞去重」契约——同 key 在飞期间 begin 返回 True，end 后放行。

这是流式讲解/提问避免并发双击双扣费的底层机制；
真正的「锁持有期绑定流生命周期」由路由层 gen()/finally 保证（见 test_explain_stream / test_tutor_stream）。
"""

from medkit.core import dedupe


def test_begin_end_release():
    key = "explain:甲科目|知识点X|"
    assert dedupe.begin(key) is False   # 首次获取到锁
    assert dedupe.begin(key) is True    # 同 key 在飞 → 重复提交
    dedupe.end(key)
    assert dedupe.begin(key) is False   # 释放后放行
    dedupe.end(key)


def test_distinct_keys_independent():
    k1, k2 = "tutor-start:甲|A|", "tutor-start:乙|B|"
    assert dedupe.begin(k1) is False
    assert dedupe.begin(k2) is False    # 不同 key 互不阻塞
    assert dedupe.begin(k1) is True
    dedupe.end(k1)
    dedupe.end(k2)


def test_exclusive_end_idempotent():
    key = "explain:x"
    dedupe.begin(key)
    dedupe.end(key)
    dedupe.end(key)   # 幂等，不报错
    assert dedupe.begin(key) is False
    dedupe.end(key)
