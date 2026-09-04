"""
UI 自动化执行环境管理（Playwright 版）

通过 SSH 连接测试服务器，检测并初始化 Playwright 执行环境：
- Python 3 是否安装
- Playwright Python 库是否安装
- Chromium 浏览器是否安装且可启动

提供一键初始化：
- 安装/检查 Python 3
- pip install playwright
- playwright install chromium（安装浏览器运行环境）

不再使用 Docker / Selenium Grid / 4444 端口。
"""
import io
import paramiko


def _ssh_connect(server):
    """根据服务器配置建立 SSH 连接，失败抛出异常"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    pkey = None
    if server.auth_type == "ssh_key" and server.ssh_key:
        for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                pkey = key_cls.from_private_key(io.StringIO(server.ssh_key))
                break
            except Exception:
                continue

    kwargs = dict(
        hostname=server.host, port=server.ssh_port or 22,
        username=server.ssh_username, timeout=15,
    )
    if server.auth_type == "ssh_key":
        kwargs["pkey"] = pkey
    else:
        kwargs["password"] = server.ssh_password
    ssh.connect(**kwargs)
    return ssh


def _run(ssh, cmd: str, timeout: int = 120):
    """执行 SSH 命令，返回 (exit_code, stdout, stderr)"""
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return code, out, err


# ---------------- 环境健康检查 ----------------

def check_environment(server) -> dict:
    """
    执行环境健康检查，返回 {"ok": bool, "items": [{"name","ok","message"}]}
    检查链路：SSH → Python → Playwright → Chromium
    任何一步失败立即返回，不继续后续检查。
    """
    items = []

    # 1. SSH 连接
    try:
        ssh = _ssh_connect(server)
        items.append({"name": "SSH连接", "ok": True,
                       "message": f"已连接 {server.ssh_username}@{server.host}:{server.ssh_port or 22}"})
    except Exception as e:
        items.append({"name": "SSH连接", "ok": False, "message": f"连接失败：{e}"})
        return {"ok": False, "items": items}

    try:
        # 2. Python 3
        code, out, _ = _run(ssh, "python3 --version 2>&1")
        if code != 0:
            items.append({"name": "Python", "ok": False,
                           "message": "未安装 Python 3。请点击「初始化执行环境」安装，或手动安装 Python 3.8+。"})
            return {"ok": False, "items": items}
        items.append({"name": "Python", "ok": True, "message": out})

        # 3. Playwright Python 库
        code, out, err = _run(ssh, "python3 -c 'import playwright; print(playwright.__version__)' 2>&1")
        if code != 0:
            items.append({"name": "Playwright", "ok": False,
                           "message": "未安装 Playwright Python 库。请点击「初始化执行环境」安装。"})
            return {"ok": False, "items": items}
        items.append({"name": "Playwright", "ok": True, "message": f"Playwright {out}"})

        # 4. Chromium 浏览器（实际启动验证）
        code, out, err = _run(ssh,
            "python3 -c \"from playwright.sync_api import sync_playwright; "
            "p=sync_playwright().start(); "
            "b=p.chromium.launch(headless=True, args=['--no-sandbox']); "
            "b.close(); p.stop(); print('chromium_ok')\" 2>&1",
            timeout=60,
        )
        if code != 0 or "chromium_ok" not in out:
            items.append({"name": "Chromium", "ok": False,
                           "message": "Chromium 浏览器未安装或无法启动。请点击「初始化执行环境」执行 playwright install chromium。"})
            return {"ok": False, "items": items}
        items.append({"name": "Chromium", "ok": True, "message": "Chromium 浏览器正常，可启动"})

        return {"ok": True, "items": items}
    finally:
        try:
            ssh.close()
        except Exception:
            pass


# ---------------- 一键初始化环境 ----------------

def init_environment(server) -> dict:
    """
    一键初始化 Playwright 执行环境：
    1. 检查/安装 Python 3
    2. pip install playwright
    3. playwright install chromium（安装浏览器及依赖）
    4. 验证 Chromium 可启动
    """
    items = []

    # 1. SSH
    try:
        ssh = _ssh_connect(server)
        items.append({"name": "SSH连接", "ok": True,
                       "message": f"已连接 {server.ssh_username}@{server.host}:{server.ssh_port or 22}"})
    except Exception as e:
        items.append({"name": "SSH连接", "ok": False, "message": f"连接失败：{e}"})
        return {"ok": False, "items": items}

    try:
        # 2. Python 3
        code, out, _ = _run(ssh, "python3 --version 2>&1")
        if code != 0:
            items.append({"name": "Python安装", "ok": False, "message": "未检测到 Python 3，正在尝试安装..."})

            code_yum, _, _ = _run(ssh, "which yum 2>/dev/null")
            code_apt, _, _ = _run(ssh, "which apt-get 2>/dev/null")

            if code_yum == 0:
                # CentOS / RHEL
                _run(ssh, "yum install -y python3 python3-pip 2>&1 | tail -3", timeout=300)
            elif code_apt == 0:
                # Ubuntu / Debian
                _run(ssh, "apt-get update -qq && apt-get install -y python3 python3-pip 2>&1 | tail -3", timeout=300)
            else:
                items.append({"name": "Python安装", "ok": False,
                               "message": "无法识别包管理器，请手动安装 Python 3.8+ 后重试。"})
                return {"ok": False, "items": items}

            code_v, out_v, err_v = _run(ssh, "python3 --version 2>&1")
            if code_v != 0:
                items.append({"name": "Python安装", "ok": False,
                               "message": f"Python 安装失败：{err_v or out_v}"})
                return {"ok": False, "items": items}
            items.append({"name": "Python安装", "ok": True, "message": f"{out_v}，安装完成"})
        else:
            items.append({"name": "Python", "ok": True, "message": f"{out}，已安装"})

        # 3. Playwright Python 库
        code, out, _ = _run(ssh, "python3 -c 'import playwright; print(playwright.__version__)' 2>&1")
        if code != 0:
            items.append({"name": "Playwright安装", "ok": False,
                           "message": "未安装 Playwright，正在执行 pip install playwright..."})
            code_inst, out_inst, err_inst = _run(
                ssh,
                "python3 -m pip install playwright 2>&1 | tail -5",
                timeout=600,
            )
            code_v2, out_v2, err_v2 = _run(ssh, "python3 -c 'import playwright; print(playwright.__version__)' 2>&1")
            if code_v2 != 0:
                items.append({"name": "Playwright安装", "ok": False,
                               "message": f"Playwright 安装失败：{err_v2 or out_v2 or err_inst}"})
                return {"ok": False, "items": items}
            items.append({"name": "Playwright安装", "ok": True, "message": f"Playwright {out_v2}，安装完成"})
        else:
            items.append({"name": "Playwright", "ok": True, "message": f"Playwright {out}，已安装"})

        # 4. Chromium 浏览器
        code, out, err = _run(ssh,
            "python3 -c \"from playwright.sync_api import sync_playwright; "
            "p=sync_playwright().start(); "
            "b=p.chromium.launch(headless=True, args=['--no-sandbox']); "
            "b.close(); p.stop(); print('ok')\" 2>&1",
            timeout=60,
        )
        if code != 0 or "ok" not in out:
            items.append({"name": "Chromium安装", "ok": False,
                           "message": "Chromium 未安装，正在执行 playwright install chromium（首次可能需要几分钟）..."})

            # 安装系统依赖 + 浏览器
            code_deps, out_deps, err_deps = _run(
                ssh,
                "python3 -m playwright install-deps chromium 2>&1 | tail -5",
                timeout=600,
            )
            code_inst2, out_inst2, err_inst2 = _run(
                ssh,
                "python3 -m playwright install chromium 2>&1 | tail -5",
                timeout=600,
            )

            # 验证
            code_v3, out_v3, err_v3 = _run(ssh,
                "python3 -c \"from playwright.sync_api import sync_playwright; "
                "p=sync_playwright().start(); "
                "b=p.chromium.launch(headless=True, args=['--no-sandbox']); "
                "b.close(); p.stop(); print('ok')\" 2>&1",
                timeout=60,
            )
            if code_v3 != 0 or "ok" not in out_v3:
                items.append({"name": "Chromium安装", "ok": False,
                               "message": f"Chromium 安装后仍无法启动：{err_v3 or out_v3}"})
                return {"ok": False, "items": items}
            items.append({"name": "Chromium安装", "ok": True, "message": "Chromium 安装完成，可正常启动"})
        else:
            items.append({"name": "Chromium", "ok": True, "message": "Chromium 已安装，可正常启动"})

        return {"ok": True, "items": items}
    finally:
        try:
            ssh.close()
        except Exception:
            pass
