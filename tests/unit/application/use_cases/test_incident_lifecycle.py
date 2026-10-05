import pytest
import uuid
from datetime import datetime, timezone, timedelta
from deployd.domain.incident.incident import Incident
from deployd.application.use_cases.incident_lifecycle import (
    IncidentLifecycleUseCase,
    ListIncidentsUseCase,
    GetIncidentUseCase,
)
from deployd.infrastructure.persistence.sqlite_incident_repository import SQLiteIncidentRepository
from deployd.domain.entities.core_event import CoreEvent, CoreEventType, Severity

T0 = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)

def _ev(id_str: str) -> CoreEvent:
    return CoreEvent(
        event_id=uuid.UUID(int=int(id_str)),
        event_type=CoreEventType.STATE_CHANGE,
        severity=Severity.ERROR,
        timestamp=T0,
        related_component="test",
        description="test",
    )

@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "test.db"
    return SQLiteIncidentRepository(str(db_path))

def test_incident_lifecycle_ensure_open(repo):
    uc = IncidentLifecycleUseCase(repo)
    e = _ev("1")
    incident = uc.ensure_open(e)
    assert incident.opened_at == T0
    assert incident.status == "OPEN"
    
    # Second call should return the same incident
    incident_2 = uc.ensure_open(e)
    assert incident_2.id == incident.id

def test_incident_lifecycle_update_severity(repo):
    uc = IncidentLifecycleUseCase(repo)
    uc.ensure_open(_ev("1"))
    
    uc.update_severity(Severity.CRITICAL)
    
    incident = repo.get_current()
    assert incident.peak_severity == Severity.CRITICAL
    
    # updating without open incident
    incident.resolve(T0, {}, [], "summary")
    repo.save(incident) # Close it
    uc.update_severity(Severity.ERROR) # should not crash

def test_incident_lifecycle_close_current(repo):
    uc = IncidentLifecycleUseCase(repo)
    
    assert uc.close_current({}, []) is None
    
    uc.ensure_open(_ev("1"))
    closed = uc.close_current({"nodes": []}, [{"msg": "test"}], "summary")
    
    assert closed.status == "RESOLVED"
    assert closed.root_cause_summary == "summary"

def test_list_and_get_incidents(repo):
    uc_life = IncidentLifecycleUseCase(repo)
    uc_list = ListIncidentsUseCase(repo)
    uc_get = GetIncidentUseCase(repo)
    
    inc = uc_life.ensure_open(_ev("1"))
    
    all_incs = uc_list.execute()
    assert len(all_incs) == 1
    
    fetched = uc_get.execute(inc.id)
    assert fetched.id == inc.id
