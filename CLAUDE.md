## Session handoff (always do this)

I plan and review projects by voice in the Claude app (often via CarPlay), and
that Claude cannot see Claude Code sessions. The repo is the shared source of
truth between us. Keep it in sync every time.

### At the start of every session
1. Read ROADMAP.md and PROGRESS.md in the repo root (create them if missing).
   Everything lives on the main branch. Don't leave work on other branches;
   merge back to main and push when done.
   Check `git status` and commit any uncommitted ROADMAP.md or PROGRESS.md
   changes before starting.
2. Check this project's area file in my Claude memory (/areas/<project-slug>.md)
   for ideas, decisions or roadmap changes made in chat since the last
   PROGRESS.md entry. Copy anything new into ROADMAP.md before starting work.
3. If ROADMAP.md conflicts with what I ask for in the session, mention it.

### Whenever you push, and at the end of every session
Write a short handoff in two places:
   a. Repo: append it to PROGRESS.md (add a new line at the bottom; never
      delete, replace or rewrite earlier entries), update ROADMAP.md if items were finished
      or changed, and include both in the commit.
   b. Memory: add the same dated line to the project's area file
      (create it if missing).

Each handoff covers, in plain sentences that work read aloud:
   - what got done
   - what is broken or blocked, and why
   - decisions made
   - the next step
Keep it to a few lines. No code, no file dumps.

### New projects
When starting a new repo, create CLAUDE.md with this same "Session handoff"
section copied in, plus ROADMAP.md and PROGRESS.md, and create the matching
memory area file.

If a session ended without a handoff, write it at the start of the next
session before doing anything else.
