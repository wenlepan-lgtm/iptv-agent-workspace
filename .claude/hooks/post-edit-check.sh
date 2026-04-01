#!/bin/bash
# Post-Edit Hook: 编辑后自动检查
# 在文件编辑后自动运行

echo "🔍 Post-Edit Check..."

# 获取修改的文件
CHANGED_FILES=$(git diff --name-only HEAD 2>/dev/null | grep -E '\.(kt|java|xml)$' || true)

if [ -z "$CHANGED_FILES" ]; then
    echo "✅ No code files changed"
    exit 0
fi

# 检查是否有 TODO/FIXME
TODO_COUNT=$(echo "$CHANGED_FILES" | xargs grep -l "TODO\|FIXME" 2>/dev/null | wc -l)
if [ "$TODO_COUNT" -gt 0 ]; then
    echo "⚠️  Warning: Found TODO/FIXME in changed files"
fi

# 检查是否有调试代码
DEBUG_PATTERNS="println\|Log.d\|console.log\|print("
DEBUG_COUNT=$(echo "$CHANGED_FILES" | xargs grep -l "$DEBUG_PATTERNS" 2>/dev/null | wc -l)
if [ "$DEBUG_COUNT" -gt 0 ]; then
    echo "⚠️  Warning: Found debug statements in changed files"
fi

echo "✅ Post-edit check completed"
