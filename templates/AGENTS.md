# Global OpenCode instructions

- Never expose secrets, tokens, passwords, API keys, or credential files.
- Do not scan `.git`, `node_modules`, build output, caches, or logs without a reason.
- When work uses `ssh_relay`, load the `ssh-relay` skill first.
- Before builds, CMake, CTest, integration/load tests, long scripts, or other long-running operations, load `remote-long-running`.
- Before risky state-changing actions or work in an unfamiliar subsystem, load the relevant agent-safe skill: `risk-gate`, `safe-cli`, `unknown-system-safety`, or `recovery-mode`.
- Do not preload specialized skills unless the current task needs them.
