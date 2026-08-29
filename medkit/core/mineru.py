"""MinerU 文件解析客户端（扫描件 OCR / 复杂版式 PDF → Markdown）。

双路径：
- 精准解析 API（v4，需用户 Token）：≤200MB / ≤600 页 / 批量 / zip 结果
  （2026-08 官方现行单文件上限：≤200MB / ≤600 页，每日 2000 页高优先级额度）
  POST https://mineru.net/api/v4/file-urls/batch  → PUT 上传 → GET .../extract-results/batch/{batch_id}
- Agent 轻量 API（v1，免 Token，IP 限频）：≤10MB / 约 20 页 / 单文件 / Markdown CDN 直链
  POST https://mineru.net/api/v1/agent/parse/file → PUT 上传 → GET .../parse/{task_id}

文档：https://mineru.net/apiManage/docs
"""

import io
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

BASE = "https://mineru.net/api"
AGENT_FILE_LIMIT_MB = 10
AGENT_PAGE_LIMIT = 20     # 官方对比表字段；错误码 -30003 文案为 50 页，取保守值
V4_FILE_LIMIT_MB = 200
V4_PAGE_LIMIT = 600       # 2026-08 官方现行单文件上限（旧值 200 页过时）

STATE_LABELS = {
    "waiting-file": "等待文件上传",
    "uploading": "文件下载中",
    "pending": "排队中",
    "running": "解析中",
    "converting": "格式转换中",
    "done": "完成",
    "failed": "失败",
}


class MinerUError(Exception):
    """MinerU 解析失败（含可读信息）。"""


class MinerUClient:
    def __init__(self, api_key: str = "", timeout: float = 120.0,
                 poll_interval: float = 5.0,
                 upload_timeout: float = 300.0, poll_timeout: float = 60.0):
        """B34：上传与轮询分开 timeout——上传 300s（大文件）、轮询 60s（查询很轻）。"""
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval

    # ------------------------------------------------------------ 公共
    def mode(self) -> str:
        return "v4" if self.api_key else "agent"

    def test(self) -> tuple[bool, str]:
        """测试连通性：v4 校验 Token（申请上传链接，不实际上传）；agent 校验 IP 限频。"""
        try:
            if self.mode() == "v4":
                with httpx.Client(timeout=30) as c:
                    r = c.post(
                        f"{BASE}/v4/file-urls/batch",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"files": [{"name": "medkit_test.pdf"}],
                              "model_version": "vlm"})
                if r.status_code in (401, 403):
                    return False, f"Token 无效（HTTP {r.status_code}），请在 mineru.net API 管理页确认"
                body = r.json()
                if body.get("code") == 0:
                    return True, "精准解析 API 连接成功（Token 有效）"
                return False, f"精准解析 API 返回：{body.get('msg', r.text[:120])}"
            else:
                with httpx.Client(timeout=30) as c:
                    r = c.post(f"{BASE}/v1/agent/parse/file",
                               json={"file_name": "medkit_test.png"})
                body = r.json()
                if body.get("code") == 0:
                    return True, "轻量 API 连接成功（免 Token，IP 限频）"
                return False, f"轻量 API 返回：{body.get('msg', r.text[:120])}"
        except Exception as e:  # noqa: BLE001
            return False, f"连接失败：{e}"

    def extract(self, file_path: str | Path,
                progress: Optional[Callable[[str], None]] = None,
                cancel: Optional[Any] = None) -> str:
        """上传文件并等待解析，返回 full.md 文本。

        progress: 状态变化回调（中文标签：排队中/解析中/…）
        cancel:   threading.Event，置位后停止轮询并抛「已取消」
        """
        p = Path(file_path)
        size_mb = p.stat().st_size / 1024 / 1024
        pages = self._page_count(p) if p.suffix.lower() == ".pdf" else None

        if self.mode() == "agent":
            if size_mb > AGENT_FILE_LIMIT_MB:
                raise MinerUError(
                    f"文件 {size_mb:.0f}MB 超出轻量 API 限制（{AGENT_FILE_LIMIT_MB}MB）。"
                    f"请到「连接服务商 → OCR」填写 MinerU Token 使用精准 API，或拆分文件")
            if pages and pages > AGENT_PAGE_LIMIT:
                raise MinerUError(
                    f"PDF {pages} 页超出轻量 API 限制（约 {AGENT_PAGE_LIMIT} 页）。"
                    f"请填写 MinerU Token 使用精准 API，或将教材按章节拆分")
            return self._extract_agent(p, progress, cancel)
        if size_mb > V4_FILE_LIMIT_MB:
            raise MinerUError(f"文件超过 {V4_FILE_LIMIT_MB}MB，请拆分后重试")
        if pages and pages > V4_PAGE_LIMIT:
            raise MinerUError(
                f"PDF {pages} 页超出精准 API 限制（{V4_PAGE_LIMIT} 页）。请按章拆分成多个文件"
                f"（也符合「一次一章」的推荐做法）")
        return self._extract_v4(p, progress, cancel)

    # ------------------------------------------------------------ 精准 v4
    def _extract_v4(self, p: Path, progress: Optional[Callable[[str], None]] = None,
                    cancel: Optional[Any] = None) -> str:
        with httpx.Client(timeout=self.upload_timeout) as c:
            r = c.post(
                f"{BASE}/v4/file-urls/batch",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"files": [{"name": p.name, "data_id": f"medkit_{int(time.time())}"}],
                      "model_version": "vlm", "language": "ch"})
            body = self._json_or(r, "申请上传链接失败")
            if body.get("code") != 0:
                raise MinerUError(f"申请上传链接失败：{body.get('msg')}")
            batch_id = body["data"]["batch_id"]
            upload_url = body["data"]["file_urls"][0]

            # B34：上传 PUT 前检查 cancel（取消后不再发起大文件上传）
            if cancel is not None and cancel.is_set():
                raise MinerUError("任务已取消")
            up = c.put(upload_url, content=p.read_bytes(), timeout=self.upload_timeout)
            if up.status_code not in (200, 201):
                raise MinerUError(f"文件上传失败（HTTP {up.status_code}）")

        with httpx.Client(timeout=self.poll_timeout) as c:
            result = self._poll(
                lambda: self._json_or(
                    c.get(f"{BASE}/v4/extract-results/batch/{batch_id}",
                          headers={"Authorization": f"Bearer {self.api_key}"}),
                    "查询任务失败"),
                self._v4_pick_state, "v4", progress=progress, cancel=cancel)

            if result.get("state") != "done":
                raise MinerUError(f"解析失败：{result.get('err_msg', '未知错误')}")
            zip_url = result.get("full_zip_url")
            if not zip_url:
                raise MinerUError("解析完成但缺少结果下载地址")

        with httpx.Client(timeout=self.upload_timeout) as c:
            zr = c.get(zip_url)
            if zr.status_code != 200:
                raise MinerUError(f"结果下载失败（HTTP {zr.status_code}）")
            return self._markdown_from_zip(zr.content)

    @staticmethod
    def _v4_pick_state(raw: dict[str, Any]) -> dict[str, Any]:
        """pick：批量接口返回 {data:{extract_result:[{state,…}]}} → 取首个任务项。"""
        data = raw.get("data") or {}
        items = data.get("extract_result")
        if isinstance(items, list) and items:
            return items[0]
        return data

    # ------------------------------------------------------------ Agent v1
    def _extract_agent(self, p: Path, progress: Optional[Callable[[str], None]] = None,
                       cancel: Optional[Any] = None) -> str:
        with httpx.Client(timeout=self.upload_timeout) as c:
            r = c.post(f"{BASE}/v1/agent/parse/file",
                       json={"file_name": p.name, "language": "ch",
                             "enable_table": True, "is_ocr": True, "enable_formula": True})
            body = self._json_or(r, "创建轻量任务失败")
            if body.get("code") != 0:
                raise MinerUError(f"创建任务失败：{body.get('msg')}")
            task_id = body["data"]["task_id"]

            # B34：上传 PUT 前检查 cancel（取消后不再发起大文件上传）
            if cancel is not None and cancel.is_set():
                raise MinerUError("任务已取消")
            up = c.put(body["data"]["file_url"], content=p.read_bytes(),
                       timeout=self.upload_timeout)
            if up.status_code not in (200, 201):
                raise MinerUError(f"文件上传失败（HTTP {up.status_code}）")

        with httpx.Client(timeout=self.poll_timeout) as c:
            data = self._poll(
                lambda: self._json_or(c.get(f"{BASE}/v1/agent/parse/{task_id}"),
                                      "查询任务失败"),
                lambda raw: (raw.get("data") or {}), "agent",
                progress=progress, cancel=cancel)

            if data.get("state") != "done":
                raise MinerUError(f"解析失败：{data.get('err_msg', '未知错误')}"
                                  f"{'（err_code ' + str(data.get('err_code')) + '）' if data.get('err_code') else ''}")
            md_url = data.get("markdown_url")
            if not md_url:
                raise MinerUError("解析完成但缺少 Markdown 链接")

        with httpx.Client(timeout=self.upload_timeout) as c:
            mdr = c.get(md_url)
            if mdr.status_code != 200:
                raise MinerUError(f"结果下载失败（HTTP {mdr.status_code}）")
            return mdr.text

    # ------------------------------------------------------------ 通用
    def _poll(self, fetch, pick: Any, mode: str, max_wait: float = 900.0,
              progress: Optional[Callable[[str], None]] = None,
              cancel: Optional[Any] = None) -> dict[str, Any]:
        """轮询直到 done/failed；状态变化时回调 progress（中文标签）。"""
        start = time.time()
        last_state = ""
        while time.time() - start < max_wait:
            if cancel is not None and cancel.is_set():
                raise MinerUError("任务已取消")
            raw = fetch()
            data = pick(raw)
            state = data.get("state", "")
            if state != last_state:
                last_state = state
                if progress:
                    progress(STATE_LABELS.get(state, state))
            if state == "done":
                return data
            if state == "failed":
                raise MinerUError(f"解析失败：{data.get('err_msg', '未知错误')}")
            time.sleep(self.poll_interval)
        raise MinerUError(f"轮询超时（{int(max_wait)}s），请到 mineru.net 控制台查看任务")

    @staticmethod
    def _json_or(r: httpx.Response, ctx: str) -> dict[str, Any]:
        try:
            body = r.json()
        except Exception as e:  # noqa: BLE001
            raise MinerUError(f"{ctx}（HTTP {r.status_code}）") from e
        return body

    @staticmethod
    def _markdown_from_zip(content: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = z.namelist()
            full = [n for n in names if n.endswith("full.md")]
            if not full:
                md = [n for n in names if n.endswith(".md")]
                if not md:
                    raise MinerUError("结果压缩包中未找到 Markdown 文件")
                full = [md[0]]
            return z.read(full[0]).decode("utf-8", errors="replace")

    @staticmethod
    def _page_count(p: Path) -> Optional[int]:
        try:
            import fitz
            with fitz.open(str(p)) as doc:
                return doc.page_count
        except Exception:  # noqa: BLE001
            return None
