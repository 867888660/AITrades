#!/usr/bin/env python3
"""
策略工作台性能验证脚本
快速检查性能优化模块是否正确集成
"""

import os
import sys
from pathlib import Path

def check_file_exists(filepath, description):
    """检查文件是否存在"""
    if Path(filepath).exists():
        print(f"✅ {description}: {filepath}")
        return True
    else:
        print(f"❌ {description} 缺失: {filepath}")
        return False

def check_file_content(filepath, search_strings):
    """检查文件内容是否包含特定字符串"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        missing = []
        for search_str in search_strings:
            if search_str not in content:
                missing.append(search_str)

        if not missing:
            print(f"✅ {filepath} 内容验证通过")
            return True
        else:
            print(f"❌ {filepath} 缺少以下内容:")
            for item in missing:
                print(f"   - {item}")
            return False
    except Exception as e:
        print(f"❌ 读取 {filepath} 失败: {e}")
        return False

def main():
    """主验证流程"""
    print("=" * 60)
    print("策略工作台性能优化验证")
    print("=" * 60)
    print()

    base_dir = Path(__file__).parent.parent
    static_dir = base_dir / "static"
    templates_dir = base_dir / "templates"
    docs_dir = base_dir / "文档"

    all_passed = True

    # 1. 检查新增文件
    print("📁 检查新增文件...")
    print()

    files_to_check = [
        (static_dir / "workspace_performance_optimization.js", "性能优化模块"),
        (static_dir / "workspace_professional_ui.css", "专业UI样式"),
        (static_dir / "workspace_optimizer_integration.js", "集成脚本"),
        (docs_dir / "策略工作台性能优化说明.md", "优化文档"),
    ]

    for filepath, desc in files_to_check:
        if not check_file_exists(filepath, desc):
            all_passed = False

    print()

    # 2. 检查HTML模板集成
    print("📄 检查HTML模板集成...")
    print()

    html_template = templates_dir / "strategy_workspace.html"
    html_checks = [
        "workspace_professional_ui.css",
        "workspace_performance_optimization.js",
        "workspace_optimizer_integration.js",
        "ws-performance-badge",
    ]

    if not check_file_content(html_template, html_checks):
        all_passed = False

    print()

    # 3. 检查性能优化模块关键函数
    print("🔧 检查性能优化模块...")
    print()

    perf_module = static_dir / "workspace_performance_optimization.js"
    perf_checks = [
        "SmartRefreshManager",
        "ChartIncrementalUpdater",
        "DOMUpdateOptimizer",
        "fillChartRowsForwardOptimized",
        "VirtualEventList",
        "PerformanceMonitor",
    ]

    if not check_file_content(perf_module, perf_checks):
        all_passed = False

    print()

    # 4. 检查UI样式关键类
    print("🎨 检查UI样式...")
    print()

    ui_css = static_dir / "workspace_professional_ui.css"
    ui_checks = [
        "--ws-spacing",
        "--ws-bg-primary",
        "--ws-text-primary",
        ".ws3-chart-area",
        ".ws3-events-section",
        ".state-chip",
        ".ws-performance-badge",
    ]

    if not check_file_content(ui_css, ui_checks):
        all_passed = False

    print()

    # 5. 检查集成脚本
    print("🔗 检查集成脚本...")
    print()

    integration = static_dir / "workspace_optimizer_integration.js"
    integration_checks = [
        "window.setAutoRefresh",
        "window.loadChart",
        "refreshManager.start",
        "perfMonitor.recordMetric",
        "updatePerformanceBadge",
    ]

    if not check_file_content(integration, integration_checks):
        all_passed = False

    print()
    print("=" * 60)

    if all_passed:
        print("✅ 所有验证通过！优化已正确集成。")
        print()
        print("📋 下一步操作:")
        print("1. 启动Flask应用")
        print("2. 访问策略工作台页面")
        print("3. 按 Ctrl/Cmd + Shift + P 查看性能面板")
        print("4. 观察网络请求频率和页面流畅度")
        print()
        print("📚 详细文档: 文档/策略工作台性能优化说明.md")
        return 0
    else:
        print("❌ 部分验证失败，请检查上述错误。")
        return 1

if __name__ == "__main__":
    sys.exit(main())
