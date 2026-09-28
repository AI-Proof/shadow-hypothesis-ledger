# SHL on Hermes Agent

## Install

```
hermes plugins install https://github.com/AI-Proof/shadow-hypothesis-ledger#shl_ledger
hermes plugins enable shl-ledger
```

Then start a new session.

- The `#shl_ledger` part matters. The plugin lives in the `shl_ledger/` folder of the repository. Without it, Hermes clones the whole repository into `plugins/shadow-hypothesis-ledger`, prints only a warning, and nothing loads. If that happened: `hermes plugins remove shadow-hypothesis-ledger`, then install again with `#shl_ledger`.
- Plugins are per profile. For another profile: `hermes -p <profile> plugins install ...` and `hermes -p <profile> plugins enable shl-ledger`.
- Hermes shows a "custom (unreviewed) source" warning for any plugin installed from a git address. That's expected.
- Check it: `hermes plugins validate <your profile>/plugins/shl-ledger` should pass, and `hermes shl` should print the ledger status.
- Update later: `hermes plugins update shl-ledger`. The database upgrades itself on first use and keeps a backup (see below).
- Requires Hermes 0.21 or newer (built against 0.21.5).

Keep `memory.write_approval: true` in the profile so assumptions don't silently become memory ([example](../examples/config.example.yaml)).

## What the plugin registers

| Piece | What it does |
|---|---|
| `pre_llm_call` hook | Checks the person's message against every parked assumption's evidence rule (plain code, no model call), then adds the SHL GATE block to the message: ACTIVE assumptions as text, parked ones by id. Uses only `user_message`; `conversation_history` is ignored on purpose. Skips scoring on `cron` and `subagent` turns. |
| `transform_llm_output` hook | Frames the reply with SHL's parts: the session note at the start of each session's first reply, and short confirmations at the end of a turn (an assumption was parked, or the assistant tried to act without you). Never on cron or subagent turns. Never contains assumption text. |
| `pre_tool_call` hook | Screens memory writes (`memory` tool: `content` and `new_text`; `old_text` is left alone because Hermes uses it to find the entry). Returns `modify` only when something was removed. Returns at once for every other tool. |
| System prompt section `shl-rules` | The fixed rules, once per session. Keeps the per-turn block short and the prompt cache warm. |
| Tool `shl` | For the agent: `compile`, `stage`, `reject` (with `evidence_for` / `evidence_against`), `reconsent`, `forget`, `review`, `notices`, `history`, `screen`. Never returns parked text, rules or consent codes. |
| `/shl` | For the person. Output goes to the person only and never enters the model's history. `/shl help` lists the everyday commands, `/shl help all` the rest. |
| `hermes shl ...` | The same subcommands from the terminal, plus `hermes shl screen FILE`. |
| Skill `shl-ledger:shl-context` | Reference text. Hermes doesn't list plugin skills to the model, so nothing depends on it. |

## What you'll see

Everything SHL shows is framed by two banner lines, with an empty line inside each, so it never runs into the assistant's own text:

```
░▒▓█ ᯽ SHL ᯽ █▓▒░

...

░▒▓███• ❉ •███▓▒░
```

**When you reject an assumption**, the reply ends with a confirmation: `Parked 1 assumption you rejected (#55). It won't be used. Changed your mind? "/shl use 55"`.

**At the start of every new session**, the first reply begins with the SHL note. It gives counts only, never the text of an assumption. Depending on what happened, it says one or more of:

| Situation | The note says |
|---|---|
| Your messages supported a parked assumption in 3 separate sessions and it recovered | "Since your last session: 1 assumption you once rejected has crossed the confidence threshold (0.65), and is waiting for your reconsideration." |
| 90 days passed since you rejected one | "... is due for its 90-day check ..." |
| Something is still waiting from before | "Still waiting from before: 2 assumptions (first mentioned 3 days ago)." |
| Contrary evidence pushed one below 0.30 | "1 assumption quietly decayed and was purged." |
| Nothing needs you | "54 assumptions you rejected are parked in your Shadow-Hypothesis Ledger. No actions necessary." |
| Nothing rejected yet | "SHL is active. You haven't rejected any assumptions yet." |
| Trial mode | "Trial mode: SHL is watching but changing nothing. Since your last session it would have brought back 1 assumption and purged 2." |
| Evidence switched off | "... Your messages aren't being checked against them (evidence is off) ..." |
| Just upgraded | "Your ledger was upgraded from v0.2 to v0.3. A backup was saved in ledger/backups/." plus how to finish (see below) |
| The change log was edited outside SHL | a warning and "/shl verify" |
| SHL couldn't open its database | "SHL couldn't read its ledger this session ..." |

When something waits, the note ends with: *Type "/shl" to see them and decide. Until you do, nothing changes and the assistant won't use them.*

**`/shl`** lists what's waiting, with the text (only you see this):

```
WAITING FOR YOU
 55. "Is vegetarian."
    0.37 → 0.82 · 3 supporting mentions

/shl use 55     use it again
/shl later 55   keep it parked, ask me again later
/shl drop 55    erase it for good
/shl 55         show why (the evidence)
/shl all        everything parked (54)
```

- **`/shl 55`** shows the evidence (your own sentences, with dates), how the confidence moved, and a line you can also give the assistant instead of a command: `use 55 K7Q2` (or `later` / `drop`). The code is shown only there, so only you can have typed it. It works in the same session, within 30 minutes, for 7 days after you last looked. Without it the assistant can't decide anything about a rejected assumption.
- **Numbers** (#55) are stable: they don't change when other assumptions come or go, and they're never reused.
- **Use** restores it. **Later** keeps it parked and asks again in 90 days at the earliest. **Drop** erases it.

## Settings

Set them in the Hermes plugin settings (Desktop/TUI), or in the profile's `config.yaml`:

```yaml
plugins:
  entries:
    shl-ledger:
      settings:
        evidence_mode: "on"      # on | log | off (quote it: bare on/off are booleans in YAML)
        session_note: true
        confirmations: true
        ghost_mode: ask          # ask | inert
        timed_review_days: 90    # 0 = no timed reviews
```

An environment variable `SHL_<NAME>` (for example `SHL_EVIDENCE_MODE=log`) overrides the config. Advanced settings (`rejection_weight`, `likelihood_ratio`, `decay`, `expire_at`, `review_evidence`, `notice_threshold`, `shadow_threshold`, `neutral_ids`, `purge_mode`, `rule_source`, `memory_tools`, `screen_memory`, `gate_note`) are listed in `plugin.yaml` with descriptions. `/shl settings` shows the values in effect. The threshold in the session note is always the live `notice_threshold`.

To let the plugin write evidence rules with one model call per rejection (instead of relying on the phrases the agent passes), set `rule_source: "agent,llm,auto"`. That call goes through `ctx.llm`, on your active model.

## Trying it safely

1. Install on a test profile first, or set `evidence_mode: log` on your main profile. In log mode SHL records what it would do and changes nothing.
2. The session note tells you what it would have done. After a week, `hermes shl report` shows the evidence it found, and which assumptions would have been brought back or erased. `hermes shl report full` adds the texts (keep that output private).
3. `/shl check I'm vegetarian these days` is a dry run: which parked assumption would count this sentence, and which way.
4. Switch to `evidence_mode: "on"` when the report looks right. Tune a rule with `/shl rule 55 for ..., ... against ..., ...`.

## Upgrading from v0.2

The database upgrades itself on first open and first copies itself to `<profile>/ledger/backups/`.
- Parked v0.2 assumptions get numbers (#1, #2, ...) and keep their confidence (the v0.3 rejection weight is not applied retroactively).
- They get a timed review date 90 days after their last change. If you have many and don't want them all waiting at once, set `timed_review_days: 0`.
- They have no evidence rule yet, so they aren't scored until they get one: `/shl rule ALL auto` builds one for each from the assumption's own words (`/shl rule 55 ...` to write your own). The session note reminds you until this is done.
- They keep their old ids. `/shl rename ALL` gives them neutral ids, so the prompt no longer shows names like `HYP_NIGHT` (assumptions waiting for your decision keep theirs until you answer).
- v0.2 already stored your rejection reasons on the assumption; the copies it also kept in the change log are dropped, and the log is rebuilt as a hash chain.
- The backup in `ledger/backups/` keeps the old database as it was, texts included. Delete it once you're happy.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `/shl` unknown | Plugin not enabled on this profile, or session started before enabling. |
| Installed but `hermes plugins list` shows `shadow-hypothesis-ledger` | Installed without `#shl_ledger`. Remove it and reinstall. |
| Nothing ever waits for you | `evidence_mode` is `log` or `off`, rules are missing (migrated assumptions: `/shl rule ALL auto`), or fewer than 3 sessions with supporting messages. `/shl 55` shows the counts. |
| No session note | `session_note` is off, or it's not a new session (one note per session). |
| A memory write came back with `[parked: ID]` | Working as intended: the sentence repeated a rejected assumption. |
| `hermes shl verify` says broken | The change log was edited outside SHL. The ledger still works; the log is just no longer trustworthy from that entry on. |
