---
name: ledger-use
description: Maintain a lightweight execution ledger for Android GUI tasks. Use for every task to decompose work, mark subtasks complete, record optional reflections, judge semantic completion, and finish in order.
---

# Ledger Use

Use the ledger only for task decomposition and execution history. Keep it short.
Do not turn it into a second task, a proof system, or a strict schema exercise.
The ledger is a guide for doing the work, not a substitute for inspecting the
app. Every validation claim must be grounded in the final visible UI or saved
filesystem state.

The managed tools are `update_ledger`, `reflect_on_ledger`, `answer`,
`validate_ledger`, and `finish`. Never read or edit the ledger file with Bash.

## Workflow

1. At task start, call `update_ledger` once to create a small set of concrete
   execution subtasks and one or more `subtask_for_validate` records. Validation
   subtasks are final acceptance tests, not generic "reverify" reminders. Write
   each as task-specific, observable checks: identify the exact objects/files,
   required content or state, ordering/relationships, and persistence (save or
   submit) requirement. Place validation records after ordinary subtasks and
   complete each only after the ordinary work it checks. The tool records the
   original task automatically.
2. Perform the GUI work. When an ordinary execution subtask is actually done,
   mark it with `complete_subtask`. Do not mark validation subtasks complete early;
   first reread the final UI/filesystem state and perform all of its named
   checks, then mark it complete. Batch execution-subtask completions when
   convenient, but keep validation completion last.
3. Use `reflect_on_ledger` proactively after obvious errors, loops, repeated
   ineffective actions, uncertainty, plan changes, missing validation tasks, or
   repeated validation failures. `next_step` must be a recommended executable
   subtask (what to inspect/change and the expected result), so it can be
   copied into a new `subtask` record; do not write vague narration.
4. For a question task, call `answer` after deriving the visible answer.
5. After the validation checks pass, append one `complete` record summarizing
   the requested outcome. Do not use `complete` as a replacement for the
   validation subtask or `validate_ledger`.
6. Perform each validation subtask after the ordinary work it covers. Reread
   the target UI/filesystem state and check each concrete assertion in its
   description (including exact names,
   text/content, order, relationships, and that the result survived save or
   submit). Only then mark it complete. Call `validate_ledger` to semantically
   review the original request, tracked subtasks, execution history, and
   current visible UI. Its summary must state the observed result and any
   mismatch, not merely say that work was rechecked. A complete validation is
   required before `finish`.
7. If validation says incomplete, add ordinary `subtask` records for the
   missing corrections, complete them, then add and execute fresh concrete
   `subtask_for_validate` records before validating again.
   If it says complete, call `finish` next.

## Records

`update_ledger` accepts only these records:

```bash
subtask "id" "description"
subtask_for_validate "verify-result" "In the final state, verify [exact target object/file names], [required content/state], [ordering and relationships], and that the result is saved/submitted and persists after reopening"
complete_subtask "id"
complete "task completion summary"
```

The other tools record their own lifecycle entries:

```bash
task "original task"
reflection "subtask-or-task" "reason" "next step"
answer "explicit answer"
validation "complete|incomplete" "semantic review"
finish "passed" "summary"
```

## Example

```text
update_ledger({records:[
  {kind:"subtask",id:"inspect",detail:"Read all three source notes"},
  {kind:"subtask",id:"merge",detail:"Create mIObBbo4 and merge sources in order with separators"},
  {kind:"subtask",id:"save",detail:"Save, leave, and reopen the target"},
  {kind:"subtask_for_validate",id:"check-content",detail:"Verify mIObBbo4 contains each source exactly once in tough_frog, proud_cat, koala order with two separator lines"},
  {kind:"subtask_for_validate",id:"check-persistence",detail:"Verify the same exact name and content persist after leaving and reopening"}
]})

update_ledger({records:[
  {kind:"complete_subtask",id:"inspect"}, {kind:"complete_subtask",id:"merge"}, {kind:"complete_subtask",id:"save"}
]})
validate_ledger({task_completed:false,summary:"Content has proud_cat twice and persistence is not confirmed",incomplete_subtasks:["merge","save"]})
reflect_on_ledger({current_subtask_id:"check-content",reason:"The first validation exposed duplicate content and an unverified save",next_step:"Recommended subtask fix-merge-save: remove the duplicate proud_cat block, restore exactly two separators, explicitly save, leave, and reopen mIObBbo4"})
update_ledger({records:[
  {kind:"subtask",id:"fix-merge-save",detail:"Remove duplicate content, restore separators, save, and reopen"},
  {kind:"subtask_for_validate",id:"check-corrected-content",detail:"After reopening, verify each source occurs once in the requested order with exactly two separators"}
]})
update_ledger({records:[{kind:"complete_subtask",id:"fix-merge-save"},{kind:"complete_subtask",id:"check-corrected-content"}]})
validate_ledger({task_completed:false,summary:"Content is now correct and persistent, but the title is mIObBbo4.md instead of exact mIObBbo4",incomplete_subtasks:["exact-name-correction"]})
reflect_on_ledger({current_subtask_id:"check-corrected-content",reason:"A second validation found an exact-name failure",next_step:"Recommended subtask fix-name: rename the note exactly to mIObBbo4, save, reopen, and verify title plus content"})
update_ledger({records:[
  {kind:"subtask",id:"fix-name",detail:"Rename to exact mIObBbo4 and save"},
  {kind:"subtask_for_validate",id:"check-final",detail:"Reopen and verify exact name, ordered unique source contents, two separators, and persistence"}
]})
update_ledger({records:[{kind:"complete_subtask",id:"fix-name"},{kind:"complete_subtask",id:"check-content"},{kind:"complete_subtask",id:"check-persistence"},{kind:"complete_subtask",id:"check-final"},{kind:"complete",detail:"Merged notes are saved under the exact name in the requested order"}]})
validate_ledger({task_completed:true,summary:"Final reopen confirms exact name mIObBbo4, ordered unique contents, separators, and persistence",incomplete_subtasks:[]})
finish({summary:"Merged and saved the requested Markor note"})
```
