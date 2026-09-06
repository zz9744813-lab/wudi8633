"""影像相学分析路由（第 8/9/64 节）。

POST /api/imaging/analyze —— 上传面相/掌纹照片，本地 CV 分析，
可选（用户当次勾选）云端视觉详批。隐私边界见 app/services/imaging.py。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from app.database import get_engine, get_session
from app.services import imaging

router = APIRouter()


@router.post("/imaging/analyze")
async def analyze_image(
    file: UploadFile = File(...),
    kind: str = Form(...),
    use_cloud: bool = Form(False),
    save: bool = Form(True),
    user_id: int = Form(1),
    hand: str = Form("right"),
) -> JSONResponse:
    kind = kind.strip().lower()
    if kind not in ("palm", "face"):
        return JSONResponse({"detail": "kind 必须是 palm 或 face"}, status_code=400)

    mime = (file.content_type or "").lower()
    ext = imaging.ALLOWED_TYPES.get(mime)
    if not ext:
        return JSONResponse(
            {"detail": "仅支持 JPEG / PNG / WebP 图片"}, status_code=415
        )

    # 大小预检：Starlette 会把超大 body 落盘缓冲，先按声明体积拒绝，
    # 避免把整个文件读进内存后才发现超限（声明缺失时仍以读后实际大小为准）。
    declared = getattr(file, "size", None)
    if declared is not None and declared > imaging.MAX_BYTES:
        return JSONResponse({"detail": "图片不能超过 8MB"}, status_code=413)

    data = await file.read()
    if not data:
        return JSONResponse({"detail": "文件为空"}, status_code=400)
    if len(data) > imaging.MAX_BYTES:
        return JSONResponse({"detail": "图片不能超过 8MB"}, status_code=413)

    # 本地确定性分析（CV，不上云；原图分析后立即删除）
    try:
        local = imaging.analyze_local(data, ext, kind)
    except Exception as exc:  # CV 管线异常要降级为可读错误，不能 500 裸奔
        return JSONResponse(
            {"detail": f"图像分析失败：{type(exc).__name__}"}, status_code=422
        )

    # 云端详批：仅当用户当次勾选。失败不阻塞本地结果。
    cloud: dict = {"used": False}
    if use_cloud:
        cloud = imaging.cloud_reading(data, mime, kind)

    # 特征存档（原图即焚不变）：派生特征经用户确认后入库，供长期参照与信号闭环
    saved = False
    record_id = None
    if save:
        try:
            from sqlmodel import Session

            from app.database import get_engine

            hand_v = hand if hand in ("left", "right") else "right"
            with Session(get_engine()) as session:
                record_id = imaging.save_record(
                    session, user_id, kind, local["features"], local["detected"], hand=hand_v
                )
            saved = True
        except Exception:
            saved = False  # 存档失败不阻断分析结果

    return JSONResponse(
        {
            "kind": kind,
            "detected": local["detected"],
            "features": local["features"],
            "reading": local["reading"],
            "cloud": cloud,
            "saved": saved,
            "record_id": record_id,
            "privacy": {
                "original_deleted": True,
                "features_stored": saved,
                "cloud_sent": bool(cloud.get("used")),
                "note": (
                    "原图已在分析完成后立即删除且永不入库；"
                    "勾选存档时仅保存派生特征数值（可随时清除），"
                    "云端详批仅在你勾选时发送原图。"
                ),
            },
        }
    )


@router.get("/imaging/history")
def imaging_history(
    user_id: int = Query(...),
    kind: str = Query("palm"),
    session: Session = Depends(get_session),
):
    """历史相法特征（解读由特征确定性重生成，原图从未入库）。"""
    if kind not in ("palm", "face"):
        raise HTTPException(400, "kind 必须是 palm 或 face")
    return {"kind": kind, "items": imaging.list_records(session, user_id, kind)}


@router.delete("/imaging/records")
def imaging_purge(
    user_id: int = Query(...),
    kind: str | None = Query(None),
    session: Session = Depends(get_session),
):
    """清除存档特征（kind 省略则全部清除）。"""
    n = imaging.purge_records(session, user_id, kind)
    return {"deleted": n}
