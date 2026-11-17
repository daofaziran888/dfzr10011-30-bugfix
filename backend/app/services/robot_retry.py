"""修复RPA批量重试：失败记录续做、批次执行权与晚到回调的审计同步。

链路规则：
- 只有「执行失败」的执行记录能进入批量重试；
- 同一执行记录在任意时刻只允许一个批次持有执行权（记录上的「占用批次」字段）；
- 提交时重新从数据仓库读取执行记录的当前版本（流程模板、执行节点、失败步骤、版本号），
  不使用列表页的旧快照，避免读取错误版本导致失败续做结果数据无法核对；
- 每条记录保留原流程模板和执行节点，续做从失败步骤继续；部分失败只影响该记录；
- 失败续做结果数据同步到采集操作审计的「修复RPA批量重试」待处理清单，
  审计记录按批次保留执行节点、晚到回调与影响范围；
- 重复提交（同一 request_id）不会生成第二份执行审计。

批次执行权释放的两种做法：
- release_first（先释放批次执行权）：批次提交后立即完成并释放执行权，
  之后到达的完成回调记为晚到回调轨迹追加到审计批次；
- wait_callback（等待回调后释放执行权）：批次暂不结束，节点占用与等待记录同时可见，
  全部回调到齐后才结束批次并释放执行权。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

ROBOT_MODULE = "robot"
AUDIT_MODULE = "audit"
AUDIT_TYPE = "修复RPA批量重试"

FAILED_STATUS = "执行失败"
RUNNING_STATUS = "执行中"
SUCCESS_STATUS = "执行成功"

STRATEGY_WAIT = "wait_callback"
STRATEGY_RELEASE = "release_first"
STRATEGY_LABELS = {
    STRATEGY_WAIT: "等待回调后释放执行权",
    STRATEGY_RELEASE: "先释放批次执行权",
}

ITEM_WAITING = "待回调"
ITEM_OK = "续做成功"
ITEM_FAIL = "续做失败"

BATCH_RUNNING = "执行中"
BATCH_DONE = "已完成"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class RobotRetryService:
    """批量重试批次的登记、回调与审计同步；批次数据挂在服务单例上。"""

    def __init__(self) -> None:
        self._batches: list[dict[str, Any]] = []
        self._request_index: dict[str, int] = {}

    # ---------- 查询 ----------
    def list_batches(self) -> list[dict[str, Any]]:
        return sorted(self._batches, key=lambda batch: int(batch["id"]), reverse=True)

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        for batch in self._batches:
            if int(batch["id"]) == batch_id:
                return batch
        return None

    def _find_batch_by_code(self, code: str) -> dict[str, Any] | None:
        for batch in self._batches:
            if batch.get("批次编号") == code:
                return batch
        return None

    # ---------- 提交 ----------
    def submit_batch(
        self,
        *,
        ids: list[Any],
        strategy: str,
        request_id: str = "",
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
        """整组提交勾选的执行记录：逐台校验并返回处理结果，失败项保留在结果里。"""
        if strategy not in STRATEGY_LABELS:
            return None, [], f"未知释放策略「{strategy}」，可选：{'、'.join(STRATEGY_LABELS.values())}"
        if not ids:
            return None, [], "未勾选任何执行记录，批量重试未提交"
        if request_id and request_id in self._request_index:
            batch = self.get_batch(self._request_index[request_id])
            if batch is not None:
                return (
                    batch,
                    list(batch.get("提交结果", [])),
                    f"重复提交已忽略：沿用批次 {batch['批次编号']} 的执行审计，不生成第二份",
                )

        results: list[dict[str, Any]] = []
        accepted: list[dict[str, Any]] = []
        seen: set[int] = set()
        for raw in ids:
            try:
                record_id = int(raw)
            except (TypeError, ValueError):
                results.append({"记录id": raw, "记录编号": str(raw), "ok": False, "message": "记录标识无法识别，已跳过"})
                continue
            if record_id in seen:
                results.append({"记录id": record_id, "记录编号": f"#{record_id}", "ok": False, "message": "勾选内重复，已去重"})
                continue
            seen.add(record_id)
            # 重新读取当前版本，不用列表页快照，保证执行记录、流程模板、执行节点、失败步骤可核对
            record = store.find(ROBOT_MODULE, record_id)
            if record is None:
                results.append({"记录id": record_id, "记录编号": f"#{record_id}", "ok": False, "message": "执行记录不存在或已归档"})
                continue
            code = str(record.get("记录编号") or f"#{record_id}")
            if record.get("status") != FAILED_STATUS:
                results.append({
                    "记录id": record_id,
                    "记录编号": code,
                    "ok": False,
                    "message": f"当前状态为「{record.get('status')}」，只有执行失败记录能进入重试",
                })
                continue
            holder = str(record.get("占用批次") or "")
            if holder:
                holding = self._find_batch_by_code(holder)
                if holding is not None and holding.get("status") == BATCH_RUNNING:
                    results.append({
                        "记录id": record_id,
                        "记录编号": code,
                        "ok": False,
                        "message": f"批次 {holder} 正持有该记录执行权，同一执行记录只能有一个批次持有",
                    })
                    continue
                record.pop("占用批次", None)  # 占用批次已结束，清掉残留执行权
            accepted.append({
                "记录id": record_id,
                "记录编号": code,
                "流程模板": record.get("流程模板"),
                "执行节点": record.get("执行节点"),
                "失败步骤": record.get("失败步骤"),
                "版本号": record.get("版本号", 1),
                "结果": ITEM_WAITING,
                "回调时间": None,
            })
            results.append({
                "记录id": record_id,
                "记录编号": code,
                "ok": True,
                "message": f"已受理，保留原流程模板与执行节点，从失败步骤「{record.get('失败步骤')}」续做",
            })

        if not accepted:
            return None, results, "没有可续做的执行失败记录，未创建批次、未生成执行审计"

        batch_id = max((int(batch["id"]) for batch in self._batches), default=0) + 1
        batch: dict[str, Any] = {
            "id": batch_id,
            "批次编号": f"RTRY-{batch_id:04d}",
            "策略": strategy,
            "策略说明": STRATEGY_LABELS[strategy],
            "status": BATCH_RUNNING,
            "提交时间": _now(),
            "完成时间": None,
            "request_id": request_id,
            "items": accepted,
            "晚到回调": [],
            "提交结果": results,
        }
        # 持有执行权并标记续做中，版本号随状态流转递增
        for item in accepted:
            record = store.find(ROBOT_MODULE, int(item["记录id"]))
            if record is None:
                continue
            record["占用批次"] = batch["批次编号"]
            record["重试批次"] = batch["批次编号"]
            record["status"] = RUNNING_STATUS
            record["pending"] = True
            record["版本号"] = int(record.get("版本号") or 0) + 1

        if strategy == STRATEGY_RELEASE:
            # 先释放批次执行权：批次立即完成，之后到达的完成回调按晚到回调处理
            self._release_locks(batch)
            batch["status"] = BATCH_DONE
            batch["完成时间"] = _now()

        self._batches.append(batch)
        if request_id:
            self._request_index[request_id] = batch_id
        self.sync_audit(batch)
        rejected = sum(1 for item in results if not item["ok"])
        return (
            batch,
            results,
            f"批次 {batch['批次编号']} 已创建：受理 {len(accepted)} 条、拒绝 {rejected} 条，策略「{STRATEGY_LABELS[strategy]}」",
        )

    # ---------- 完成回调 ----------
    def callback(self, *, batch_id: int, record_id: int, result: str) -> tuple[dict[str, Any] | None, str]:
        """执行节点的完成回调：晚于批次完成的回调记为晚到回调轨迹，重复回调幂等忽略。"""
        batch = self.get_batch(batch_id)
        if batch is None:
            return None, f"重试批次 {batch_id} 不存在"
        item = next((it for it in batch["items"] if int(it["记录id"]) == record_id), None)
        if item is None:
            return None, f"执行记录 {record_id} 不在批次 {batch['批次编号']} 内"
        if item.get("结果") != ITEM_WAITING:
            return batch, f"记录 {item['记录编号']} 的完成回调已受理过，重复回调已忽略"
        if result not in ("成功", "失败"):
            return None, "回调结果只能是「成功」或「失败」"

        item["结果"] = ITEM_OK if result == "成功" else ITEM_FAIL
        item["回调时间"] = _now()
        record = store.find(ROBOT_MODULE, record_id)
        if record is not None:
            record["版本号"] = int(record.get("版本号") or 0) + 1
            record["结束时间"] = _now()
            if result == "成功":
                record["status"] = SUCCESS_STATUS
                record["执行结果"] = "续做成功"
                record["pending"] = False
                record["abnormal"] = False
            else:
                # 部分失败只影响该记录：回到执行失败，等待下一次重试
                record["status"] = FAILED_STATUS
                record["执行结果"] = "续做失败"
                record["异常信息"] = "续做仍失败，等待下一次重试"
                record["pending"] = True
                record["abnormal"] = True

        if batch["status"] == BATCH_DONE:
            # 先释放批次执行权：批次已结束，回调按晚到回调轨迹追加到审计批次
            batch["晚到回调"].append({
                "记录编号": item["记录编号"],
                "结果": item["结果"],
                "回调时间": item["回调时间"],
                "说明": "批次执行权已释放、批次已完成后到达的晚到回调",
            })
            message = f"记录 {item['记录编号']} 回调完成：{item['结果']}（晚到回调，已追加到批次 {batch['批次编号']} 的审计轨迹）"
        elif all(it.get("结果") != ITEM_WAITING for it in batch["items"]):
            # 等待回调后释放执行权：全部到齐才结束批次、释放执行权
            self._release_locks(batch)
            batch["status"] = BATCH_DONE
            batch["完成时间"] = _now()
            message = f"记录 {item['记录编号']} 回调完成：{item['结果']}，批次 {batch['批次编号']} 回调全部到齐，执行权已释放"
        else:
            waiting = sum(1 for it in batch["items"] if it.get("结果") == ITEM_WAITING)
            message = f"记录 {item['记录编号']} 回调完成：{item['结果']}，批次暂不结束，仍等待 {waiting} 条回调，节点占用保持"
        self.sync_audit(batch)
        return batch, message

    def _release_locks(self, batch: dict[str, Any]) -> None:
        for item in batch["items"]:
            record = store.find(ROBOT_MODULE, int(item["记录id"]))
            if record is not None and record.get("占用批次") == batch["批次编号"]:
                record.pop("占用批次", None)

    # ---------- 审计同步 ----------
    def sync_audit(self, batch: dict[str, Any] | None = None) -> tuple[int, int]:
        """把失败续做结果数据同步到审计模块的修复RPA批量重待处理清单；按批次编号幂等。"""
        batches = [batch] if batch is not None else list(self._batches)
        created = 0
        updated = 0
        rows = store.rows(AUDIT_MODULE)
        for item in batches:
            data = self._build_audit(item)
            existing = next(
                (row for row in rows if row.get("操作类型") == AUDIT_TYPE and row.get("操作对象") == item["批次编号"]),
                None,
            )
            if existing is not None:
                existing.update(data)
                updated += 1
            else:
                new_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
                entry = {"id": new_id, "记录编号": f"AUDI-{new_id:04d}", "status": "已记录", "复核状态": "待复核"}
                entry.update(data)
                rows.append(entry)
                created += 1
        return created, updated

    def retry_audit_entries(self) -> list[dict[str, Any]]:
        """修复RPA批量重待处理清单：待处理批次排在前面，便于按批次核对。"""
        rows = [row for row in store.rows(AUDIT_MODULE) if row.get("操作类型") == AUDIT_TYPE]
        return sorted(rows, key=lambda row: (not row.get("pending"), str(row.get("操作对象"))))

    def _build_audit(self, batch: dict[str, Any]) -> dict[str, Any]:
        items = batch["items"]
        ok = sum(1 for it in items if it.get("结果") == ITEM_OK)
        fail = sum(1 for it in items if it.get("结果") == ITEM_FAIL)
        waiting = sum(1 for it in items if it.get("结果") == ITEM_WAITING)
        nodes = sorted({str(it.get("执行节点")) for it in items if it.get("执行节点")})
        codes = [str(it.get("记录编号")) for it in items]
        detail = (
            f"批次 {batch['批次编号']} 续做 {len(items)} 条执行失败记录（{batch['策略说明']}）："
            f"续做成功 {ok}、续做失败 {fail}、待回调 {waiting}"
        )
        if batch["晚到回调"]:
            detail += f"；晚到回调 {len(batch['晚到回调'])} 条"
        return {
            "操作类型": AUDIT_TYPE,
            "操作对象": batch["批次编号"],
            "操作人": "系统",
            "操作时间": batch["提交时间"],
            "操作详情": detail,
            "影响范围": f"记录 {'、'.join(codes)}；节点 {'、'.join(nodes)}",
            "pending": batch["status"] == BATCH_RUNNING,
            "abnormal": fail > 0 or bool(batch["晚到回调"]),
            "批次编号": batch["批次编号"],
            "批次状态": batch["status"],
            "释放策略": batch["策略"],
            "策略说明": batch["策略说明"],
            "执行节点": nodes,
            "影响记录": codes,
            "重试记录": [dict(it) for it in items],
            "晚到回调": [dict(trail) for trail in batch["晚到回调"]],
            "完成时间": batch.get("完成时间"),
        }


retry_service = RobotRetryService()
