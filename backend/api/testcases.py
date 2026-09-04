"""测试用例管理接口：导入、查看、删除"""
import os
import shutil
import tempfile

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from .. import models, schemas, excel_import
from ..database import get_db

router = APIRouter(prefix="/api/testcases", tags=["测试用例"])


@router.post("/import", response_model=schemas.ImportResult)
def import_testcases(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "仅支持 .xlsx / .xls 文件")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        result = excel_import.import_test_cases(db, tmp_path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        os.unlink(tmp_path)

    return result


@router.get("/template")
def download_template():
    path = os.path.join(tempfile.gettempdir(), "testcase_template.xlsx")
    excel_import.generate_template(path)
    return FileResponse(path, filename="测试用例导入模板.xlsx")


@router.post("", response_model=schemas.TestCaseOut)
def create_testcase(payload: schemas.TestCaseCreate, db: Session = Depends(get_db)):
    """新增测试用例"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "用例名称不能为空")

    test_case = models.TestCase(
        name=name,
        module=payload.module.strip() if payload.module and payload.module.strip() else None,
        description=payload.description.strip() if payload.description and payload.description.strip() else None,
        login_mode=(payload.login_mode or "auto").strip() or "auto",
    )
    db.add(test_case)
    db.flush()

    for i, step in enumerate(payload.steps or []):
        db.add(models.TestStep(
            test_case_id=test_case.id,
            step_order=step.step_order or (i + 1),
            action=(step.action or "click").strip() or "click",
            target=step.target.strip() if step.target and step.target.strip() else None,
            input_value=step.input_value.strip() if step.input_value and step.input_value.strip() else None,
            expected_result=step.expected_result.strip() if step.expected_result and step.expected_result.strip() else None,
            wait_timeout=10,
        ))

    db.commit()
    db.refresh(test_case)
    return test_case


@router.get("", response_model=list[schemas.TestCaseListItem])
def list_testcases(db: Session = Depends(get_db)):
    rows = (
        db.query(models.TestCase, func.count(models.TestStep.id).label("step_count"))
        .outerjoin(models.TestStep)
        .group_by(models.TestCase.id)
        .order_by(models.TestCase.id.desc())
        .all()
    )
    out = []
    for case, step_count in rows:
        item = schemas.TestCaseListItem.model_validate(case)
        item.step_count = step_count
        out.append(item)
    return out


@router.get("/{case_id}", response_model=schemas.TestCaseOut)
def get_testcase(case_id: int, db: Session = Depends(get_db)):
    case = db.query(models.TestCase).filter(models.TestCase.id == case_id).first()
    if not case:
        raise HTTPException(404, "用例不存在")
    return case


@router.delete("/{case_id}")
def delete_testcase(case_id: int, db: Session = Depends(get_db)):
    case = db.query(models.TestCase).filter(models.TestCase.id == case_id).first()
    if not case:
        raise HTTPException(404, "用例不存在")
    db.delete(case)
    db.commit()
    return {"ok": True}


@router.put("/{case_id}", response_model=schemas.TestCaseOut)
def update_testcase(case_id: int, payload: schemas.TestCaseUpdate, db: Session = Depends(get_db)):
    """编辑测试用例：支持修改基本信息和步骤（步骤采用全量替换策略）"""
    case = db.query(models.TestCase).filter(models.TestCase.id == case_id).first()
    if not case:
        raise HTTPException(404, "用例不存在")

    # 更新基本信息
    if payload.name is not None:
        case.name = payload.name.strip()
    if payload.module is not None:
        case.module = payload.module.strip() or None
    if payload.description is not None:
        case.description = payload.description.strip() or None
    if payload.login_mode is not None:
        case.login_mode = payload.login_mode.strip() if payload.login_mode.strip() else "auto"

    # 更新步骤（全量替换：删除旧步骤，插入新步骤）
    if payload.steps is not None:
        # 删除旧步骤
        db.query(models.TestStep).filter(models.TestStep.test_case_id == case_id).delete()
        # 插入新步骤
        for i, step in enumerate(payload.steps):
            db.add(models.TestStep(
                test_case_id=case_id,
                step_order=step.step_order,
                action=step.action.strip(),
                target=step.target.strip() if step.target else None,
                input_value=step.input_value.strip() if step.input_value else None,
                expected_result=step.expected_result.strip() if step.expected_result else None,
            ))

    db.commit()
    db.refresh(case)
    return case
