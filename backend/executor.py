"""
测试执行引擎（Playwright 本机执行版）

架构：
Testbench（Windows 本机）
  → Python + Playwright
  → 本机启动 Chromium
  → HTTP/HTTPS 访问测试环境（Base URL）
  → 自动定位页面元素
  → 执行测试步骤
  → 记录结果 / 截图 / 断言
  → 生成执行报告

不再使用：
- SSH 远程执行
- Selenium Grid / Remote WebDriver
- 4444 端口
- Docker Selenium 容器

元素定位：由 Playwright 根据「操作对象」自动定位，
优先级：get_by_role() → get_by_label() → get_by_text() → get_by_placeholder() → CSS → XPath。

异常区分：
- 本机环境问题：Playwright / Chromium 未安装
- 测试环境问题：无法访问 Base URL
- 执行问题：无法定位元素 / 断言失败
"""
import os
import time
import datetime
from typing import Optional

from sqlalchemy.orm import Session

from . import models

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


# ---------------- 本机环境检查 ----------------

def check_local_env() -> dict:
    """
    检查 Testbench 本机的 Playwright 执行环境。
    返回 {"ok": bool, "items": [{"name","ok","message"}]}
    """
    items = []

    # 1. Python（当前进程即是 Python，直接通过）
    import sys
    py_ver = sys.version.split()[0]
    items.append({"name": "Python", "ok": True,
                   "message": "Python " + py_ver})

    # 2. Playwright 库
    try:
        import playwright
        items.append({"name": "Playwright", "ok": True,
                       "message": f"Playwright {getattr(playwright, '__version__', '已安装')}"})
    except ImportError:
        items.append({"name": "Playwright", "ok": False,
                       "message": "未安装 Playwright Python 库。请执行 pip install playwright，或点击「初始化执行环境」。"})
        return {"ok": False, "items": items}

    # 3. Chromium 浏览器（实际启动验证）
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        items.append({"name": "Chromium", "ok": True, "message": "Chromium 浏览器正常，可启动"})
    except Exception as e:
        items.append({"name": "Chromium", "ok": False,
                       "message": f"Chromium 浏览器未安装或无法启动：{e}。请执行 playwright install chromium，或点击「初始化执行环境」。"})
        return {"ok": False, "items": items}

    return {"ok": True, "items": items}


def init_local_env() -> dict:
    """
    初始化本机 Playwright 执行环境：
    1. pip install playwright
    2. playwright install chromium
    返回 {"ok": bool, "items": [...]}
    """
    import subprocess
    import sys
    items = []

    # 1. 安装 Playwright 库
    items.append({"name": "安装Playwright", "ok": False, "message": "正在执行 pip install playwright..."})
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "playwright"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            items[-1]["message"] = f"pip install playwright 失败：{result.stderr[-500:]}"
            return {"ok": False, "items": items}
        items[-1]["ok"] = True
        items[-1]["message"] = "Playwright 库安装完成"
    except Exception as e:
        items[-1]["message"] = f"pip install playwright 异常：{e}"
        return {"ok": False, "items": items}

    # 2. 安装 Chromium 浏览器
    items.append({"name": "安装Chromium", "ok": False, "message": "正在执行 playwright install chromium（首次可能需要几分钟）..."})
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            items[-1]["message"] = f"playwright install chromium 失败：{result.stderr[-500:]}"
            return {"ok": False, "items": items}
        items[-1]["ok"] = True
        items[-1]["message"] = "Chromium 浏览器安装完成"
    except Exception as e:
        items[-1]["message"] = f"playwright install chromium 异常：{e}"
        return {"ok": False, "items": items}

    # 3. 验证
    verify = check_local_env()
    if verify["ok"]:
        items.append({"name": "环境验证", "ok": True, "message": "Playwright + Chromium 环境验证通过"})
        return {"ok": True, "items": items}
    else:
        items.append({"name": "环境验证", "ok": False, "message": "环境验证失败，请检查上述步骤"})
        return {"ok": False, "items": items}


# ---------------- 智能元素定位 ----------------

def _candidate_names(target: str) -> list:
    """根据操作对象生成候选名称列表（按优先级排序）。
    例如"登录按钮" → ["登录按钮", "登录"]
    "用户名输入框" → ["用户名输入框", "用户名"]
    """
    names = [target]
    for suffix in ["按钮", "输入框", "框", "链接", "菜单", "标签", "页"]:
        if target.endswith(suffix) and len(target) > len(suffix):
            stripped = target[:-len(suffix)].strip()
            if stripped and stripped not in names:
                names.append(stripped)
    return names


def _try_locator(page, locator_factory):
    """尝试创建定位器并检查是否存在元素，返回 locator 或 None。
    优化：不逐个检查可见性，减少 Playwright 调用次数，提高定位速度。
    """
    try:
        loc = locator_factory()
        if loc.count() > 0:
            return loc.first
    except Exception:
        pass
    return None


def _is_editable(page, locator):
    """检查定位到的元素是否是可编辑元素（input/textarea/select/contenteditable/Ant Design Select）"""
    try:
        tag = locator.evaluate("el => el.tagName ? el.tagName.toLowerCase() : ''")
        if tag in ("input", "textarea", "select"):
            return True
        if locator.get_attribute("contenteditable"):
            return True
        # Ant Design Select / Cascader / DatePicker 等组件也认为是可交互的
        if _is_dropdown_select(page, locator):
            return True
        return False
    except Exception:
        return False


def _locate_form_field_fast(page, name: str):
    """
    快速定位表单字段：一次 JavaScript 调用完成定位和组件类型判断。
    专门针对 Ant Design 表单优化，大幅减少 Playwright 调用次数。
    
    返回: (locator, component_type) 或 (None, None)
    component_type: "input" / "select" / "radio" / "cascader" / "picker" / "checkbox" / "unknown"
    """
    try:
        result = page.evaluate("""
            (targetName) => {
                // 先清除所有旧的 data-pw-fast 标记，避免多个元素有标记
                document.querySelectorAll('[data-pw-fast]').forEach(el => el.removeAttribute('data-pw-fast'));
                
                const clean = (s) => (s || '').replace(/\\s/g, '').replace(/[：:]$/, '');
                const target = clean(targetName);
                
                // 1. 查找所有 ant-form-item-label
                const labels = document.querySelectorAll('label.ant-form-item-label, .ant-form-item-label label, .ant-form-item-label > label');
                for (const lbl of labels) {
                    let text = '';
                    try { text = lbl.innerText || lbl.textContent || ''; } catch(e) { text = lbl.textContent || ''; }
                    if (clean(text) !== target && !clean(text).includes(target) && !target.includes(clean(text))) continue;
                    
                    // 找到匹配的 label，向上找 ant-form-item 容器
                    let container = lbl.closest('.ant-form-item');
                    if (!container) {
                        let p = lbl.parentElement;
                        for (let i = 0; i < 5 && p; i++) {
                            if (p.classList && p.classList.contains('ant-form-item')) { container = p; break; }
                            p = p.parentElement;
                        }
                    }
                    if (!container) continue;
                    
                    // 2. 在容器中查找可交互元素，判断组件类型
                    // 优先找 ant-select（下拉选择框）
                    const selectEl = container.querySelector('.ant-select');
                    if (selectEl) {
                        const selector = selectEl.querySelector('.ant-select-selector');
                        const clickable = selector || selectEl;
                        clickable.setAttribute('data-pw-fast', '1');
                        return {found: true, type: 'select'};
                    }
                    
                    // 找 ant-radio-group（单选按钮组）
                    const radioGroup = container.querySelector('.ant-radio-group');
                    if (radioGroup) {
                        radioGroup.setAttribute('data-pw-fast', '1');
                        return {found: true, type: 'radio'};
                    }
                    
                    // 找 ant-cascader-picker（级联选择器）
                    const cascader = container.querySelector('.ant-cascader-picker');
                    if (cascader) {
                        cascader.setAttribute('data-pw-fast', '1');
                        return {found: true, type: 'cascader'};
                    }
                    
                    // 找 ant-picker（日期选择器）
                    const picker = container.querySelector('.ant-picker');
                    if (picker) {
                        picker.setAttribute('data-pw-fast', '1');
                        return {found: true, type: 'picker'};
                    }
                    
                    // 找 ant-checkbox-group（复选框组）
                    const checkboxGroup = container.querySelector('.ant-checkbox-group');
                    if (checkboxGroup) {
                        checkboxGroup.setAttribute('data-pw-fast', '1');
                        return {found: true, type: 'checkbox'};
                    }
                    
                    // 找 ant-input-number（数字输入框）
                    const inputNumber = container.querySelector('.ant-input-number');
                    if (inputNumber) {
                        const input = inputNumber.querySelector('input');
                        if (input) {
                            input.setAttribute('data-pw-fast', '1');
                            return {found: true, type: 'input'};
                        }
                    }
                    
                    // 找普通 input/textarea
                    const inputs = container.querySelectorAll('input, textarea');
                    for (const inp of inputs) {
                        if (inp.type === 'hidden' || inp.type === 'radio' || inp.type === 'checkbox') continue;
                        // 跳过 ant-select 的搜索 input（已经在上面处理了）
                        if (inp.closest('.ant-select')) continue;
                        inp.setAttribute('data-pw-fast', '1');
                        return {found: true, type: 'input'};
                    }
                    
                    // 找 contenteditable
                    const editable = container.querySelector('[contenteditable="true"]');
                    if (editable) {
                        editable.setAttribute('data-pw-fast', '1');
                        return {found: true, type: 'input'};
                    }
                }
                
                // 3. 备用：全局查找包含目标文本的 ant-select-prefix（带前缀的选择框）
                const prefixes = document.querySelectorAll('.ant-select-prefix');
                for (const prefix of prefixes) {
                    let text = '';
                    try { text = prefix.innerText || prefix.textContent || ''; } catch(e) { text = prefix.textContent || ''; }
                    if (clean(text) === target || clean(text).includes(target) || target.includes(clean(text))) {
                        const selectEl = prefix.closest('.ant-select');
                        if (selectEl) {
                            const selector = selectEl.querySelector('.ant-select-selector');
                            const clickable = selector || selectEl;
                            clickable.setAttribute('data-pw-fast', '1');
                            return {found: true, type: 'select'};
                        }
                    }
                }
                
                // 4. 备用：全局查找所有 ant-radio-group，检查其附近是否有目标文本
                const radioGroups = document.querySelectorAll('.ant-radio-group');
                for (const rg of radioGroups) {
                    // 向上找 ant-form-item 容器，检查 label 文本
                    const formItem = rg.closest('.ant-form-item');
                    if (formItem) {
                        const lbl = formItem.querySelector('.ant-form-item-label label, label.ant-form-item-label');
                        if (lbl) {
                            let text = '';
                            try { text = lbl.innerText || lbl.textContent || ''; } catch(e) { text = lbl.textContent || ''; }
                            if (clean(text) === target || clean(text).includes(target) || target.includes(clean(text))) {
                                rg.setAttribute('data-pw-fast', '1');
                                return {found: true, type: 'radio'};
                            }
                        }
                    }
                    // 检查 radio-group 前面的兄弟元素是否有目标文本
                    let prev = rg.previousElementSibling;
                    for (let i = 0; i < 3 && prev; i++) {
                        let text = '';
                        try { text = prev.innerText || prev.textContent || ''; } catch(e) { text = prev.textContent || ''; }
                        if (clean(text) === target || clean(text).includes(target) || target.includes(clean(text))) {
                            rg.setAttribute('data-pw-fast', '1');
                            return {found: true, type: 'radio'};
                        }
                        prev = prev.previousElementSibling;
                    }
                }
                
                // 5. 备用：全局查找所有 ant-select，检查其附近是否有目标文本
                const selects = document.querySelectorAll('.ant-select');
                for (const sel of selects) {
                    const formItem = sel.closest('.ant-form-item');
                    if (formItem) {
                        const lbl = formItem.querySelector('.ant-form-item-label label, label.ant-form-item-label');
                        if (lbl) {
                            let text = '';
                            try { text = lbl.innerText || lbl.textContent || ''; } catch(e) { text = lbl.textContent || ''; }
                            if (clean(text) === target || clean(text).includes(target) || target.includes(clean(text))) {
                                const selector = sel.querySelector('.ant-select-selector');
                                const clickable = selector || sel;
                                clickable.setAttribute('data-pw-fast', '1');
                                return {found: true, type: 'select'};
                            }
                        }
                    }
                }
                
                return {found: false, type: 'unknown'};
            }
        """, name)
        
        if result and result.get("found"):
            comp_type = result.get("type", "unknown")
            # 使用 count() 检查元素是否存在，不使用 wait_for（避免 React 重新渲染导致标记丢失后超时）
            loc = page.locator("[data-pw-fast='1']").first
            try:
                cnt = loc.count()
                print(f"[DEBUG fast_locate] target={name}, found=True, type={comp_type}, count={cnt}")
                if cnt == 0:
                    # 标记丢失（可能 React 重新渲染），回退到原来的定位逻辑
                    print(f"[DEBUG fast_locate] target={name}, 标记丢失，回退到原逻辑")
                    return None, None
            except Exception as e:
                print(f"[DEBUG fast_locate] target={name}, count异常: {e}")
                return None, None
            # 注意：不清除 data-pw-fast 标记！
            # Playwright 的 Locator 是惰性的，每次操作（fill/click）都会重新定位元素。
            # 如果清除标记，后续操作就会找不到元素，导致超时。
            # 标记会在下一次定位时被 JavaScript 自动清除。
            return loc, comp_type
        else:
            print(f"[DEBUG fast_locate] target={name}, found=False, result={result}")
    except Exception as e:
        print(f"[DEBUG fast_locate] target={name}, 异常: {type(e).__name__}: {e}")
    return None, None


def _close_modal_if_exists(page, timeout=1000):
    """
    检查页面上是否有模态弹窗（ant-modal-wrap）遮挡，如果有关闭按钮则尝试关闭。
    返回 True 表示有关闭弹窗，False 表示没有弹窗或无法关闭。
    """
    try:
        modals = page.locator(".ant-modal-wrap:not([style*='display: none'])")
        count = modals.count()
        if count == 0:
            return False
        print(f"[DEBUG modal] 发现 {count} 个模态弹窗，尝试关闭")
        # 从后往前关闭（最上层的先关）
        for i in range(count - 1, -1, -1):
            try:
                modal = modals.nth(i)
                # 查找关闭按钮
                close_btn = modal.locator(".ant-modal-close, .ant-modal-close-x, button[aria-label='Close'], .close")
                if close_btn.count() > 0:
                    close_btn.first.click(timeout=timeout)
                    page.wait_for_timeout(200)
                    print(f"[DEBUG modal] 已关闭第 {i+1} 个弹窗")
            except Exception as e:
                print(f"[DEBUG modal] 关闭第 {i+1} 个弹窗失败: {e}")
                continue
        return True
    except Exception as e:
        print(f"[DEBUG modal] 检查弹窗失败: {e}")
        return False


def _is_dropdown_select(page, locator):
    """
    判断定位到的元素是否是选择类组件（不能直接 fill，需要点击选择）。
    支持：Ant Design Select / Cascader / TreeSelect / Radio.Group / Checkbox.Group / Segmented
    """
    try:
        result = locator.evaluate("""
            (el) => {
                let node = el;
                for (let i = 0; i < 6 && node; i++) {
                    if (node.classList) {
                        const cls = node.className.toString();
                        if (cls.includes('ant-select') ||
                            cls.includes('ant-cascader') ||
                            cls.includes('ant-tree-select') ||
                            cls.includes('ant-radio-group') ||
                            cls.includes('ant-checkbox-group') ||
                            cls.includes('ant-segmented') ||
                            cls.includes('ant-picker')) {
                            return true;
                        }
                    }
                    node = node.parentElement;
                }
                return false;
            }
        """)
        return bool(result)
    except Exception:
        return False


def _get_component_type(page, locator):
    """获取组件类型：select / radio / cascader / checkbox / segmented / picker / unknown"""
    try:
        result = locator.evaluate("""
            (el) => {
                let node = el;
                for (let i = 0; i < 6 && node; i++) {
                    if (node.classList) {
                        const cls = node.className.toString();
                        if (cls.includes('ant-radio-group')) return 'radio';
                        if (cls.includes('ant-checkbox-group')) return 'checkbox';
                        if (cls.includes('ant-cascader')) return 'cascader';
                        if (cls.includes('ant-tree-select')) return 'tree-select';
                        if (cls.includes('ant-segmented')) return 'segmented';
                        if (cls.includes('ant-picker')) return 'picker';
                        if (cls.includes('ant-select')) return 'select';
                    }
                    node = node.parentElement;
                }
                return 'unknown';
            }
        """)
        return result or 'unknown'
    except Exception:
        return 'unknown'


def _get_select_clickable(page, locator):
    """
    获取下拉选择框的可点击元素（.ant-select-selector）。
    如果定位到的是隐藏的 input、ant-select-prefix 或其他子元素，返回其父级的可点击选择器。
    """
    try:
        # 在浏览器端检查并查找最近的可点击元素
        result = locator.evaluate("""
            (el) => {
                // 如果已经是 ant-select-selector，直接返回
                if (el.classList && el.classList.contains('ant-select-selector')) {
                    return {needConvert: false};
                }
                // 向上查找最近的 ant-select-selector
                let node = el;
                for (let i = 0; i < 10 && node; i++) {
                    if (node.classList && node.classList.contains('ant-select-selector')) {
                        node.setAttribute('data-pw-clickable', '1');
                        return {needConvert: true, found: true};
                    }
                    node = node.parentElement;
                }
                // 如果没找到 ant-select-selector，向上找 ant-select
                node = el;
                for (let i = 0; i < 10 && node; i++) {
                    if (node.classList && node.classList.contains('ant-select')) {
                        node.setAttribute('data-pw-clickable', '1');
                        return {needConvert: true, found: true};
                    }
                    node = node.parentElement;
                }
                return {needConvert: true, found: false};
            }
        """)
        if result and result.get("needConvert") and result.get("found"):
            new_loc = page.locator("[data-pw-clickable='1']").first
            try:
                new_loc.evaluate("el => el.removeAttribute('data-pw-clickable')")
            except Exception:
                pass
            return new_loc
        return locator
    except Exception:
        return locator


def _select_from_radio_group(page, locator, value, timeout=5000):
    """
    对 Ant Design Radio.Group 单选按钮组执行选择操作。
    优化：优先使用全局查找，减少层级和等待，提高速度。
    """
    # 方法1：全局查找包含目标文本的 radio-wrapper（最可靠、最快）
    selectors = [
        f".ant-radio-wrapper:has-text('{value}')",
        f"label.ant-radio-wrapper:has-text('{value}')",
        f".ant-radio-group .ant-radio-wrapper:has-text('{value}')",
    ]
    for selector in selectors:
        try:
            option = page.locator(selector).first
            if option.count() > 0:
                option.click(timeout=timeout, force=True)
                page.wait_for_timeout(100)
                return True
        except Exception:
            continue

    # 方法2：遍历所有可见的 radio-wrapper，匹配文本（备用）
    try:
        wrappers = page.locator(".ant-radio-wrapper:visible")
        count = wrappers.count()
        for i in range(count):
            try:
                wrapper = wrappers.nth(i)
                text = wrapper.inner_text(timeout=300).strip()
                if value in text or text in value:
                    wrapper.click(timeout=timeout, force=True)
                    page.wait_for_timeout(100)
                    return True
            except Exception:
                continue
    except Exception:
        pass

    raise RuntimeError(f"在单选按钮组中找不到选项: '{value}'")


def _select_from_cascader(page, locator, value, timeout=10000):
    """
    对 Ant Design Cascader 级联选择器执行选择操作。
    支持多级选择，输入值用 / 或 , 分隔各级，例如："江苏省 / 南京市 / 玄武区"
    """
    clickable = _get_select_clickable(page, locator)

    # 点击展开级联菜单
    try:
        clickable.click(timeout=timeout)
    except Exception as e:
        raise RuntimeError(f"点击级联选择器失败: {e}")

    # 等待级联菜单出现
    try:
        page.wait_for_selector(".ant-cascader-menu, .ant-cascader-dropdown", timeout=timeout, state="visible")
    except Exception:
        page.wait_for_timeout(300)

    # 解析各级选项（支持 / 或 , 分隔）
    levels = [v.strip() for v in value.replace(",", "/").split("/") if v.strip()]
    if not levels:
        levels = [value]

    # 逐级选择
    for level_idx, level_value in enumerate(levels):
        try:
            # 等待当前级菜单稳定
            page.wait_for_timeout(200)

            # 查找当前级菜单中匹配的选项
            menus = page.locator(".ant-cascader-menu")
            menu_count = menus.count()
            # 选择当前级菜单（最后一个出现的菜单）
            current_menu = menus.nth(min(level_idx, menu_count - 1)) if menu_count > 0 else page.locator(".ant-cascader-menu").first

            options = current_menu.locator(".ant-cascader-menu-item")
            opt_count = options.count()
            found = False
            for i in range(opt_count):
                try:
                    opt = options.nth(i)
                    text = opt.inner_text(timeout=1000).strip()
                    if level_value in text or text in level_value:
                        opt.click(timeout=5000)
                        found = True
                        break
                except Exception:
                    continue

            if not found:
                # 备用：全局查找
                try:
                    opt = page.locator(f".ant-cascader-menu-item:has-text('{level_value}')").first
                    if opt.count() > 0 and opt.is_visible():
                        opt.click(timeout=5000)
                        found = True
                except Exception:
                    pass

            if not found:
                raise RuntimeError(f"在级联选择器第 {level_idx+1} 级找不到选项: '{level_value}'")

            # 如果不是最后一级，等待下一级菜单出现
            if level_idx < len(levels) - 1:
                page.wait_for_timeout(300)

        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"级联选择器第 {level_idx+1} 级选择失败: {e}")

    page.wait_for_timeout(300)
    return True


def _select_from_dropdown_fast(page, locator, value, timeout=5000):
    """
    快速下拉选择：优化版本，减少不必要的调用和等待。
    假设 locator 已经是可点击元素（ant-select-selector 或 ant-select 容器）。
    """
    # 1. 确保 locator 是可点击的 selector（如果是 ant-select 容器，找到 selector）
    try:
        is_select_container = locator.evaluate("el => el.classList && el.classList.contains('ant-select')")
        if is_select_container:
            selector = locator.locator(".ant-select-selector").first
            if selector.count() > 0:
                locator = selector
    except Exception:
        pass

    # 2. 点击展开下拉（force=True，减少可见性检查）
    try:
        locator.click(timeout=timeout, force=True)
    except Exception:
        try:
            locator.click(timeout=timeout)
        except Exception as e:
            raise RuntimeError(f"点击下拉选择框失败: {e}")

    # 3. 短暂等待下拉展开（不使用 wait_for_selector，减少超时等待）
    page.wait_for_timeout(150)

    # 4. 直接查找匹配的选项（优先最常用的选择器）
    option_selectors = [
        f".ant-select-item-option:has-text('{value}')",
        f".ant-select-item:has-text('{value}')",
        f"[role='option']:has-text('{value}')",
    ]

    for selector in option_selectors:
        try:
            option = page.locator(selector).first
            if option.count() > 0:
                option.click(timeout=1500, force=True)
                page.wait_for_timeout(80)
                return True
        except Exception:
            continue

    # 5. 备用：遍历所有选项模糊匹配
    try:
        all_options = page.locator(".ant-select-item, .ant-select-item-option, [role='option']")
        count = all_options.count()
        for i in range(count):
            try:
                opt = all_options.nth(i)
                text = opt.inner_text(timeout=200).strip()
                if value in text or text in value:
                    opt.click(timeout=1500, force=True)
                    page.wait_for_timeout(80)
                    return True
            except Exception:
                continue
    except Exception:
        pass

    raise RuntimeError(f"在下拉选择框中找不到选项: '{value}'")


def _select_from_dropdown(page, locator, value, timeout=5000):
    """
    对选择类组件执行选择操作，根据组件类型自动分发：
    - radio: 单选按钮组
    - cascader: 级联选择器
    - select/tree-select/segmented/picker: 下拉选择框
    优化：减少等待和可见性检查，提高速度。
    """
    # 获取组件类型，自动分发
    comp_type = _get_component_type(page, locator)

    if comp_type == "radio":
        return _select_from_radio_group(page, locator, value, timeout=timeout)

    if comp_type == "cascader":
        return _select_from_cascader(page, locator, value, timeout=timeout)

    # select / tree-select / segmented / picker 等下拉类组件
    clickable = _get_select_clickable(page, locator)

    # 1. 点击展开下拉（使用 force=True 强制点击）
    try:
        clickable.click(timeout=timeout, force=True)
    except Exception as e:
        try:
            clickable.click(timeout=timeout)
        except Exception as e2:
            raise RuntimeError(f"点击下拉选择框失败: {e2}")

    # 2. 等待下拉选项出现（缩短等待时间）
    try:
        page.wait_for_selector(".ant-select-dropdown, .ant-select-item, .ant-select-item-option", timeout=min(timeout, 3000), state="visible")
    except Exception:
        page.wait_for_timeout(150)

    # 3. 尝试查找匹配的选项（优化：不检查可见性，减少调用）
    option_selectors = [
        f".ant-select-item-option:has-text('{value}')",
        f".ant-select-item:has-text('{value}')",
        f"[role='option']:has-text('{value}')",
        f".ant-segmented-item:has-text('{value}')",
    ]

    for selector in option_selectors:
        try:
            option = page.locator(selector).first
            if option.count() > 0:
                option.click(timeout=2000, force=True)
                page.wait_for_timeout(100)
                return True
        except Exception:
            continue

    # 4. 模糊匹配（包含文本）
    try:
        all_options = page.locator(".ant-select-item, .ant-select-item-option, [role='option'], .ant-segmented-item")
        count = all_options.count()
        for i in range(count):
            try:
                opt = all_options.nth(i)
                text = opt.inner_text(timeout=300).strip()
                if value in text or text in value:
                    opt.click(timeout=2000, force=True)
                    page.wait_for_timeout(100)
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # 5. 可搜索的 Select：在搜索框输入过滤
    try:
        search_input = page.locator(".ant-select-search input, .ant-select-selection-search-input").first
        if search_input.count() > 0:
            search_input.fill(value, timeout=1500)
            page.wait_for_timeout(200)
            for selector in option_selectors[:2]:
                try:
                    option = page.locator(selector).first
                    if option.count() > 0:
                        option.click(timeout=2000, force=True)
                        page.wait_for_timeout(100)
                        return True
                except Exception:
                    continue
    except Exception:
        pass

    raise RuntimeError(f"在下拉选择框中找不到选项: '{value}'")


def _find_adjacent_input(page, name: str):
    """
    找到包含目标文本的标签元素，然后在其周围容器中查找可交互元素。
    适配 Ant Design（Input/Select/DatePicker/InputNumber）、标准 label、传统 table、div 布局等。
    """
    try:
        js_code = r"""
        (target) => {
            target = target.replace(/\s/g, '');
            const debug = {target: target, antLabels: [], candidates: [], found: false, foundBy: ''};
            if (!target) return {found: false, debug: debug};

            // 先清除旧的临时属性
            try { document.querySelectorAll('[data-pw-input]').forEach(el => el.removeAttribute('data-pw-input')); } catch(e) {}

            const isVisible = (el) => {
                if (!el) return false;
                try {
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetWidth > 0;
                } catch(e) { return false; }
            };

            // 判断元素是否是可交互的表单元素（包括 Ant Design 组件）
            const isInteractive = (el) => {
                if (!el) return false;
                const tag = el.tagName.toLowerCase();
                // 标准表单元素
                if (tag === 'input') {
                    if (['hidden','button','submit','reset'].includes(el.type)) return false;
                    // radio/checkbox 也认为是可交互的（Radio.Group/Checkbox.Group）
                    if (['radio','checkbox'].includes(el.type)) {
                        // 如果在 ant-radio-wrapper/ant-checkbox-wrapper 中，返回 wrapper（更易点击）
                        const wrapper = el.closest('.ant-radio-wrapper, .ant-checkbox-wrapper');
                        if (wrapper && isVisible(wrapper)) return true;
                        return isVisible(el);
                    }
                    // Ant Design Select 的搜索 input 是隐藏的，但它的父级 .ant-select 是可点击的
                    if (el.closest('.ant-select')) return true;
                    return isVisible(el);
                }
                if (tag === 'textarea' || tag === 'select') return isVisible(el);
                // Ant Design 组件的可点击区域
                if (el.classList && (
                    el.classList.contains('ant-select-selector') ||
                    el.classList.contains('ant-picker') ||
                    el.classList.contains('ant-picker-input') ||
                    el.classList.contains('ant-input-number') ||
                    el.classList.contains('ant-cascader-picker') ||
                    el.classList.contains('ant-radio-wrapper') ||
                    el.classList.contains('ant-checkbox-wrapper') ||
                    el.classList.contains('ant-radio') ||
                    el.classList.contains('ant-checkbox')
                )) return isVisible(el);
                // contenteditable
                if (el.getAttribute && el.getAttribute('contenteditable') === 'true') return isVisible(el);
                return false;
            };

            // 在容器中查找可交互元素
            const findInteractiveInContainer = (container) => {
                if (!container) return null;
                // 1. 先找标准 input/textarea/select
                const inputs = container.querySelectorAll('input, textarea, select');
                for (const inp of inputs) {
                    if (isInteractive(inp)) {
                        // 如果是 Ant Design Select 的隐藏 input，返回它的父级 .ant-select-selector
                        if (inp.closest('.ant-select')) {
                            const selector = inp.closest('.ant-select').querySelector('.ant-select-selector');
                            if (selector && isVisible(selector)) return selector;
                            return inp.closest('.ant-select');
                        }
                        // 如果是 radio/checkbox，返回它的 wrapper（更易点击）
                        if (['radio','checkbox'].includes(inp.type)) {
                            const wrapper = inp.closest('.ant-radio-wrapper, .ant-checkbox-wrapper');
                            if (wrapper && isVisible(wrapper)) return wrapper;
                        }
                        return inp;
                    }
                }
                // 2. 找 Ant Design 组件的可点击区域
                const antComponents = container.querySelectorAll(
                    '.ant-select-selector, .ant-picker, .ant-picker-input, .ant-input-number, .ant-cascader-picker, ' +
                    '.ant-radio-wrapper, .ant-checkbox-wrapper, .ant-radio, .ant-checkbox, [contenteditable="true"]'
                );
                for (const comp of antComponents) {
                    if (isInteractive(comp)) return comp;
                }
                return null;
            };

            const markAndReturn = (el, by) => {
                el.setAttribute('data-pw-input', '1');
                return {found: true, foundBy: by, debug: debug};
            };

            // 文本匹配函数（支持去冒号、包含匹配）
            const textMatches = (text, target) => {
                if (!text) return false;
                const clean = text.replace(/\s/g, '').replace(/[：:]$/, '');
                return clean === target || clean.includes(target) || target.includes(clean);
            };

            // ========== 策略1: Ant Design 专门策略 ==========
            const antLabels = document.querySelectorAll('label.ant-form-item-label, .ant-form-item-label label, .ant-form-item-label > label');
            debug.antLabelCount = antLabels.length;
            for (const lbl of antLabels) {
                let text = '';
                try { text = lbl.innerText || lbl.textContent || ''; } catch(e) { text = lbl.textContent || ''; }
                const labelInfo = {text: text.replace(/\s/g, ''), matched: textMatches(text, target)};
                if (textMatches(text, target)) {
                    // 向上找 .ant-form-item 容器
                    let container = lbl.closest('.ant-form-item');
                    labelInfo.hasContainer = !!container;
                    if (!container) {
                        let p = lbl.parentElement;
                        for (let i = 0; i < 8 && p; i++) {
                            if (p.classList && p.classList.contains('ant-form-item')) { container = p; break; }
                            p = p.parentElement;
                        }
                    }
                    if (container) {
                        const interactive = findInteractiveInContainer(container);
                        if (interactive) {
                            debug.found = true;
                            debug.foundBy = 'ant-design';
                            return markAndReturn(interactive, 'ant-design');
                        }
                    }
                }
                debug.antLabels.push(labelInfo);
            }

            // ========== 策略2: 通用 label 元素 ==========
            const allLabels = document.querySelectorAll('label');
            for (const lbl of allLabels) {
                if (lbl.closest('.ant-form-item-label')) continue; // 已经在策略1处理过
                let text = '';
                try { text = lbl.innerText || lbl.textContent || ''; } catch(e) { text = lbl.textContent || ''; }
                if (textMatches(text, target) && isVisible(lbl)) {
                    // 标准 label for 属性
                    const forId = lbl.getAttribute('for');
                    if (forId) {
                        const targetEl = document.getElementById(forId);
                        if (targetEl && isInteractive(targetEl)) {
                            return markAndReturn(targetEl, 'label-for');
                        }
                    }
                    // 向上找父容器
                    let container = lbl.parentElement;
                    for (let i = 0; i < 6 && container; i++) {
                        const interactive = findInteractiveInContainer(container);
                        if (interactive) return markAndReturn(interactive, 'label-parent');
                        container = container.parentElement;
                    }
                }
            }

            // ========== 策略3: 通用文本元素 + 父级容器查找 ==========
            const all = document.querySelectorAll('span, div, td, th, p, strong, b');
            const candidates = [];
            for (const el of all) {
                let text = '';
                try { text = el.innerText || el.textContent || ''; } catch(e) { text = el.textContent || ''; }
                if (!text) continue;
                if (textMatches(text, target)) {
                    if (isVisible(el) && text.replace(/\s/g, '').length <= target.length * 10) {
                        candidates.push({el: el, textLen: text.replace(/\s/g, '').length, tag: el.tagName.toLowerCase()});
                    }
                }
            }

            // 排序：文本越短越精确，label/span/td 优先
            const priorityTags = ['label', 'span', 'td', 'th', 'strong', 'b', 'p'];
            candidates.sort((a, b) => {
                const aScore = priorityTags.includes(a.tag) ? 0 : 1;
                const bScore = priorityTags.includes(b.tag) ? 0 : 1;
                if (aScore !== bScore) return aScore - bScore;
                return a.textLen - b.textLen;
            });

            for (const cand of candidates) {
                let parent = cand.el;
                for (let i = 0; i < 8 && parent; i++) {
                    const interactive = findInteractiveInContainer(parent);
                    if (interactive) return markAndReturn(interactive, 'generic-' + cand.tag);
                    parent = parent.parentElement;
                }
            }

            return {found: false, debug: debug};
        }
        """
        result = page.evaluate(js_code, name)
        print(f"[DEBUG _find_adjacent_input] target={name}, result={result}")
        if result and result.get("found"):
            # 等待元素出现在 DOM 中
            try:
                loc = page.locator("[data-pw-input='1']").first
                loc.wait_for(state="attached", timeout=2000)
                if loc.count() > 0:
                    return loc
            except Exception as e:
                print(f"[DEBUG _find_adjacent_input] wait_for failed: {e}")
    except Exception as e:
        print(f"[DEBUG _find_adjacent_input] EXCEPTION: {type(e).__name__}: {e}")
    return None


def _fuzzy_text_match(page, name: str):
    """
    模糊文本匹配：通过 page.evaluate 在页面端遍历所有元素，
    用 innerText（包含所有子元素文本）去空格后匹配，
    收集所有候选元素，按文本长度排序，优先选择最短（最精确）的匹配。
    适配"登 录"这类文本中间有空格、或文本被拆分成多个子元素的情况。
    """
    try:
        js_code = """
        (target) => {
            target = target.replace(/\\s/g, '');
            if (!target) return null;

            // 先清除旧的临时属性
            try { document.querySelectorAll('[data-pw-found]').forEach(el => el.removeAttribute('data-pw-found')); } catch(e) {}

            const isClickable = (el) => {
                const tag = el.tagName.toLowerCase();
                return tag === 'button' || tag === 'a' ||
                    (tag === 'input' && ['submit','button','reset'].includes(el.type)) ||
                    el.getAttribute('role') === 'button' || el.hasAttribute('onclick');
            };

            // 收集所有匹配元素
            const candidates = [];
            const all = document.querySelectorAll('*');
            for (const el of all) {
                // 用 innerText 获取包含子元素的完整文本
                let text = '';
                try { text = el.innerText || el.textContent || ''; } catch(e) { text = el.textContent || ''; }
                text = text.replace(/\\s/g, '');
                const value = (el.value || '').replace(/\\s/g, '');
                const aria = (el.getAttribute('aria-label') || '').replace(/\\s/g, '');
                const title = (el.getAttribute('title') || '').replace(/\\s/g, '');
                const combined = text + value + aria + title;

                if (combined.includes(target)) {
                    // 避免匹配到外层大容器：文本长度不超过目标的 8 倍
                    if (text.length > 0 && text.length <= target.length * 8) {
                        // 检查是否可见
                        const style = window.getComputedStyle(el);
                        const visible = style.display !== 'none' && style.visibility !== 'hidden' && el.offsetWidth > 0;
                        if (visible) {
                            candidates.push({
                                el: el,
                                textLen: text.length,
                                clickable: isClickable(el),
                                tag: el.tagName.toLowerCase()
                            });
                        }
                    }
                }
            }

            if (candidates.length === 0) return null;

            // 排序：可点击元素优先，然后按文本长度升序（越短越精确）
            candidates.sort((a, b) => {
                if (a.clickable !== b.clickable) return b.clickable - a.clickable;
                return a.textLen - b.textLen;
            });

            const best = candidates[0];
            best.el.setAttribute('data-pw-found', '1');
            return {tag: best.tag, clickable: best.clickable, textLen: best.textLen, total: candidates.length};
        }
        """
        result = page.evaluate(js_code, name)
        if result:
            loc = page.locator("[data-pw-found='1']").first
            if loc.count() > 0:
                return loc
    except Exception:
        pass
    return None


def locate_element(page, target: str, action: str = None):
    """
    根据操作对象（业务描述，如"用户名"、"登录按钮"）自动定位页面元素。

    参数:
        action: 当前操作类型（input/click/clear/select 等）。
                对于 input/clear/select 等需要可编辑元素的操作，
                只返回 input/textarea/select/contenteditable 元素，
                避免定位到标签 span 等不可编辑元素。

    定位策略（按优先级，对每个候选名称依次尝试）：
    1. get_by_role（按钮类优先 button role，输入类优先 textbox）
    2. get_by_label
    3. get_by_placeholder
    4. get_by_text
    5. 标签相邻输入框（输入类操作专用，适配 Ant Design span+input 布局）
    6. Playwright text= 选择器（text=登录）
    7. CSS：input[type=submit][value*=]、button:has-text()、#id、[name=]、.class
    8. CSS 属性匹配：[aria-label*=]、[title*=]
    9. XPath：button[contains(text())]、input[@type=submit][contains(@value)]、a[contains(text())]
    10. 模糊文本匹配（去空格，适配"登 录"）

    对"XX按钮"这类操作对象，会自动剥离"按钮"后缀后用"XX"匹配。
    定位失败抛出 ValueError。
    """
    if not target:
        raise ValueError("操作对象为空，无法定位元素")

    target = target.strip()
    candidates = _candidate_names(target)
    is_button_like = target.endswith("按钮") or "按钮" in target
    need_editable = action in ("input", "clear", "select")
    print(f"[DEBUG locate_element] target={target}, action={action}, need_editable={need_editable}, candidates={candidates}")

    def _check(loc):
        """内部辅助：如果需要可编辑元素，检查后返回；否则直接返回"""
        if not loc:
            return None
        # 如果定位到的是 ant-select-prefix（前缀文字）等不可点击元素，自动转换为可点击的 ant-select-selector
        try:
            # 在浏览器端检查并查找最近的可点击元素
            result = loc.evaluate("""
                (el) => {
                    // 检查是否是需要转换的元素
                    const needConvert = el.classList && (
                        el.classList.contains('ant-select-prefix') ||
                        el.classList.contains('ant-select-selection-item') ||
                        el.classList.contains('ant-select-selection-placeholder') ||
                        el.classList.contains('ant-select-arrow')
                    );
                    if (!needConvert) {
                        return {needConvert: false};
                    }
                    // 向上查找最近的 ant-select-selector
                    let node = el;
                    for (let i = 0; i < 10 && node; i++) {
                        if (node.classList && node.classList.contains('ant-select-selector')) {
                            // 给目标元素添加临时标记
                            node.setAttribute('data-pw-clickable', '1');
                            return {needConvert: true, found: true};
                        }
                        node = node.parentElement;
                    }
                    // 如果没找到 ant-select-selector，向上找 ant-select
                    node = el;
                    for (let i = 0; i < 10 && node; i++) {
                        if (node.classList && node.classList.contains('ant-select')) {
                            node.setAttribute('data-pw-clickable', '1');
                            return {needConvert: true, found: true};
                        }
                        node = node.parentElement;
                    }
                    return {needConvert: true, found: false};
                }
            """)
            if result and result.get("needConvert"):
                if result.get("found"):
                    # 通过临时标记获取可点击元素
                    new_loc = page.locator("[data-pw-clickable='1']").first
                    # 清除临时标记
                    try:
                        new_loc.evaluate("el => el.removeAttribute('data-pw-clickable')")
                    except Exception:
                        pass
                    loc = new_loc
        except Exception:
            pass
        if need_editable and not _is_editable(page, loc):
            return None
        return loc

    for name in candidates:
        # === 策略 1: get_by_role（优化：输入类只尝试最可能的 2 个 role，减少调用次数）===
        if is_button_like:
            loc = _check(_try_locator(page, lambda: page.get_by_role("button", name=name, exact=False)))
            if loc:
                return loc

        # 输入类操作只尝试 textbox 和 combobox，其他 role 对 Ant Design 表单意义不大
        if need_editable:
            for role in ["textbox", "combobox"]:
                loc = _check(_try_locator(page, lambda r=role: page.get_by_role(r, name=name, exact=False)))
                if loc:
                    return loc
        else:
            # 非输入类操作尝试更多 role
            role_list = ["button", "link", "checkbox", "radio", "menuitem", "tab", "heading"]
            for role in role_list:
                loc = _check(_try_locator(page, lambda r=role: page.get_by_role(r, name=name, exact=False)))
                if loc:
                    return loc

        # === 策略 2: 标签相邻输入框（输入类操作专用，对 Ant Design 表单最可靠，提前）===
        if need_editable:
            loc = _find_adjacent_input(page, name)
            if loc:
                return loc

        # === 策略 3: get_by_label ===
        loc = _check(_try_locator(page, lambda: page.get_by_label(name, exact=False)))
        if loc:
            return loc

        # === 策略 4: get_by_placeholder ===
        loc = _check(_try_locator(page, lambda: page.get_by_placeholder(name, exact=False)))
        if loc:
            return loc

        # === 策略 5: get_by_text ===
        loc = _check(_try_locator(page, lambda: page.get_by_text(name, exact=False)))
        if loc:
            return loc

        # === 策略 6: Playwright text= 选择器 ===
        loc = _check(_try_locator(page, lambda: page.locator(f"text={name}")))
        if loc:
            return loc

        # === 策略 7: CSS 选择器 ===
        import re
        safe = re.sub(r"[^\w\-]", "_", name)
        css_selectors = [
            f"input[type='submit'][value*='{name}']",
            f"input[type='button'][value*='{name}']",
            f"button:has-text('{name}')",
            f"a:has-text('{name}')",
            f"#{safe}",
            f"[name='{name}']",
            f".{safe}",
        ]
        for selector in css_selectors:
            loc = _check(_try_locator(page, lambda s=selector: page.locator(s)))
            if loc:
                return loc

        # === 策略 8: CSS 属性匹配（aria-label / title） ===
        for attr in ["aria-label", "title", "placeholder"]:
            loc = _check(_try_locator(page, lambda a=attr: page.locator(f"[{a}*='{name}']")))
            if loc:
                return loc

        # === 策略 9: XPath ===
        xpath_selectors = [
            f"//button[contains(normalize-space(text()), '{name}')]",
            f"//a[contains(normalize-space(text()), '{name}')]",
            f"//input[@type='submit'][contains(@value, '{name}')]",
            f"//input[@type='button'][contains(@value, '{name}')]",
            f"//span[contains(normalize-space(text()), '{name}')]",
            f"//*[contains(normalize-space(text()), '{name}')]",
        ]
        for xp in xpath_selectors:
            loc = _check(_try_locator(page, lambda x=xp: page.locator(x)))
            if loc:
                return loc

        # === 策略 10: 模糊文本匹配（去空格，适配"登 录"） ===
        loc = _check(_fuzzy_text_match(page, name))
        if loc:
            return loc

    # === 最后手段：按钮类操作对象，尝试点击页面上最后一个可见按钮 ===
    # 登录按钮通常是表单的最后一个可点击元素
    if is_button_like and not need_editable:
        for selector in [
            "button:visible",
            "input[type='submit']:visible",
            "input[type='button']:visible",
            "[role='button']:visible",
        ]:
            try:
                loc = page.locator(selector)
                count = loc.count()
                if count > 0:
                    # 从后往前找第一个可见的
                    for i in range(count - 1, -1, -1):
                        try:
                            if loc.nth(i).is_visible():
                                return loc.nth(i)
                        except Exception:
                            pass
            except Exception:
                pass

    raise ValueError(f"无法定位元素：{target}")


# ---------------- URL 拼接 ----------------

def _resolve_url(base_url: str, path_or_url: str) -> str:
    """
    将测试用例中的输入值（可能是相对路径如 /login，也可能是完整 URL）
    与测试环境的 Base URL 拼接成完整 URL。
    """
    if not path_or_url:
        return base_url
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    base = base_url.rstrip("/")
    path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
    return f"{base}{path}"


# ---------------- 自动登录 ----------------

def _try_fill(page, candidates: list, value: str, timeout: int = 5000) -> bool:
    """尝试用多个候选名称定位输入框并填充值，成功返回 True"""
    for name in candidates:
        try:
            loc = locate_element(page, name, "input")
            loc.fill(value, timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _try_click(page, candidates: list, timeout: int = 5000) -> bool:
    """尝试用多个候选名称定位按钮并点击，成功返回 True"""
    for name in candidates:
        try:
            loc = locate_element(page, name, "click")
            loc.click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _auto_login(page, env) -> tuple:
    """
    自动完成登录流程。从测试环境获取客户名、用户名、密码。
    返回 (success: bool, message: str)
    """
    if not env.username or not env.password:
        return False, "测试环境未配置用户名或密码，无法自动登录"

    login_url = _resolve_url(env.base_url, env.login_path or "/#/login")

    # 1. 打开登录页面
    try:
        page.goto(login_url, timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(500)
    except Exception as e:
        return False, f"无法打开登录页面：{login_url}（{e}）"

    # 2. 输入客户名（如果配置了）
    if env.customer_name:
        ok = _try_fill(page, ["客户名", "客户名输入框", "客户编号", "租户", "租户名", "客户"], env.customer_name)
        if not ok:
            return False, "无法定位客户名输入框"

    # 3. 输入用户名
    ok = _try_fill(page, ["用户名", "用户名输入框", "账号", "登录名", "用户"], env.username)
    if not ok:
        return False, "无法定位用户名输入框"

    # 4. 输入密码
    ok = _try_fill(page, ["密码", "密码输入框", "口令"], env.password)
    if not ok:
        return False, "无法定位密码输入框"

    # 5. 点击登录按钮
    ok = _try_click(page, ["登录按钮", "登录", "登 录", "提交", "立即登录", "确认登录"])
    if not ok:
        return False, "无法定位登录按钮"

    # 6. 等待登录完成（URL 变化或页面加载）
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(800)

    return True, "自动登录成功"


# ---------------- 单步执行 ----------------

def execute_step(page, step, base_url: str) -> tuple:
    """
    执行单步测试，返回 (status, message)。
    status: "passed" / "failed"
    """
    action = step.action or ""
    target = step.target or ""
    value = step.input_value or ""
    timeout = (step.wait_timeout or 8) * 1000

    if action == "open_url":
        url = _resolve_url(base_url, value)
        try:
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(300)
        except Exception as e:
            return "failed", f"无法访问测试环境：{url}（{e}）"
        return "passed", f"已打开 {url}"

    if action == "input":
        # 优先使用快速定位（一次 JS 调用完成定位+组件类型判断，大幅减少延迟）
        loc, comp_type = _locate_form_field_fast(page, target)
        
        if loc and comp_type in ("select", "radio", "cascader", "picker", "checkbox"):
            # 快速定位到选择类组件，直接执行下拉选择，跳过 _is_dropdown_select 和 _get_component_type
            if comp_type == "radio":
                _select_from_radio_group(page, loc, value, timeout=timeout)
            elif comp_type == "cascader":
                _select_from_cascader(page, loc, value, timeout=timeout)
            else:
                # select / picker / checkbox 都使用通用下拉选择
                _select_from_dropdown_fast(page, loc, value, timeout=timeout)
            return "passed", f"在「{target}」选择: {value}"
        
        if not loc:
            # 快速定位失败，回退到原来的定位逻辑
            loc = locate_element(page, target, "input")
            # 自动识别下拉选择框
            if _is_dropdown_select(page, loc):
                _select_from_dropdown(page, loc, value, timeout=timeout)
                return "passed", f"在「{target}」下拉选择: {value}"
        
        # 普通输入框
        loc.fill(value, timeout=timeout)
        return "passed", f"在「{target}」输入: {value}"

    if action == "click":
        loc = locate_element(page, target, "click")
        loc.click(timeout=timeout)
        return "passed", f"点击成功：{target}"

    if action == "clear":
        loc = locate_element(page, target, "clear")
        loc.fill("", timeout=timeout)
        return "passed", f"已清空「{target}」"

    if action == "select":
        # 优先使用快速定位（一次 JS 调用完成定位+组件类型判断，大幅减少延迟）
        loc, comp_type = _locate_form_field_fast(page, target)
        
        if loc and comp_type in ("select", "radio", "cascader", "picker", "checkbox"):
            # 快速定位到选择类组件，直接执行下拉选择
            if comp_type == "radio":
                _select_from_radio_group(page, loc, value, timeout=timeout)
            elif comp_type == "cascader":
                _select_from_cascader(page, loc, value, timeout=timeout)
            else:
                # select / picker / checkbox 都使用通用下拉选择
                _select_from_dropdown_fast(page, loc, value, timeout=timeout)
            return "passed", f"在「{target}」选择: {value}"
        
        if not loc:
            # 快速定位失败，回退到原来的定位逻辑
            loc = locate_element(page, target, "select")
        
        # 自动识别下拉选择框：Ant Design Select 使用自定义选择逻辑
        if _is_dropdown_select(page, loc):
            _select_from_dropdown(page, loc, value, timeout=timeout)
            return "passed", f"在「{target}」下拉选择: {value}"
        # 原生 select 元素
        loc.select_option(label=value, timeout=timeout)
        return "passed", f"已选择: {value}"

    if action == "hover":
        loc = locate_element(page, target, "hover")
        loc.hover(timeout=timeout)
        return "passed", f"已悬停「{target}」"

    if action == "assert_text":
        loc = locate_element(page, target, "assert_text")
        actual = loc.inner_text(timeout=timeout)
        if value and value not in actual:
            return "failed", f"期望包含文本 '{value}'，实际为 '{actual}'"
        return "passed", f"「{target}」实际文本: {actual}"

    if action == "assert_element_exists":
        loc = locate_element(page, target, "assert_element_exists")
        loc.wait_for(state="visible", timeout=timeout)
        return "passed", f"元素「{target}」存在"

    if action == "assert_element_not_exists":
        try:
            loc = locate_element(page, target, "assert_element_not_exists")
            if loc.count() > 0 and loc.first.is_visible():
                return "failed", f"元素「{target}」不应存在，但找到了"
        except ValueError:
            pass  # 找不到就是不存在，符合预期
        return "passed", f"元素「{target}」不存在（符合预期）"

    if action == "assert_title":
        actual = page.title()
        if value and value not in actual:
            return "failed", f"期望标题包含 '{value}'，实际为 '{actual}'"
        return "passed", f"实际标题: {actual}"

    if action == "sleep":
        seconds = float(value) if value else 1
        page.wait_for_timeout(int(seconds * 1000))
        return "passed", f"等待 {seconds} 秒"

    if action == "refresh":
        page.reload(timeout=30000)
        return "passed", "已刷新页面"

    if action == "go_back":
        page.go_back(timeout=30000)
        return "passed", "已返回上一页"

    if action == "scroll_to":
        loc = locate_element(page, target, "scroll_to")
        loc.scroll_into_view_if_needed(timeout=timeout)
        return "passed", f"已滚动到「{target}」"

    if action == "press_key":
        key = value or "Enter"
        if target:
            loc = locate_element(page, target, "press_key")
            loc.press(key, timeout=timeout)
        else:
            page.keyboard.press(key)
        return "passed", f"已按下 {key}"

    if action == "screenshot":
        return "passed", "已截图"  # 截图在主循环中统一处理

    return "failed", f"未知操作类型: {action}"


# ---------------- 主执行流程 ----------------

def execute_run(run_id: int, session_factory, headless: bool = False):
    """
    在后台线程中执行一次完整的测试运行。
    使用本机 Playwright 启动 Chromium，访问测试环境，按步骤执行。
    headless: 是否使用无头模式（默认 False，有头模式显示浏览器窗口）
    """
    db: Session = session_factory()
    try:
        run = db.query(models.TestRun).filter(models.TestRun.id == run_id).first()
        if not run:
            return
        run.status = models.RunStatus.RUNNING
        run.start_time = datetime.datetime.utcnow()
        db.commit()

        env = db.query(models.TestServer).filter(models.TestServer.id == run.server_id).first()
        test_case = db.query(models.TestCase).filter(models.TestCase.id == run.test_case_id).first()

        overall_status = models.RunStatus.PASSED
        try:
            # 执行前检查本机 Playwright 环境
            local_env = check_local_env()
            if not local_env["ok"]:
                raise RuntimeError(
                    "当前Testbench执行环境未安装Playwright运行环境，请先完成本机环境初始化。"
                )

            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                # 根据用户选择的浏览器类型启动
                browser_type = (run.browser or "chromium").lower()
                # 有头模式：显示浏览器窗口，方便观察执行过程
                # 无头模式：后台静默执行，不显示窗口，适合批量执行
                # slow_mo：每步操作延迟，有头模式便于观察，无头模式可以设为 0 提高速度
                launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
                if not headless:
                    launch_args.append("--start-maximized")
                slow_mo_val = 0 if headless else 50

                if browser_type == "chrome":
                    browser = p.chromium.launch(
                        headless=headless, slow_mo=slow_mo_val, channel="chrome", args=launch_args,
                    )
                elif browser_type in ("edge", "msedge"):
                    browser = p.chromium.launch(
                        headless=headless, slow_mo=slow_mo_val, channel="msedge", args=launch_args,
                    )
                elif browser_type == "firefox":
                    browser = p.firefox.launch(
                        headless=headless, slow_mo=slow_mo_val,
                    )
                else:
                    # 默认 chromium
                    browser = p.chromium.launch(
                        headless=headless, slow_mo=slow_mo_val, args=launch_args,
                    )
                # 无头模式下需要设置视口大小，有头模式使用 no_viewport 最大化
                if headless:
                    context = browser.new_context(viewport={"width": 1920, "height": 1080})
                else:
                    context = browser.new_context(no_viewport=True)
                page = context.new_page()

                # 根据登录模式决定是否自动登录
                login_mode = getattr(test_case, 'login_mode', 'auto') or 'auto'
                if login_mode in ('auto', 'reuse'):
                    login_ok, login_msg = _auto_login(page, env)
                    if not login_ok:
                        raise RuntimeError(f"自动登录失败：{login_msg}")

                for i, step in enumerate(test_case.steps):
                    result = models.StepResult(
                        run_id=run.id, step_id=step.id, step_order=step.step_order,
                        action=step.action, status=models.StepStatus.PENDING,
                    )
                    db.add(result)
                    db.flush()

                    t0 = time.time()
                    try:
                        status, msg = execute_step(page, step, env.base_url)
                        result.status = models.StepStatus.PASSED if status == "passed" else models.StepStatus.FAILED
                        result.actual_result = msg
                        if step.action == "screenshot" or status == "failed":
                            result.screenshot_path = _save_screenshot(page, run.id, step.step_order)
                        if status == "failed":
                            overall_status = models.RunStatus.FAILED
                            result.duration_ms = int((time.time() - t0) * 1000)
                            db.commit()
                            # 后续步骤标记为跳过
                            for remaining in test_case.steps:
                                if remaining.step_order > step.step_order:
                                    db.add(models.StepResult(
                                        run_id=run.id, step_id=remaining.id,
                                        step_order=remaining.step_order, action=remaining.action,
                                        status=models.StepStatus.SKIPPED,
                                    ))
                            db.commit()
                            break
                    except ValueError as e:
                        # 元素定位失败
                        result.status = models.StepStatus.FAILED
                        result.actual_result = str(e)
                        result.screenshot_path = _save_screenshot(page, run.id, step.step_order)
                        overall_status = models.RunStatus.FAILED
                        result.duration_ms = int((time.time() - t0) * 1000)
                        db.commit()
                        for remaining in test_case.steps:
                            if remaining.step_order > step.step_order:
                                db.add(models.StepResult(
                                    run_id=run.id, step_id=remaining.id,
                                    step_order=remaining.step_order, action=remaining.action,
                                    status=models.StepStatus.SKIPPED,
                                ))
                        db.commit()
                        break
                    except Exception as e:
                        result.status = models.StepStatus.FAILED
                        result.actual_result = str(e)
                        result.screenshot_path = _save_screenshot(page, run.id, step.step_order)
                        overall_status = models.RunStatus.FAILED
                        result.duration_ms = int((time.time() - t0) * 1000)
                        db.commit()
                        for remaining in test_case.steps:
                            if remaining.step_order > step.step_order:
                                db.add(models.StepResult(
                                    run_id=run.id, step_id=remaining.id,
                                    step_order=remaining.step_order, action=remaining.action,
                                    status=models.StepStatus.SKIPPED,
                                ))
                        db.commit()
                        break

                    result.duration_ms = int((time.time() - t0) * 1000)
                    db.commit()

                context.close()
                browser.close()

        except RuntimeError as e:
            # 本机环境问题
            overall_status = models.RunStatus.ERROR
            run.error_message = str(e)
        except Exception as e:
            overall_status = models.RunStatus.ERROR
            run.error_message = str(e)

        run.status = overall_status
        run.end_time = datetime.datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _save_screenshot(page, run_id: int, step_order: int) -> Optional[str]:
    """保存当前页面截图到本地，返回相对路径"""
    try:
        filename = f"run{run_id}_step{step_order}_{int(time.time())}.png"
        path = os.path.join(SCREENSHOT_DIR, filename)
        page.screenshot(path=path, full_page=True)
        return f"screenshots/{filename}"
    except Exception:
        return None
