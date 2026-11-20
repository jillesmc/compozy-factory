# Sourced by tests/it/run.sh and tests/it/activation.sh (S8 machine-local routing).
# it_providers <compozy home>: config.toml provider sections. The four agent providers run tests/it/fake-acp and
#   report `authenticated` (auth_status_command); the two reviewer providers launch the github-app-review
#   reviewer-sandbox under <compozy home> directly, so bin/install counts them as sandboxed (never launched here).
# it_routing <xdg dir>: today's routing.toml under a throwaway XDG_CONFIG_HOME (never ~/.config).
it_providers() {
  local p
  for p in claude claude-mm1 claude-mm2 claude-mm4; do
    printf '[providers.%s]\ndisplay_name = "%s (IT stub)"\nharness = "acp"\ncommand = "%s/tests/it/fake-acp"\n' "$p" "$p" "$REPO"
    printf 'runtime_provider = "claude"\nauth_mode = "native_cli"\nauth_status_command = "echo authenticated"\n\n'
  done
  for p in claude-reviewer claude-reviewer-fallback; do
    printf '[providers.%s]\ndisplay_name = "%s (IT stub sandbox)"\nharness = "acp"\nruntime_provider = "claude"\n' "$p" "$p"
    printf 'auth_mode = "native_cli"\ncommand = "%s/extensions/github-app-review/bin/reviewer-sandbox --provider claude --login-dir %s/no-login --worktree-root %s/worktrees"\n\n' "$1" "$1" "$1"
  done
}
it_routing() {
  mkdir -p "$1/compozy-factory"
  cat > "$1/compozy-factory/routing.toml" <<'TOML'
[work]
primary = { provider = "claude-mm4", model = "sonnet" }
fallback = [{ provider = "claude-mm2", model = "sonnet" }, { provider = "claude-mm1", model = "sonnet" }]

[auditor]
primary = { provider = "claude", model = "sonnet" }
fallback = [{ provider = "claude-mm1", model = "sonnet" }]

[reviewer]
primary = { provider = "claude-reviewer", model = "opus" }
fallback = [{ provider = "claude-reviewer-fallback", model = "opus" }]
TOML
  chmod 600 "$1/compozy-factory/routing.toml"
}
