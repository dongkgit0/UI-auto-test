"""Pydantic Schema：用于 API 请求校验与响应序列化"""
import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


# ---------- TestEnvironment（测试环境） ----------
class TestServerCreate(BaseModel):
    name: str
    base_url: str
    remark: Optional[str] = None
    customer_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    login_path: Optional[str] = "/#/login"


class TestServerUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    remark: Optional[str] = None
    customer_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    login_path: Optional[str] = None


class TestServerOut(BaseModel):
    """测试环境输出"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    base_url: str
    status: str
    remark: Optional[str] = None
    customer_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    login_path: Optional[str] = None
    created_at: datetime.datetime


# ---------- TestStep ----------
class TestStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    step_order: int
    action: str
    target: Optional[str] = None
    locator_type: Optional[str] = None
    locator_value: Optional[str] = None
    input_value: Optional[str] = None
    expected_result: Optional[str] = None
    wait_timeout: int


# ---------- TestCase ----------
class TestCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    module: Optional[str] = None
    description: Optional[str] = None
    is_active: bool
    login_mode: str = "auto"
    created_at: datetime.datetime
    steps: list[TestStepOut] = []


class TestCaseListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    module: Optional[str] = None
    description: Optional[str] = None
    is_active: bool
    login_mode: str = "auto"
    step_count: int = 0


class TestStepUpdate(BaseModel):
    """编辑时的单条步骤"""
    step_order: int
    action: str
    target: Optional[str] = None
    input_value: Optional[str] = None
    expected_result: Optional[str] = None


class TestCaseUpdate(BaseModel):
    """编辑测试用例请求体"""
    name: Optional[str] = None
    module: Optional[str] = None
    description: Optional[str] = None
    login_mode: Optional[str] = None
    steps: Optional[list[TestStepUpdate]] = None


class TestCaseCreate(BaseModel):
    """新增测试用例请求体"""
    name: str
    module: Optional[str] = None
    description: Optional[str] = None
    login_mode: Optional[str] = "auto"
    steps: list[TestStepUpdate] = []


class ImportResult(BaseModel):
    imported_cases: int
    imported_steps: int
    case_names: list[str]
    warnings: list[str] = []


# ---------- TestRun ----------
class RunCreate(BaseModel):
    test_case_id: int
    server_id: int
    browser: Optional[str] = "chromium"
    headless: Optional[bool] = False


class StepResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    step_order: int
    action: str
    status: str
    actual_result: Optional[str] = None
    screenshot_path: Optional[str] = None
    duration_ms: Optional[int] = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    test_case_id: int
    server_id: int
    browser: Optional[str] = "chromium"
    status: str
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    error_message: Optional[str] = None
    created_at: datetime.datetime
    step_results: list[StepResultOut] = []
