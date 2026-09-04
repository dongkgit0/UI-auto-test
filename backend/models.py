"""
数据模型定义

核心实体关系：
TestServer（测试服务器/Selenium节点）
TestCase（测试用例）──1:N──> TestStep（测试步骤）
TestRun（一次执行）──关联 TestCase + TestServer，1:N──> StepResult（每一步的执行结果）
"""
import enum
import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Enum, Boolean
)
from sqlalchemy.orm import relationship
from .database import Base


class ServerStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"
    CHECKING = "checking"
    ERROR = "error"


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class StepStatus(str, enum.Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TestServer(Base):
    """
    测试环境管理。

    维护被测试的业务环境信息（环境名称、Base URL、状态）。
    Playwright 执行引擎运行在 Testbench 本机，通过 HTTP/HTTPS 访问测试环境。
    不需要 SSH、不需要在测试环境安装任何自动化依赖。
    """
    __tablename__ = "test_servers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)                    # 环境名称
    base_url = Column(String(500), nullable=False)                 # 环境地址 / Base URL，如 http://172.16.10.151
    status = Column(Enum(ServerStatus), default=ServerStatus.UNKNOWN)  # 环境状态
    remark = Column(String(500), nullable=True)                    # 备注
    # 登录信息（统一维护，业务用例自动登录时使用）
    customer_name = Column(String(100), nullable=True)             # 客户名
    username = Column(String(100), nullable=True)                  # 用户名
    password = Column(String(200), nullable=True)                  # 密码
    login_path = Column(String(200), nullable=True, default="/#/login")  # 登录页面路径
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    runs = relationship("TestRun", back_populates="server")


class TestCase(Base):
    __tablename__ = "test_cases"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    module = Column(String(100), nullable=True)           # 所属模块/业务线
    description = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    login_mode = Column(String(20), nullable=False, default="auto")  # auto=自动登录, none=不登录, reuse=复用登录状态
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    steps = relationship(
        "TestStep", back_populates="test_case",
        order_by="TestStep.step_order", cascade="all, delete-orphan"
    )
    runs = relationship("TestRun", back_populates="test_case")


class TestStep(Base):
    """
    单条测试步骤。action 决定如何解释 locator_type/locator_value/input_value
    支持的 action 见 backend/executor.py 中的 ACTION_HANDLERS
    """
    __tablename__ = "test_steps"

    id = Column(Integer, primary_key=True, index=True)
    test_case_id = Column(Integer, ForeignKey("test_cases.id"), nullable=False)
    step_order = Column(Integer, nullable=False)
    action = Column(String(50), nullable=False)            # 业务操作：打开/输入/点击/校验/选择/...
    target = Column(String(200), nullable=True)             # 操作对象（业务描述，如"用户名"、"登录按钮"）
    locator_type = Column(String(20), nullable=True)       # id/name/css/xpath/link_text（后续元素管理填充，Excel 不要求）
    locator_value = Column(String(500), nullable=True)
    input_value = Column(String(500), nullable=True)       # 输入内容 或 url 或 期望等
    expected_result = Column(String(500), nullable=True)   # 人工可读的期望描述
    wait_timeout = Column(Integer, default=10)              # 元素等待超时（秒）

    test_case = relationship("TestCase", back_populates="steps")


class TestRun(Base):
    """一次测试执行任务（可能包含一个或多个用例）"""
    __tablename__ = "test_runs"

    id = Column(Integer, primary_key=True, index=True)
    test_case_id = Column(Integer, ForeignKey("test_cases.id"), nullable=False)
    server_id = Column(Integer, ForeignKey("test_servers.id"), nullable=False)
    browser = Column(String(20), default="chromium")
    status = Column(Enum(RunStatus), default=RunStatus.PENDING)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    test_case = relationship("TestCase", back_populates="runs")
    server = relationship("TestServer", back_populates="runs")
    step_results = relationship(
        "StepResult", back_populates="run",
        order_by="StepResult.step_order", cascade="all, delete-orphan"
    )


class StepResult(Base):
    """每一步的执行结果，包含失败时的截图路径"""
    __tablename__ = "step_results"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("test_runs.id"), nullable=False)
    step_id = Column(Integer, ForeignKey("test_steps.id"), nullable=False)
    step_order = Column(Integer, nullable=False)
    action = Column(String(50), nullable=False)
    status = Column(Enum(StepStatus), default=StepStatus.PENDING)
    actual_result = Column(String(1000), nullable=True)
    screenshot_path = Column(String(255), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    run = relationship("TestRun", back_populates="step_results")
