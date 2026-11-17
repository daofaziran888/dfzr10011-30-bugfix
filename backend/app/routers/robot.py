"""RPA执行记录接口：维护执行记录，覆盖启动执行、重试执行、归档记录与批量重试续做。

注意路由顺序：/batch-retry 与 /export 必须注册在 /{entry_id} 之前，
否则会被详情路由截获并因 id 解析失败返回 422。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, BatchRetryResult, EntryPayload, PageResult
from app.services.robot import RobotService
from app.services.robot_retry import retry_service

router = APIRouter(prefix="/api/robot", tags=["RPA执行记录"])

service = RobotService()

LIST_FIELDS = ["记录编号", "流程模板", "执行节点", "开始时间", "结束时间", "执行结果", "异常信息", "记录状态"]
STATUSES = ["待执行", "执行中", "执行成功", "执行失败"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="待执行、执行中、执行成功、执行失败"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号与状态过滤RPA执行记录列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条执行记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="执行记录已登记", entry=entry)


@router.get("/batch-retry", response_model=PageResult[dict])
def list_retry_batches(page: int = 1, size: int = 50) -> PageResult[dict]:
    """重试批次列表：按批次核对重试记录、节点占用与晚到回调。"""
    batches = retry_service.list_batches()
    total = len(batches)
    start = max(page - 1, 0) * size
    return PageResult(items=batches[start:start + size], total=total, page=page, size=size)


@router.post("/batch-retry", response_model=BatchRetryResult)
def submit_batch_retry(payload: EntryPayload) -> BatchRetryResult:
    """整组提交勾选的执行记录：只有执行失败记录能进入重试，逐台返回处理结果并保留失败项。

    提交时重新读取执行记录当前版本（流程模板、执行节点、失败步骤、版本号），
    不使用列表页旧快照；同一 request_id 重复提交不会生成第二份执行审计。
    """
    ids = payload.values.get("ids") or []
    if not isinstance(ids, list):
        ids = [ids]
    strategy = str(payload.values.get("strategy") or "wait_callback").strip()
    request_id = str(payload.values.get("request_id") or "").strip()
    batch, results, message = retry_service.submit_batch(ids=ids, strategy=strategy, request_id=request_id)
    return BatchRetryResult(ok=batch is not None, message=message, batch=batch, results=results)


@router.get("/batch-retry/{batch_id}", response_model=dict)
def get_retry_batch(batch_id: int) -> dict:
    """单个重试批次明细：重试记录、节点占用、等待记录与晚到回调轨迹。"""
    batch = retry_service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"重试批次 {batch_id} 不存在")
    return batch


@router.post("/batch-retry/{batch_id}/callback", response_model=ActionResult)
def retry_callback(batch_id: int, payload: EntryPayload) -> ActionResult:
    """执行节点的完成回调：晚于批次完成的回调记为晚到回调轨迹，重复回调幂等忽略。"""
    try:
        record_id = int(payload.values.get("record_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="回调缺少有效的 record_id")
    result = str(payload.values.get("result") or "").strip()
    batch, message = retry_service.callback(batch_id=batch_id, record_id=record_id, result=result)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出RPA执行记录清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "robot", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条执行记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"执行记录 {entry_id} 不存在或已归档")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条执行记录执行启动执行、重试执行、归档记录；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
