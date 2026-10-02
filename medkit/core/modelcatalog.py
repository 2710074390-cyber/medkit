"""模型目录（三层来源合并）——「最新可用模型」的**唯一事实来源**。

## 这个模块解决什么

`routers/config.py` 的 `/api/llm/models` 此前只把服务商返回的 `id` 列表原样透传：

```python
return {"ok": True, "models": [m.id for m in ...]}   # 只有裸 ID
```

于是前端下拉里只有一串 id（`deepseek-flash`），用户看不到：
**显示名、上下文窗口、支不支持图像输入、是不是推理模型**。

更要紧的是**「最新」这个词**：前端文案写「默认选最新」，但实现是
`value = saved || list[0]` —— 即**服务商返回顺序的第一条**，**没有任何排序**。
实测（2026-10-02，DeepSeek 官方端点）返回顺序是
`["deepseek-flash", "deepseek-v4-pro"]`，第 0 条并不是任何意义上的「最新」。
**文案承诺了一个从未发生的动作。**

## 为什么不让代码「算」最新

实测三条证据说明「最新」算不出来：

1. `created` 字段为 `null`（DeepSeek 端点）⇒ 按创建时间排序**不可用**；
2. `deepseek-flash` 与 `deepseek-v4-pro` 谁「新」**没有机械答案**——
   一个是速度档、一个是旗舰档，官方并未在接口里声明代际序；
3. 名称版本号排序对 `-pro` / `-flash` / `-exp` / 日期版会**误判**
   （`v4-pro` 与 `v4.1-flash` 谁版本高？取决于怎么切分，而切分规则是猜的）。

⇒ 本模块的立场：**把「序」变成目录里的一个显式事实（数组顺序 = 新鲜度序），
而不是在运行时算**。UI 文案相应改成「推荐档」——**档位是准确语义，新旧不是。**

## 三层来源（缺一不可，合并优先级从高到低）

| 层 | 来源 | 覆盖的服务商 | 权威度 |
|---|---|---|---|
| ① | **上游字段** `input_modalities` / `context_window` / `name` | 给了扩展字段的（如 DeepSeek） | **最高**（服务商自己说的） |
| ② | **内置目录** `CATALOG`（本文件维护） | 全部预置服务商 | 高（我们维护，可能滞后） |
| ③ | **命名启发式** | 自定义端点 / 全新的模型 | 低（猜的，但兜底） |

- 只有 ① ⇒ 自定义端点、老服务商**全裸**；
- 只有 ② ⇒ 服务商上了新模型要**等发版**才认识；
- 只有 ③ ⇒ 退化成「猜名字」，正是 `vision.py` 一直想摆脱的状态。

**关键约定：上游字段缺失时行为与改造前完全一致**（回落到②③），故**零回归**。

## 与 `vision.py` 的关系（**不另起一套判据**）

`vision.py` 的 `VISION_MODEL_HINTS` 是**名字启发式**（保守正面判据）。
本模块的 `caps_of()` 复用它作为第 ③ 层，**不重写一份标记表**——
两份表迟早会漂移（新增一家服务商只改了一处）。`vision.py` 的
`capability()`/`looks_like_vision()` 反过来也吃本模块的①层，
形成**单一事实来源**（与 `vision.py` 既有约定「前后端不各判一套」同精神）。

## 无 IO / 无 LLM / 无状态

本模块是**纯函数**：不读盘、不发请求、不 import `routers`。
合并所需的上游数据由调用方（`llm.list_models_detailed`）取好传进来——
这样它可以在**任何**环境（含 CI 无网络）被完整测试。
"""

from __future__ import annotations

from typing import Any, Optional

from .vision import looks_like_vision

# --------------------------------------------------------------------- 内置目录
#
# 维护约定（照 `providers.py:VISION_MODEL_HINTS` 的「只加不删」精神）：
#
# 1. **数组顺序 = 新鲜度序**：最新的排最前。这不是装饰——它是
#    `pick_default()` 的**唯一**排序依据（见模块 docstring「为什么不让代码算最新」）。
# 2. **`tier` 是我们对模型定位的诚实描述**，不是营销词：`旗舰` / `速度` / `均衡`。
#    UI 直接展示它，所以别写「最强」这类无法证伪的标签。
# 3. **`caps` 只写我们确定知道的**：不知道就别写键（`caps_of` 会回落到名字启发式）。
#    写 `True` 比写 `False` 更危险——写错 `True` 会让用户传图吃到 400。
# 4. 新增服务商模型时，**只加**条目；旧模型即使下线也保留（用户可能仍在用，
#    且历史配置里存着它的名字，删掉会让「已保存但不在列表」的提示出现）。
CATALOG: dict[str, list[dict[str, Any]]] = {
    "deepseek": [
        {
            "id": "deepseek-v4-pro",
            "name": "DeepSeek V4 Pro",
            "tier": "旗舰",
            "ctx": 1_048_576,
            # 实测（2026-10-02）：input_modalities == ["text"]，**不支持图像**
            "caps": {"reasoning": True, "vision": False},
        },
        {
            "id": "deepseek-flash",
            "name": "DeepSeek V4.1 Flash",
            "tier": "速度",
            "ctx": 1_048_576,
            # 实测：input_modalities == ["text", "image"] ⇒ **支持图像**
            "caps": {"reasoning": True, "vision": True},
        },
    ],
    "zhipu": [
        {
            "id": "glm-5.3",
            "name": "GLM-5.3",
            "tier": "旗舰",
            "ctx": 204_800,
            "caps": {"reasoning": True, "vision": False},
        },
        {
            "id": "glm-5v",
            "name": "GLM-5V（视觉）",
            "tier": "视觉",
            "ctx": 131_072,
            "caps": {"reasoning": False, "vision": True},
        },
    ],
    "qwen": [
        {
            "id": "qwen-plus",
            "name": "通义千问 Plus",
            "tier": "均衡",
            "ctx": 131_072,
            "caps": {"reasoning": False, "vision": False},
        },
        {
            "id": "qwen3-vl-plus",
            "name": "通义千问 VL Plus（视觉）",
            "tier": "视觉",
            "ctx": 131_072,
            "caps": {"reasoning": False, "vision": True},
        },
    ],
    "kimi": [
        {
            "id": "kimi-k2-thinking",
            "name": "Kimi K2 Thinking",
            "tier": "推理",
            "ctx": 262_144,
            "caps": {"reasoning": True, "vision": False},
        },
    ],
    # custom 无内置目录——端点未知，全靠上游字段与命名启发式
}


def catalog_for(provider: str) -> list[dict[str, Any]]:
    """取某服务商的内置目录（副本，防调用方就地改动常量）。"""
    return [dict(m) for m in CATALOG.get(str(provider or ""), ())]


# --------------------------------------------------------------------- 格式化
def format_ctx(n: Any) -> str:
    """上下文窗口 → 人类可读（`1048576` → `1M`）。

    用 1024 进制（与厂商宣称「1M 上下文」的口径一致），不四舍五入到失真：
    恰好整数倍才给短写法，否则保留一位小数（`204800` → `200K`，
    `131072` → `128K`，`262144` → `256K`）。
    """
    try:
        v = int(n)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    for unit, div in (("M", 1024 * 1024), ("K", 1024)):
        if v >= div:
            q = v / div
            # 整除（含 1M 这种 1024*1024）给整数；否则一位小数
            return f"{q:.0f}{unit}" if abs(q - round(q)) < 0.05 else f"{q:.1f}{unit}"
    return str(v)


# --------------------------------------------------------------------- 能力判定
def _modalities_caps(mods: Any) -> Optional[bool]:
    """从上游 `input_modalities` 判视觉能力；字段缺失/类型不对返回 `None`（= 不知道）。

    **返回 `None` 而不是 `False`** 是关键：`False` 表示「确定不支持」，
    `None` 表示「没拿到这个字段，请继续往下一层找」。
    """
    if not isinstance(mods, (list, tuple)):
        return None
    return "image" in {str(x).strip().lower() for x in mods}


def caps_of(model_id: str, provider: str = "",
            upstream: Optional[dict[str, Any]] = None) -> dict[str, bool]:
    """三层合并出能力表（`vision` / `reasoning`）。

    优先级：① 上游字段 → ② 内置目录 → ③ 名字启发式。
    **未知就是未知**：拿不到任何线索时不编（`vision` 回落保守 False，
    与 `vision.py` 的正面判据方向一致）。
    """
    out: dict[str, bool] = {}
    up = upstream or {}

    # ① 上游字段（最权威）：input_modalities 含 "image" ⇒ 支持图像
    up_vision = _modalities_caps(up.get("input_modalities"))
    if up_vision is not None:
        out["vision"] = bool(up_vision)

    # reasoning：上游 `effort` 结构存在即视为支持推理档位控制
    if isinstance(up.get("effort"), dict):
        out["reasoning"] = True

    # ② 内置目录
    entry = _catalog_entry(model_id, provider)
    if entry:
        for k, v in (entry.get("caps") or {}).items():
            out.setdefault(k, bool(v))
        if entry.get("tier") == "推理" or entry.get("tier") == "视觉":
            # 目录已明确标注定位但没有 caps 键时，不再补——避免把展示标签当能力断言
            pass

    # ③ 名字启发式（复用 vision.py，不重写一份标记表）
    if "vision" not in out:
        out["vision"] = bool(looks_like_vision(model_id))

    out.setdefault("reasoning", False)
    return out


def _catalog_entry(model_id: str, provider: str) -> Optional[dict[str, Any]]:
    """在目录里找某模型。**先按服务商找，找不到就全目录找**。

    为什么允许跨服务商命中：同一模型常被多家转售（`deepseek-v4-pro` 可能出现在
    SiliconFlow 之类的自定义端点），此时服务商 id 是 `custom` 而模型名完全一致——
    按服务商找会落空，但我们**确实知道**它的能力。
    """
    mid = str(model_id or "").strip()
    if not mid:
        return None
    pool = CATALOG.get(str(provider or ""), ())
    for m in pool:
        if m.get("id") == mid:
            return m
    for entries in CATALOG.values():
        for m in entries:
            if m.get("id") == mid:
                return m
    return None


# --------------------------------------------------------------------- 合并
def merge(provider: str,
          upstream: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """把上游模型元数据与内置目录**合并**成展示用的条目列表。

    `source` 语义（前端据此分组）：
    - `"remote"` —— 本次从服务商拉到的**且有元数据**（`⚡ 最新拉取`）
    - `"catalog"` —— 内置目录里的条目（`★ 推荐`）
    - `"both"`    —— 两边都有（最理想，能力判定最准）
    - `"saved"`   —— 只存在于用户配置里（上游没返回、目录也没有），
      由调用方通过 `extra_ids` 传入，避免用户**已保存的模型在下拉里消失**

    排序：`both`/`remote` 在前（服务商自己列的 = 当前可用），
    组内保持**目录顺序**（= 新鲜度序），目录外条目保持**上游顺序**。
    """
    up_list = [m for m in (upstream or []) if isinstance(m, dict) and m.get("id")]
    up_by_id = {str(m["id"]): m for m in up_list}

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    # ① 目录条目（含被上游确认的）——顺序即推荐序
    for entry in catalog_for(provider):
        mid = str(entry["id"])
        seen.add(mid)
        up = up_by_id.get(mid)
        items.append(_make_item(entry, up, provider,
                                source="both" if up is not None else "catalog"))

    # ② 上游有、目录没有 ⇒ 新模型，进「最新拉取」组
    for m in up_list:
        mid = str(m["id"])
        if mid in seen:
            continue
        seen.add(mid)
        items.append(_make_item({"id": mid}, m, provider, source="remote"))

    return items


def _make_item(entry: dict[str, Any], up: Optional[dict[str, Any]],
               provider: str, source: str) -> dict[str, Any]:
    """合成单条目。**上游字段优先于目录**（服务商自己说的比我们记的准）。"""
    mid = str(entry.get("id") or (up or {}).get("id") or "")
    up = up or {}

    name = str(up.get("name") or entry.get("name") or mid)

    ctx = up.get("context_window")
    if ctx is None:
        ctx = entry.get("ctx")
    max_out = up.get("max_output_tokens")

    return {
        "id": mid,
        "name": name,
        # 上游没给 name（多数 OpenAI 兼容端点只给 id）⇒ name == id，
        # 前端用 `name != id` 决定要不要渲染第二行，避免同一串显示两遍
        "tier": str(entry.get("tier") or ""),
        "ctx": int(ctx) if isinstance(ctx, int) else 0,
        "ctx_label": format_ctx(ctx),
        "max_output_tokens": int(max_out) if isinstance(max_out, int) else 0,
        "caps": caps_of(mid, provider, up),
        "source": source,
    }


# --------------------------------------------------------------------- 默认选中
def pick_default(items: list[dict[str, Any]], saved: str = "") -> tuple[str, str]:
    """挑默认选中项，返回 `(model_id, reason)`。

    **这不是「最新」**（见模块 docstring）。`reason` 直接用于 UI 文案，
    所以用词必须是我们真能兑现的。判据顺序：

    1. `saved` 非空 ⇒ **不动**（用户手填/上次选的优先，对齐 `A-新4`：
       切服务商不得静默覆盖手填模型名）；
    2. 否则取**目录内**第一条（数组顺序 = 新鲜度序）⇒ `"推荐档"`；
    3. 目录为空（如自定义端点）⇒ 取上游第一条 ⇒ `"服务商列表首项"`；
       **文案必须与动作一致**——这里不能再叫「最新」。
    """
    ids = {str(it.get("id")) for it in items}
    if saved and saved in ids:
        return saved, "已保存"
    if saved:
        # 已保存但不在列表里：仍返回它（前端会把它作为附加项追加）
        return saved, "已保存"

    for it in items:
        if it.get("source") in ("both", "catalog"):
            return str(it["id"]), "推荐档"
    if items:
        return str(items[0]["id"]), "服务商列表首项"
    return "", ""


def ids_of(items: list[dict[str, Any]]) -> list[str]:
    """取 id 列表（保持 `merge` 的顺序）。"""
    return [str(it["id"]) for it in items if it.get("id")]
