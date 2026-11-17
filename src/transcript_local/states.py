"""Every lifecycle in this app, in one place. Read this before touching a `status`.

Job and update statuses are defined and enforced here. The smaller ones stay
where they live:
  model download   idle -> downloading -> complete | failed      (models.py)
  update check     not_checked | up_to_date | update_available |
                   repair_available | check_failed | unknown     (update_check.py)
  worker progress  preparing -> loading -> transcribing -> complete (worker.py)
  AI proofreading  idle -> running -> complete | failed           (assistant.py)

supervisor.py is copied out of the package and must stay stdlib-only, so it
repeats the update strings; tests/test_states.py fails if they drift.
"""

# job.json 'status': what each status may become next. Only Manager changes it.
JOB = {
    'queued':      {'running', 'cancelled', 'interrupted'},
    'running':     {'complete', 'failed', 'cancelling', 'cancelled', 'interrupted', 'queued'},
    'cancelling':  {'cancelled', 'interrupted'},
    'failed':      {'queued'},
    'cancelled':   {'queued'},
    'interrupted': {'queued'},
    'complete':    set(),
}
ACTIVE = {'queued', 'running', 'cancelling'}
RESUMABLE = {'failed', 'cancelled', 'interrupted'}
LABELS = {'queued': '排隊中', 'running': '處理中', 'cancelling': '停止中', 'cancelled': '已停止',
          'interrupted': '可續跑', 'failed': '處理失敗', 'complete': '可校稿'}

# runtime/update.json 'status', written by updater.py then supervisor.py.
UPDATE = {'idle', 'requested', 'preparing', 'validating', 'switching', 'complete', 'rolled_back', 'failed'}
UPDATING = {'requested', 'preparing', 'validating', 'switching'}


def move(job, status):
    """Change a job's status, refusing any step the table does not allow."""
    if status != job['status'] and status not in JOB.get(job['status'], ()):
        raise ValueError(f"任務狀態不能從 {job['status']} 變成 {status}")
    job['status'] = status


def actions(status):
    """Buttons a client may offer for a job; the web UI and agents read this."""
    offered = (('cancel', status in {'queued', 'running'}), ('resume', status in RESUMABLE),
               ('delete', status not in ACTIVE))
    return [name for name, allowed in offered if allowed]
