#!/bin/bash
# Pre-Commit Check: 提交前自动检查
# 在 git commit 前运行

echo "🔍 Pre-Commit Check..."
echo ""

# 获取变更的文件
STAGED_FILES=$(git diff --cached --name-only 2>/dev/null || true)

if [ -z "$STAGED_FILES" ]; then
    echo "⚠️  No staged files to check"
    exit 0
fi

# 统计
KT_FILES=$(echo "$STAGED_FILES" | grep -c '\.kt$' || echo "0")
JAVA_FILES=$(echo "$STAGED_FILES" | grep -c '\.java$' || echo "0")
XML_FILES=$(echo "$STAGED_FILES" | grep -c '\.xml$' || echo "0")
TOTAL_LINES=$(git diff --cached --stat 2>/dev/null | tail -1 || echo "")

echo "📊 变更统计:"
echo "   - Kotlin 文件: $KT_FILES"
echo "   - Java 文件: $JAVA_FILES"
echo "   - XML 文件: $XML_FILES"
echo "   - $TOTAL_LINES"
echo ""

# 检查大文件
LARGE_FILES=$(git diff --cached --name-only 2>/dev/null | xargs -I {} sh -c 'if [ -f "{}" ] && [ $(wc -c < "{}") -gt 100000 ]; then echo "{}"; fi' || true)
if [ -n "$LARGE_FILES" ]; then
    echo "⚠️  Warning: Large files detected:"
    echo "$LARGE_FILES"
    echo ""
fi

# 检查敏感文件
SENSITIVE_FILES=$(echo "$STAGED_FILES" | grep -E '(local\.properties|keystore|\.jks|\.p12|credentials|secrets|\.env)' || true)
if [ -n "$SENSITIVE_FILES" ]; then
    echo "🚨 WARNING: Sensitive files detected!"
    echo "$SENSITIVE_FILES"
    echo ""
    echo "Are you sure you want to commit these files?"
    echo "Press Ctrl+C to abort, or Enter to continue..."
    read -r
fi

# 检查 TODO/FIXME
STAGED_CODE_FILES=$(echo "$STAGED_FILES" | grep -E '\.(kt|java)$' || true)
if [ -n "$STAGED_CODE_FILES" ]; then
    TODO_FILES=$(echo "$STAGED_CODE_FILES" | xargs grep -l "TODO\|FIXME" 2>/dev/null || true)
    if [ -n "$TODO_FILES" ]; then
        echo "📝 Files with TODO/FIXME:"
        echo "$TODO_FILES"
        echo ""
    fi
fi

echo "✅ Pre-commit check completed"
echo ""
echo "Remember to:"
echo "  1. Run tests if applicable"
echo "  2. Update documentation if needed"
echo "  3. Check for regressions"
