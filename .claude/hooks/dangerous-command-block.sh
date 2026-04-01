#!/bin/bash
# Dangerous Command Blocker: 阻断危险命令
# 在执行命令前检查

COMMAND="$1"

# 危险命令模式列表
DANGEROUS_PATTERNS=(
    "rm -rf"
    "rm -r"
    "git push --force"
    "git push -f"
    "git reset --hard"
    "git checkout --"
    "DROP TABLE"
    "DROP DATABASE"
    "DELETE FROM"
    "truncate"
    ":(){ :|:& };:"
    "mkfs"
    "dd if="
    "> /dev/sd"
    "> /dev/hd"
)

# 检查是否匹配危险模式
for pattern in "${DANGEROUS_PATTERNS[@]}"; do
    if [[ "$COMMAND" == *"$pattern"* ]]; then
        echo "🚫 BLOCKED: Dangerous command detected"
        echo "Pattern: $pattern"
        echo "Command: $COMMAND"
        echo ""
        echo "If you really need to run this command, please confirm with the user first."
        exit 1
    fi
done

# 允许通过
exit 0
