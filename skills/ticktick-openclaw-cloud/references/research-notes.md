# Research Notes (GitHub + X)

## GitHub Samples Reviewed

- OpenClaw community skill repository: https://github.com/openclaw/skills
- TickTick minimal skill: https://github.com/openclaw/skills/tree/main/skills/kaiofreitas/ticktick-api
- TickTick CLI skill (manual/headless OAuth): https://github.com/openclaw/skills/tree/main/skills/manuelhettich/ticktick
- TickTick runtime + OpenClaw wrappers: https://github.com/openclaw/skills/tree/main/skills/one0x393/ticktick-calendar
- TickTick CLI variant: https://github.com/openclaw/skills/tree/main/skills/norickkevorkov/ticktick-cli

## X (Twitter) Scan

- OpenClaw skill discussions/search: https://x.com/search?q=OpenClaw%20skills
- TickTick API/community search: https://x.com/search?q=TickTick%20API%20OpenClaw

## Patterns Adopted

1. Use headless OAuth flow (`auth-url` + callback URL exchange) for cloud environments.
2. Keep commands JSON-first for reliable agent parsing.
3. Prefer explicit IDs (`projectId`, `taskId`) over fuzzy title matching.
4. Add token auto-refresh to reduce runtime interruptions.
5. Separate region hosts to support both Dida365 and TickTick accounts.
