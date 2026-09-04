"""
测试环境管理接口

维护被测试的业务环境信息（环境名称、Base URL、状态、备注）。
Playwright 执行引擎运行在 Testbench 本机，通过 HTTP/HTTPS 访问测试环境。
不需要 SSH、不需要在测试环境安装任何自动化依赖。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, executor
from ..database import get_db

router = APIRouter(prefix="/api/servers", tags=["测试环境"])


@router.post("", response_model=schemas.TestServerOut)
def create_env(payload: schemas.TestServerCreate, db: Session = Depends(get_db)):
    env = models.TestServer(
        name=payload.name.strip(),
        base_url=payload.base_url.strip(),
        remark=payload.remark,
        status=models.ServerStatus.ONLINE,
        customer_name=payload.customer_name,
        username=payload.username,
        password=payload.password,
        login_path=payload.login_path or "/#/login",
    )
    db.add(env)
    db.commit()
    db.refresh(env)
    return env


@router.get("", response_model=list[schemas.TestServerOut])
def list_envs(db: Session = Depends(get_db)):
    return db.query(models.TestServer).order_by(models.TestServer.id.desc()).all()


# 静态路由必须在参数路由 /{env_id} 之前定义，否则会被参数路由匹配
@router.post("/env-check")
def check_local_env():
    """检查 Testbench 本机的 Playwright 执行环境（Python / Playwright / Chromium）"""
    return executor.check_local_env()


@router.post("/env-init")
def init_local_env():
    """初始化 Testbench 本机的 Playwright 执行环境（pip install playwright + playwright install chromium）"""
    return executor.init_local_env()


@router.get("/{env_id}", response_model=schemas.TestServerOut)
def get_env(env_id: int, db: Session = Depends(get_db)):
    env = db.query(models.TestServer).filter(models.TestServer.id == env_id).first()
    if not env:
        raise HTTPException(404, "测试环境不存在")
    return env


@router.put("/{env_id}", response_model=schemas.TestServerOut)
def update_env(env_id: int, payload: schemas.TestServerUpdate, db: Session = Depends(get_db)):
    env = db.query(models.TestServer).filter(models.TestServer.id == env_id).first()
    if not env:
        raise HTTPException(404, "测试环境不存在")
    update_data = payload.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(env, k, v)
    db.commit()
    db.refresh(env)
    return env


@router.delete("/{env_id}")
def delete_env(env_id: int, db: Session = Depends(get_db)):
    env = db.query(models.TestServer).filter(models.TestServer.id == env_id).first()
    if not env:
        raise HTTPException(404, "测试环境不存在")
    # 先删除关联的执行记录（test_runs 的 server_id 有 NOT NULL 约束，不能设为 NULL）
    related_runs = db.query(models.TestRun).filter(models.TestRun.server_id == env_id).all()
    for run in related_runs:
        # 先删除执行记录的步骤结果
        for sr in run.step_results:
            db.delete(sr)
        db.delete(run)
    db.delete(env)
    db.commit()
    return {"ok": True}
