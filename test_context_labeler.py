import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, call
from context_labeler import app, add_context_label, SECTION_TO_LABEL_MAPPING

client = TestClient(app)

@pytest.fixture
def mock_todoist_api():
    with patch("context_labeler.todoist_api", new_callable=AsyncMock) as mock_api:
        yield mock_api

@pytest.mark.asyncio
async def test_add_context_label_new_label(mock_todoist_api):
    task_id = "123"
    section_id = "151168595"  # work context
    current_labels = ["existing_label"]
    
    updated_labels = await add_context_label(task_id, section_id, current_labels)
    
    assert "context/work" in updated_labels
    assert "existing_label" in updated_labels
    mock_todoist_api.update_task.assert_called_once_with(task_id=task_id, labels=updated_labels)

@pytest.mark.asyncio
async def test_add_context_label_existing_label(mock_todoist_api):
    task_id = "123"
    section_id = "151168595"  # work context
    current_labels = ["context/work", "existing_label"]
    
    updated_labels = await add_context_label(task_id, section_id, current_labels)
    
    assert updated_labels == current_labels
    mock_todoist_api.update_task.assert_not_called()

@pytest.mark.asyncio
async def test_add_context_label_unknown_section(mock_todoist_api):
    task_id = "123"
    section_id = "unknown_section"
    current_labels = ["existing_label"]
    
    updated_labels = await add_context_label(task_id, section_id, current_labels)
    
    assert updated_labels == current_labels
    mock_todoist_api.update_task.assert_not_called()

def test_todoist_webhook_item_updated():
    webhook_data = {
        "event_name": "item:updated",
        "user_id": "user1",
        "event_data": {
            "id": "task1",
            "labels": ["existing_label"],
            "section_id": "151168595",
        },
        "initiator": {
            "id": "user1",
        },
    }

    with patch("context_labeler.add_context_label", new_callable=AsyncMock) as mock_add_context_label:
        mock_add_context_label.return_value = ["existing_label", "context/work"]
        response = client.post("/todoist/", json=webhook_data)

    assert response.status_code == 200
    assert response.json() == {"task_id": "task1", "updated_labels": ["existing_label", "context/work"]}
    mock_add_context_label.assert_called_once_with("task1", "151168595", ["existing_label"])

def test_todoist_webhook_item_added():
    webhook_data = {
        "event_name": "item:added",
        "user_id": "user1",
        "event_data": {
            "id": "task2",
            "labels": [],
            "section_id": "151168593",
        },
        "initiator": {
            "id": "user1",
        },
    }

    with patch("context_labeler.add_context_label", new_callable=AsyncMock) as mock_add_context_label:
        mock_add_context_label.return_value = ["context/home"]
        response = client.post("/todoist/", json=webhook_data)

    assert response.status_code == 200
    assert response.json() == {"task_id": "task2", "updated_labels": ["context/home"]}
    mock_add_context_label.assert_called_once_with("task2", "151168593", [])

def test_todoist_webhook_no_action():
    webhook_data = {
        "event_name": "item:completed",
        "user_id": "user1",
        "event_data": {
            "id": "task3",
            "labels": ["existing_label"],
            "section_id": None,
        },
        "initiator": {
            "id": "user1",
        },
    }

    response = client.post("/todoist/", json=webhook_data)

    assert response.status_code == 200
    assert response.json() == {"message": "No action taken"}

def test_todoist_webhook_task_moved_to_work_section():
    # Simulate task added outside of any section
    webhook_data_added = {
        "event_name": "item:added",
        "user_id": "28192378",
        "event_data": {
            "id": "8175729377",
            "labels": [],
            "section_id": None,
        },
        "initiator": {
            "id": "28192378",
        },
    }

    response = client.post("/todoist/", json=webhook_data_added)
    assert response.status_code == 200
    assert response.json() == {"message": "No action taken"}

    # Simulate task moved to work section
    webhook_data_moved = {
        "event_name": "item:updated",
        "user_id": "28192378",
        "event_data": {
            "id": "8175729377",
            "labels": [],
            "section_id": "151168595",
        },
        "initiator": {
            "id": "28192378",
        },
    }

    with patch("context_labeler.add_context_label", new_callable=AsyncMock) as mock_add_context_label:
        mock_add_context_label.return_value = ["context/work"]
        response = client.post("/todoist/", json=webhook_data_moved)

    assert response.status_code == 200
    assert response.json() == {"task_id": "8175729377", "updated_labels": ["context/work"]}
    mock_add_context_label.assert_called_once_with("8175729377", "151168595", [])

    # Simulate task updated with new label
    webhook_data_updated = {
        "event_name": "item:updated",
        "user_id": "28192378",
        "event_data": {
            "id": "8175729377",
            "labels": ["context/work"],
            "section_id": "151168595",
        },
        "initiator": {
            "id": "28192378",
        },
    }

    with patch("context_labeler.add_context_label", new_callable=AsyncMock) as mock_add_context_label:
        mock_add_context_label.return_value = ["context/work"]
        response = client.post("/todoist/", json=webhook_data_updated)

    assert response.status_code == 200
    assert response.json() == {"task_id": "8175729377", "updated_labels": ["context/work"]}
    mock_add_context_label.assert_called_once_with("8175729377", "151168595", ["context/work"])
