"""测试执行接口：触发执行、查询进度与报告"""
import threading
import io
import os
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from .. import models, schemas, executor
from ..database import get_db, SessionLocal

router = APIRouter(prefix="/api/runs", tags=["测试执行"])


class BatchDeleteRequest(BaseModel):
    ids: list[int]


class BatchExportRequest(BaseModel):
    ids: list[int] = []


@router.post("", response_model=schemas.RunOut)
def create_run(payload: schemas.RunCreate, db: Session = Depends(get_db)):
    case = db.query(models.TestCase).filter(models.TestCase.id == payload.test_case_id).first()
    server = db.query(models.TestServer).filter(models.TestServer.id == payload.server_id).first()
    if not case:
        raise HTTPException(404, "测试用例不存在")
    if not server:
        raise HTTPException(404, "测试服务器不存在")
    if not case.steps:
        raise HTTPException(400, "该用例没有测试步骤，无法执行")

    # 执行前检查本机 Playwright 环境
    local_env = executor.check_local_env()
    if not local_env["ok"]:
        raise HTTPException(400, "当前Testbench执行环境未安装Playwright运行环境，请先完成本机环境初始化。")

    run = models.TestRun(
        test_case_id=payload.test_case_id,
        server_id=payload.server_id,
        browser=payload.browser or "chromium",
        status=models.RunStatus.PENDING,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # 在后台线程中执行，避免阻塞 API（Playwright 操作是同步阻塞的）
    thread = threading.Thread(
        target=executor.execute_run, args=(run.id, SessionLocal, payload.headless), daemon=True
    )
    thread.start()

    return run


class BatchRunRequest(BaseModel):
    test_case_ids: list[int]
    server_id: int
    browser: str = "chromium"
    headless: bool = False


@router.post("/batch")
def batch_create_runs(payload: BatchRunRequest, db: Session = Depends(get_db)):
    """批量创建执行任务，在后台线程中依次执行"""
    if not payload.test_case_ids:
        raise HTTPException(400, "请选择要执行的测试用例")
    
    server = db.query(models.TestServer).filter(models.TestServer.id == payload.server_id).first()
    if not server:
        raise HTTPException(404, "测试服务器不存在")
    
    # 验证所有用例都存在且有步骤
    cases = []
    for case_id in payload.test_case_ids:
        case = db.query(models.TestCase).filter(models.TestCase.id == case_id).first()
        if not case:
            raise HTTPException(404, f"测试用例 #{case_id} 不存在")
        if not case.steps:
            raise HTTPException(400, f"用例「{case.name}」没有测试步骤，无法执行")
        cases.append(case)
    
    # 执行前检查本机 Playwright 环境
    local_env = executor.check_local_env()
    if not local_env["ok"]:
        raise HTTPException(400, "当前Testbench执行环境未安装Playwright运行环境，请先完成本机环境初始化。")
    
    # 为每个用例创建执行记录
    run_ids = []
    for case in cases:
        run = models.TestRun(
            test_case_id=case.id,
            server_id=payload.server_id,
            browser=payload.browser or "chromium",
            status=models.RunStatus.PENDING,
        )
        db.add(run)
        db.flush()
        run_ids.append(run.id)
    db.commit()
    
    # 在后台线程中依次执行所有用例
    thread = threading.Thread(
        target=execute_batch_runs,
        args=(run_ids, SessionLocal, payload.headless),
        daemon=True
    )
    thread.start()
    
    return {"created": len(run_ids), "run_ids": run_ids}


def execute_batch_runs(run_ids, session_factory, headless):
    """依次执行批量用例，每个用例独立的浏览器会话"""
    for run_id in run_ids:
        try:
            executor.execute_run(run_id, session_factory, headless)
        except Exception as e:
            # 单个用例执行失败不影响后续用例
            print(f"[BatchRun] 用例 #{run_id} 执行异常: {e}")


@router.get("", response_model=list[schemas.RunOut])
def list_runs(db: Session = Depends(get_db), limit: int = 50):
    return (
        db.query(models.TestRun)
        .order_by(models.TestRun.id.desc())
        .limit(limit)
        .all()
    )


@router.post("/batch-delete")
def batch_delete_runs(payload: BatchDeleteRequest, db: Session = Depends(get_db)):
    """批量删除执行记录（同时删除关联的步骤结果和截图）"""
    if not payload.ids:
        raise HTTPException(400, "请选择要删除的记录")
    # 运行中的任务不允许删除
    running = db.query(models.TestRun).filter(
        models.TestRun.id.in_(payload.ids),
        models.TestRun.status.in_([models.RunStatus.RUNNING, models.RunStatus.PENDING])
    ).count()
    if running > 0:
        raise HTTPException(400, f"有 {running} 条任务正在执行中，请等待完成后再删除")
    # 删除步骤结果
    db.query(models.StepResult).filter(models.StepResult.run_id.in_(payload.ids)).delete(
        synchronize_session=False
    )
    # 删除执行记录
    deleted = db.query(models.TestRun).filter(models.TestRun.id.in_(payload.ids)).delete(
        synchronize_session=False
    )
    db.commit()
    return {"ok": True, "deleted": deleted}


@router.post("/export")
def export_runs(payload: BatchExportRequest, db: Session = Depends(get_db)):
    """批量导出执行记录为 PDF"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        raise HTTPException(500, "PDF 导出依赖未安装，请在 Python 环境中执行：pip install reportlab")

    query = db.query(models.TestRun).order_by(models.TestRun.id.desc())
    if payload.ids:
        query = query.filter(models.TestRun.id.in_(payload.ids))
    runs = query.all()
    if not runs:
        raise HTTPException(404, "没有可导出的记录")

    # 预取用例和环境名称
    case_map = {c.id: c.name for c in db.query(models.TestCase).all()}
    server_map = {s.id: s.name for s in db.query(models.TestServer).all()}
    status_label = {
        "pending": "等待中", "running": "执行中", "passed": "通过",
        "failed": "失败", "error": "异常", "skipped": "已跳过"
    }
    status_color = {
        "passed": colors.HexColor("#2e9e5b"),
        "failed": colors.HexColor("#d9483d"),
        "error": colors.HexColor("#c98a1e"),
        "running": colors.HexColor("#2563eb"),
        "pending": colors.HexColor("#6b7280"),
    }

    # 注册中文字体（Windows 黑体）
    font_name = "Helvetica"
    font_paths = [
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                pdfmetrics.registerFont(TTFont("ChineseFont", fp))
                font_name = "ChineseFont"
                break
            except Exception:
                continue

    # 生成 PDF
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=styles["Title"],
        fontName=font_name, fontSize=18, leading=24,
        textColor=colors.HexColor("#111827"), spaceAfter=4
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"],
        fontName=font_name, fontSize=10, leading=14,
        textColor=colors.HexColor("#6b7280"), spaceAfter=12
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Normal"],
        fontName=font_name, fontSize=12, leading=16,
        textColor=colors.HexColor("#374151"), spaceBefore=10, spaceAfter=6
    )
    normal_style = ParagraphStyle(
        "Normal", parent=styles["Normal"],
        fontName=font_name, fontSize=9, leading=12,
        textColor=colors.HexColor("#374151")
    )

    elements = []

    # 标题
    elements.append(Paragraph("UI 自动化测试执行报告", title_style))
    elements.append(Paragraph(f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}　|　共 {len(runs)} 条记录", subtitle_style))

    # 统计信息
    total = len(runs)
    passed_count = sum(1 for r in runs if r.status == "passed")
    failed_count = sum(1 for r in runs if r.status == "failed")
    error_count = sum(1 for r in runs if r.status == "error")
    pass_rate = (passed_count / total * 100) if total > 0 else 0

    stats_data = [
        ["总执行数", "通过", "失败", "异常", "通过率"],
        [str(total), str(passed_count), str(failed_count), str(error_count), f"{pass_rate:.1f}%"],
    ]
    stats_table = Table(stats_data, colWidths=[35*mm, 30*mm, 30*mm, 30*mm, 30*mm])
    stats_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, 1), 11),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f3f5")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#374151")),
        ("TEXTCOLOR", (1, 1), (1, 1), colors.HexColor("#2e9e5b")),
        ("TEXTCOLOR", (2, 1), (2, 1), colors.HexColor("#d9483d")),
        ("TEXTCOLOR", (3, 1), (3, 1), colors.HexColor("#c98a1e")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("FONTWEIGHT", (0, 0), (-1, 0), "bold"),
        ("FONTWEIGHT", (0, 1), (-1, 1), "bold"),
    ]))
    elements.append(stats_table)
    elements.append(Spacer(1, 12))

    # 执行记录明细
    elements.append(Paragraph("执行记录明细", section_style))

    for idx, r in enumerate(runs, 1):
        steps = r.step_results or []
        step_total = len(steps)
        step_passed = sum(1 for s in steps if s.status == "passed")
        step_failed = sum(1 for s in steps if s.status == "failed")
        step_skipped = sum(1 for s in steps if s.status == "skipped")
        duration = ""
        if r.start_time and r.end_time:
            duration = f"{round((r.end_time - r.start_time).total_seconds(), 1)} 秒"

        case_name = case_map.get(r.test_case_id, f"#{r.test_case_id}")
        server_name = server_map.get(r.server_id, f"#{r.server_id}")
        status_text = status_label.get(r.status, r.status)
        s_color = status_color.get(r.status, colors.HexColor("#374151"))

        # 记录标题
        record_title = ParagraphStyle(
            f"RecordTitle_{idx}", parent=normal_style,
            fontSize=11, leading=15, textColor=colors.HexColor("#111827"),
            spaceBefore=8, spaceAfter=4
        )
        elements.append(Paragraph(f"<b>#{r.id}</b>　{case_name}", record_title))

        # 记录详情表格
        detail_data = [
            ["测试环境", server_name, "浏览器", (r.browser or "chromium").capitalize()],
            ["状态", status_text, "耗时", duration],
            ["总步骤", str(step_total), "成功/失败/跳过", f"{step_passed} / {step_failed} / {step_skipped}"],
            ["开始时间", r.start_time.strftime("%Y-%m-%d %H:%M:%S") if r.start_time else "-",
             "结束时间", r.end_time.strftime("%Y-%m-%d %H:%M:%S") if r.end_time else "-"],
        ]
        detail_table = Table(detail_data, colWidths=[22*mm, 55*mm, 28*mm, 55*mm])
        detail_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f9fafb")),
            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f9fafb")),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#6b7280")),
            ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#6b7280")),
            ("TEXTCOLOR", (1, 1), (1, 1), s_color),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(detail_table)

        # 错误信息
        if r.error_message:
            error_style = ParagraphStyle(
                f"Error_{idx}", parent=normal_style,
                fontSize=9, leading=12, textColor=colors.HexColor("#d9483d"),
                backColor=colors.HexColor("#fef2f2"), borderPadding=6,
                spaceBefore=4, spaceAfter=4
            )
            elements.append(Paragraph(f"<b>错误信息：</b>{r.error_message}", error_style))

        elements.append(Spacer(1, 6))

    doc.build(elements)
    output.seek(0)

    filename = f"执行报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    encoded_filename = quote(filename)
    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"}
    )


@router.get("/{run_id}", response_model=schemas.RunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.query(models.TestRun).filter(models.TestRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "执行记录不存在")
    return run
