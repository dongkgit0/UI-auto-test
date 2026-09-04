"""
数据库连接配置
默认使用 SQLite，生产环境可通过环境变量 DATABASE_URL 切换为 MySQL/PostgreSQL
例如: postgresql://user:pass@localhost:5432/ui_test_platform
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ui_test_platform.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依赖注入：获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库表结构，并在需要时迁移旧版 test_servers 表"""
    from . import models  # noqa: F401  确保模型被注册
    _migrate_test_servers()
    Base.metadata.create_all(bind=engine)
    _ensure_test_step_target_column()
    _ensure_server_base_url_column()
    _ensure_server_login_columns()
    _ensure_case_login_mode_column()
    _ensure_run_browser_column()


def _ensure_server_base_url_column():
    """给已存在的 test_servers 表添加 base_url 列，并从旧 host 字段迁移数据。

    旧版表结构（SSH 远程执行时代）有 host 列（NOT NULL），新版（测试环境管理）需要 base_url。
    SQLite 的 create_all 不会修改已有表结构，因此需要手动 ALTER TABLE。
    迁移完成后删除旧 host 列，避免 INSERT 时因 host NOT NULL 约束失败。
    """
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "test_servers" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("test_servers")}
    if "base_url" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE test_servers ADD COLUMN base_url VARCHAR(500)"))
        # 从旧 host 字段迁移数据：host → http://host
        if "host" in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE test_servers SET base_url = 'http://' || host "
                    "WHERE base_url IS NULL OR base_url = ''"
                ))

    # 迁移完成后删除旧 host 列（NOT NULL 约束会导致新 INSERT 失败）
    # SQLite 3.35.0+ 支持 DROP COLUMN，Python 3.11 自带版本满足
    cols = {c["name"] for c in insp.get_columns("test_servers")}
    if "host" in cols:
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE test_servers DROP COLUMN host"))
        except Exception:
            # 旧版 SQLite 不支持 DROP COLUMN 时，重建表去除 host 列
            _rebuild_test_servers_without_host()


def _rebuild_test_servers_without_host():
    """SQLite 旧版不支持 DROP COLUMN 时，通过重建表去除 host 列。"""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("test_servers") if c["name"] != "host"]
    cols_str = ", ".join(cols)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE test_servers RENAME TO test_servers_tmp"))
        conn.execute(text(f"CREATE TABLE test_servers AS SELECT {cols_str} FROM test_servers_tmp"))
        conn.execute(text("DROP TABLE test_servers_tmp"))


def _ensure_server_login_columns():
    """给已存在的 test_servers 表添加登录信息列（customer_name/username/password/login_path）"""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "test_servers" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("test_servers")}
    new_cols = {
        "customer_name": "VARCHAR(100)",
        "username": "VARCHAR(100)",
        "password": "VARCHAR(200)",
        "login_path": "VARCHAR(200) DEFAULT '/#/login'",
    }
    for col_name, col_def in new_cols.items():
        if col_name not in cols:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE test_servers ADD COLUMN {col_name} {col_def}"))


def _ensure_case_login_mode_column():
    """给已存在的 test_cases 表添加 login_mode 列，默认 auto（自动登录）"""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "test_cases" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("test_cases")}
    if "login_mode" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE test_cases ADD COLUMN login_mode VARCHAR(20) DEFAULT 'auto'"))


def _ensure_test_step_target_column():
    """给已存在的 test_steps 表添加 target（操作对象）列。

    SQLite 的 create_all 不会修改已有表结构，因此已有数据库需要手动 ALTER TABLE。
    """
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "test_steps" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("test_steps")}
    if "target" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE test_steps ADD COLUMN target VARCHAR(200)"))


def _migrate_test_servers():
    """
    旧版 test_servers 表包含 hub_url / max_sessions / platform 等字段，
    新版改为 host + SSH 信息 + 环境检测字段。检测到旧结构时自动迁移：
    重命名旧表 → 清理残留索引 → 创建新表 → 从 hub_url 解析 host 并迁移数据 → 删除旧表。

    注意：SQLite 的 ALTER TABLE RENAME TO 不会自动重命名用户创建的索引
    （如 ix_test_servers_id），因此重命名后必须手动 DROP INDEX，否则
    后续 CREATE TABLE 会报 "index already exists"。
    """
    from urllib.parse import urlparse
    from sqlalchemy import inspect, text
    from . import models

    insp = inspect(engine)
    table_names = insp.get_table_names()

    # 中断恢复：上次迁移在"旧表已改名、新表未创建"阶段失败
    if "test_servers_old" in table_names and "test_servers" not in table_names:
        _drop_stale_server_indexes()
        Base.metadata.create_all(bind=engine, tables=[models.TestServer.__table__])
        _migrate_old_server_data()
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS test_servers_old"))
        return

    # 没有旧表也没有新表，无需迁移
    if "test_servers" not in table_names:
        return

    cols = {c["name"] for c in insp.get_columns("test_servers")}

    # 新结构（已有 host 列）：若上次迁移残留了 test_servers_old，清理掉
    if "host" in cols:
        if "test_servers_old" in table_names:
            _drop_stale_server_indexes()
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS test_servers_old"))
        return

    # 没有 hub_url 列，不是待迁移的旧结构
    if "hub_url" not in cols:
        return

    # 旧结构：执行迁移
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE test_servers RENAME TO test_servers_old"))

    _drop_stale_server_indexes()
    Base.metadata.create_all(bind=engine, tables=[models.TestServer.__table__])
    _migrate_old_server_data()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS test_servers_old"))


def _drop_stale_server_indexes():
    """删除 SQLite 重命名表后残留的旧索引名，避免新表创建时冲突"""
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_test_servers_id"))


def _migrate_old_server_data():
    """从 test_servers_old 读取旧数据，解析 hub_url 得到 host，写入新表"""
    from urllib.parse import urlparse
    from sqlalchemy import text

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT * FROM test_servers_old")).fetchall()
        for row in rows:
            row_dict = dict(row._mapping)
            host = row_dict.get("hub_url") or ""
            try:
                parsed = urlparse(host)
                if parsed.hostname:
                    host = parsed.hostname
            except Exception:
                pass
            browser = row_dict.get("browser") or "chrome"
            conn.execute(text("""
                INSERT INTO test_servers
                    (id, name, host, ssh_port, ssh_username, auth_type,
                     browsers, browser, status, remark, created_at)
                VALUES
                    (:id, :name, :host, 22, NULL, 'password',
                     :browsers, :browser, :status, :remark, :created_at)
            """), {
                "id": row_dict.get("id"),
                "name": row_dict.get("name"),
                "host": host,
                "browsers": browser,
                "browser": browser,
                "status": row_dict.get("status", "unknown"),
                "remark": row_dict.get("remark"),
                "created_at": row_dict.get("created_at"),
            })


def _ensure_run_browser_column():
    """给已存在的 test_runs 表添加 browser 列，默认 chromium"""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "test_runs" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("test_runs")}
    if "browser" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE test_runs ADD COLUMN browser VARCHAR(20) DEFAULT 'chromium'"))
