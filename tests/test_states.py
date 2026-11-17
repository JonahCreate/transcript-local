import re
from pathlib import Path
import pytest
from transcript_local import states


def test_job_table_is_closed_and_sets_agree():
    assert all(targets <= set(states.JOB) for targets in states.JOB.values())
    assert set(states.LABELS) == set(states.JOB)
    assert states.RESUMABLE == {s for s, nxt in states.JOB.items() if nxt == {'queued'}}
    assert states.ACTIVE | states.RESUMABLE | {'complete'} == set(states.JOB)
    assert states.UPDATING < states.UPDATE


def test_move_refuses_steps_outside_the_table():
    job = {'status': 'complete'}
    with pytest.raises(ValueError): states.move(job, 'queued')
    assert job['status'] == 'complete'
    job = {'status': 'running'}
    states.move(job, 'cancelling'); states.move(job, 'cancelled'); states.move(job, 'queued')
    assert job['status'] == 'queued'


def test_actions_match_what_the_manager_accepts():
    assert states.actions('queued') == ['cancel']
    assert states.actions('cancelling') == []
    assert states.actions('failed') == ['resume', 'delete']
    assert states.actions('complete') == ['delete']


def test_supervisor_and_clients_use_only_known_statuses():
    source = Path(states.__file__).parent
    written = set(re.findall(r"self\.state\('(\w+)'", (source / 'supervisor.py').read_text()))
    assert written and written <= states.UPDATE
    # ponytail: app.js still lists UPDATING itself; serve it from /api/update if it ever changes.
    listed = re.findall(r"\['requested','preparing','validating','switching'\]", (source / 'static/app.js').read_text())
    assert listed and set(eval(listed[0])) == states.UPDATING
