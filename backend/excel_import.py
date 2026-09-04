"""
Excel 测试用例导入

模板列（第一行表头，中文）：
用例名称 | 所属模块 | 用例描述 | 步骤序号 | 操作 | 操作对象 | 输入值 | 预期结果

设计理念：
测试人员只描述业务测试步骤（做什么、对谁做、输入什么、期望什么），
不需要填写元素定位信息（CSS/XPath/ID 等）。后续由自动化执行引擎根据
"操作对象"自动进行元素识别和定位。

- "用例名称"/"所属模块"/"用例描述" 可以像 Excel 合并单元格一样只在每个用例的第一行填写，
  下面同一用例的步骤行留空即可（本模块会自动向下填充）。
- "操作" 支持中文业务描述（打开/输入/点击/校验/...），导入时自动映射为技术操作。
  也兼容直接填写技术操作名（open_url/click/input/...）。
"""
import pandas as pd
from sqlalchemy.orm import Session

from . import models

COLUMN_MAP = {
    "用例名称": "case_name",
    "所属模块": "module",
    "用例描述": "description",
    "步骤序号": "step_order",
    "操作": "action",
    "操作对象": "target",
    "输入值": "input_value",
    "预期结果": "expected_result",
}

REQUIRED_COLUMNS = ["用例名称", "步骤序号", "操作"]

# 中文业务操作 → 技术操作映射
ACTION_MAP = {
    "打开": "open_url",
    "输入": "input",
    "点击": "click",
    "校验": "assert_text",
    "选择": "select",
    "悬停": "hover",
    "等待": "sleep",
    "刷新": "refresh",
    "返回": "go_back",
    "截图": "screenshot",
    "滚动": "scroll_to",
    "按键": "press_key",
    "切换iframe": "switch_to_frame",
    "切回主文档": "switch_to_default",
    "清空": "clear",
    "校验标题": "assert_title",
    "校验元素存在": "assert_element_exists",
    "校验元素不存在": "assert_element_not_exists",
}

VALID_ACTIONS = {
    "open_url", "click", "input", "clear", "select", "hover", "wait_for_element",
    "assert_text", "assert_element_exists", "assert_element_not_exists",
    "assert_title", "screenshot", "scroll_to", "press_key", "refresh",
    "go_back", "sleep", "switch_to_frame", "switch_to_default",
}


def parse_excel(file_path: str) -> pd.DataFrame:
    """读取 Excel 并做基础校验/清洗，返回标准化后的 DataFrame"""
    df = pd.read_excel(file_path, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Excel 缺少必需列: {', '.join(missing)}")

    # 向下填充合并单元格效果的列（用例名称/模块/描述常常只填第一行）
    for col in ["用例名称", "所属模块", "用例描述"]:
        if col in df.columns:
            df[col] = df[col].ffill()

    df = df.rename(columns=COLUMN_MAP)
    df = df.dropna(subset=["case_name", "action"])
    df["step_order"] = pd.to_numeric(df["step_order"], errors="coerce").fillna(0).astype(int)
    return df


def import_test_cases(db: Session, file_path: str) -> dict:
    """解析 Excel 并写入数据库，返回导入统计信息"""
    df = parse_excel(file_path)

    warnings: list[str] = []
    imported_case_names: list[str] = []
    total_steps = 0

    for case_name, group in df.groupby("case_name", sort=False):
        group = group.sort_values("step_order")
        first = group.iloc[0]

        test_case = models.TestCase(
            name=str(case_name).strip(),
            module=(str(first.get("module")).strip() if pd.notna(first.get("module")) else None),
            description=(str(first.get("description")).strip() if pd.notna(first.get("description")) else None),
        )
        db.add(test_case)
        db.flush()  # 拿到 test_case.id

        for _, row in group.iterrows():
            action_raw = str(row["action"]).strip()
            # 中文业务操作映射为技术操作；已填技术操作名则原样使用
            action = ACTION_MAP.get(action_raw, action_raw)
            if action not in VALID_ACTIONS:
                warnings.append(
                    f"用例《{case_name}》第{row['step_order']}步：未知操作 '{action_raw}'，已跳过该步骤"
                )
                continue
            step = models.TestStep(
                test_case_id=test_case.id,
                step_order=int(row["step_order"]),
                action=action,
                target=_clean(row.get("target")),
                input_value=_clean(row.get("input_value")),
                expected_result=_clean(row.get("expected_result")),
                wait_timeout=10,
            )
            db.add(step)
            total_steps += 1

        imported_case_names.append(str(case_name).strip())

    db.commit()
    return {
        "imported_cases": len(imported_case_names),
        "imported_steps": total_steps,
        "case_names": imported_case_names,
        "warnings": warnings,
    }


def _clean(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    val = str(val).strip()
    return val if val else None


def generate_template(file_path: str) -> str:
    """生成一份 Excel 模板（仅表头），方便用户下载后照着填写"""
    columns = ["用例名称", "所属模块", "用例描述", "步骤序号", "操作", "操作对象", "输入值", "预期结果"]
    pd.DataFrame(columns=columns).to_excel(file_path, index=False)
    return file_path
