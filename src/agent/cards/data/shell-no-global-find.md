---
id: shell-no-global-find
title: Shell 命令 + 写文件纪律
triggers: ["shell", "find", "命令", "查找", "卡死", "执行不完", "install", "安装", "依赖", "npm", "pip", "apt", "write_file", "截断", "truncated", "cd"]
applies_to_tools: ["kg_shell_exec"]
priority: 50
---

## 🚫 Shell / 文件写入禁忌

### 1. **禁止全盘 `find /` 或大范围扫描**
- `find / -name "xxx"`、`find /home -maxdepth 999` 会扫遍整个文件系统，**长时间阻塞 shell** 且消耗大量 token
- **替代**：`npm root -g` / `pip show xxx` / `python3 -c "import x; print(x.__file__)"` / `which` / 在已知子目录 `find <path> -maxdepth 4`
- 不明确 → **直接问用户**，不要瞎扫

### 2. **禁止单条 shell 命令塞太多步骤**
- `cmd1 && cmd2 && cmd3 && cmd4 && cmd5`：失败定位难、超时风险高、整条被取消时浪费 token
- **替代**：拆成 2-4 条**有边界的子命令**，每条独立可观察

### 3. **禁止用 `cd && cmd`（Cygwin/WSL 环境下）**
- Cygwin 下 `cd /tmp/xxx && pwd` 会报 `builtin: not found`
- **替代**：所有路径用**绝对路径**：`NODE_PATH=... node /abs/path/compile.js`

### 4. **禁止自行安装任何包/依赖（npm / pip / apt / brew 等）**
- 需要新包时**必须先问用户**并提供具体安装命令
- 理由：耗时长、要 sudo 密码、影响系统状态、不在 agent 控制范围
- 替代：给用户一句可复制命令，让用户跑完后回报结果

### 5. **`write_file` 写 ≥2 KB 文件可能被截断**
- 工具返回 `(argument truncated)` 且**文件未落盘**，后续读文件报 `MODULE_NOT_FOUND`
- **替代**：用 shell `cat > FILE <<'EOF' ... EOF`（heredoc）或 Python `with open(...) as f: f.write(...)`

### 6. **shell heredoc 整体超过 ~30 KB 可能被截断**
- 单条 heredoc 塞太多 slide/代码会被中间截断，导致部分文件没写出来
- **替代**：拆成**多条独立 shell 调用**，每条 1-2 个文件，宁可多次小步走

### 7. **并发工具调用会被用户新消息取消（不是 bug）**
- in-flight 工具显示 `cancelled - another message came in` 是正常的中断机制
- **应对**：不要假设工具一定跑完；在大动作前先和用户对齐方向（避免"我先建工作目录再并行"这种 commit 性的话被当成新消息触发取消）