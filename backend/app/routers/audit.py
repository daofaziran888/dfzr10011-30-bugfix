"""采集操作审计接口：维护审计记录，覆盖标记复核、归档记录，以及修复RPA批量重待处理清单。

注意路由顺序：/robot-retry-batches、/sync-robot-retry 与 /export 必须注册在
/{entry_id} 之前，否则会被详情路由截获并因 id 解析失败返回 422。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.audit import AuditService
from app.services.robot_retry import retry_service

router = APIRouter(prefix="/api/audit", tags=["采集操作审计"])

service = AuditService()

LIST_FIELDS = ["记录编号", "操作类型", "操作对象", "操作人", "操作时间", "操作详情", "影响范围", "复核状态"]
STATUSES = ["已记录", "已复核", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="已记录、已复核、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号与状态过滤采集操作审计列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条审计记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="审计记录已登记", entry=entry)


@router.get("/robot-retry-batches", response_model=PageResult[dict])
def list_robot_retry_batches(page: int = 1, size: int = 50) -> PageResult[dict]:
    """修复RPA批量重待处理清单：按重试批次记录执行节点、晚到回调和影响范围。"""
    entries = retry_service.retry_audit_entries()
    total = len(entries)
    start = max(page - 1, 0) * size
    return PageResult(items=entries[start:start + size], total=total, page=page, size=size)


@router.post("/sync-robot-retry", response_model=ActionResult)
def sync_robot_retry() -> ActionResult:
    """另一个入口：同步刷新修复RPA批量重清单与失败续做结果数据，按批次编号幂等。"""
    created, updated = retry_service.sync_audit()
    return ActionResult(ok=True, message=f"同步完成：新增 {created} 条、刷新 {updated} 条修复RPA批量重审计记录")


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集操作审计清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "audit", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条审计记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"审计记录 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条审计记录执行标记复核、归档记录；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
