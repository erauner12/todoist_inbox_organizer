import os
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
from typing import Dict, Any
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
import requests
import json
import time
from todoist_api_python.api import TodoistAPI

# Load environment variables
load_dotenv()

# Access environment variables
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
TODOIST_API_KEY = os.getenv("TODOIST_API_KEY")

# Logging setup
logging_format = "%(asctime)s %(levelname)s:%(name)s %(filename)s:%(lineno)d %(funcName)s - %(message)s"
logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO, format=logging_format)

# FastAPI app setup
app = FastAPI(debug=DEBUG)

# Todoist API setup
todoist_api = TodoistAPI(TODOIST_API_KEY)

# Todoist API constants
TODOIST_SYNC_URL = "https://api.todoist.com/sync/v9/sync"

# Mappings and configurations
INBOX_PROJECT_ID = "2236493795"
SECTION_TO_LABEL_MAPPING = {
    "Work": "context/work",
    "Home": "context/home",
    "Side": "context/side",
    "Move to Immediate": "move/immediate",
    "Move to Parallel": "move/parallel",
    "Move to project Inbox": "move/inbox",
}
LABEL_TO_PROJECT_MAPPING = {
    "context/work": "2327425429",
    "context/home": "2244866374",
    "context/side": "2327425662",
}
DUE_TIME_SECTIONS = {
    "Due 9am": {"due_string": "at 9am", "due_lang": "en"},
    "Due 12pm": {"due_string": "at 12pm", "due_lang": "en"},
    "Due 5pm": {"due_string": "at 5pm", "due_lang": "en"},
}

# Global variables
sync_token = "*"
last_sync_time = None

class TodoistWebhook(BaseModel):
    event_name: str
    user_id: str
    event_data: Dict[str, Any]

async def get_section_name(section_id: str) -> str:
    try:
        section = todoist_api.get_section(section_id)
        return section.name
    except Exception as e:
        logging.error(f"Error getting section name: {str(e)}")
        return None

async def add_label_to_task(task_id: str, label: str) -> bool:
    try:
        task = todoist_api.get_task(task_id)
        if label not in task.labels:
            labels = task.labels + [label]
            updated_task = todoist_api.update_task(task_id=task_id, labels=labels)
            if updated_task:
                logging.info(f"Added label {label} to task {task_id}")
                return True
        return False
    except Exception as e:
        logging.error(f"Error adding label to task: {str(e)}")
        return False

async def set_due_date(task_id: str, due_string: str, due_lang: str = "en", add_duration: bool = False) -> bool:
    try:
        data = {
            "due_string": due_string,
            "due_lang": due_lang,
        }
        if add_duration:
            data["duration"] = {"amount": 60, "unit": "minute"}
        
        updated_task = todoist_api.update_task(task_id=task_id, **data)
        if updated_task:
            logging.info(f"Set due date to '{due_string}' for task {task_id}")
            return True
        return False
    except Exception as e:
        logging.error(f"Error setting due date: {str(e)}")
        return False

async def remove_due_date(task_id: str) -> bool:
    try:
        updated_task = todoist_api.update_task(task_id=task_id, due_string=None)
        if updated_task:
            logging.info(f"Removed due date from task {task_id}")
            return True
        return False
    except Exception as e:
        logging.error(f"Error removing due date: {str(e)}")
        return False

async def move_task_to_project(task_id: str, project_id: str) -> bool:
    try:
        updated_task = todoist_api.update_task(task_id=task_id, project_id=project_id)
        if updated_task:
            logging.info(f"Moved task {task_id} to project {project_id}")
            return True
        return False
    except Exception as e:
        logging.error(f"Error moving task to project: {str(e)}")
        return False

async def process_task(task: Dict[str, Any]):
    task_id = task["id"]
    content = task["content"]
    project_id = task["project_id"]
    section_id = task.get("section_id")

    if section_id:
        section_name = await get_section_name(section_id)
        
        if section_name in SECTION_TO_LABEL_MAPPING:
            label = SECTION_TO_LABEL_MAPPING[section_name]
            if label.startswith("move/"):
                if label == "move/immediate":
                    await move_task_to_project(task_id, project_id)
                    # Add logic for moving to Immediate section
                elif label == "move/parallel":
                    await move_task_to_project(task_id, project_id)
                    # Add logic for moving to Parallel section
                elif label == "move/inbox":
                    await move_task_to_project(task_id, INBOX_PROJECT_ID)
            else:
                await add_label_to_task(task_id, label)
        
        elif section_name == "Due Today":
            await set_due_date(task_id, "today")
        
        elif section_name in DUE_TIME_SECTIONS:
            due_info = DUE_TIME_SECTIONS[section_name]
            await set_due_date(task_id, due_info["due_string"], due_info["due_lang"], add_duration=True)
        
        elif section_name and section_name.startswith("Inbox *"):
            await remove_due_date(task_id)

def process_changes(changes: Dict[str, Any]):
    for item in changes.get("items", []):
        process_task(item)

@app.post("/todoist/")
async def todoist_webhook(webhook: TodoistWebhook, background_tasks: BackgroundTasks):
    background_tasks.add_task(sync_and_process)
    return {"status": "Processing started"}

async def sync_and_process():
    global sync_token, last_sync_time
    
    current_time = time.time()
    if last_sync_time and current_time - last_sync_time < 5:
        logging.info("Skipping sync due to rate limiting")
        return

    headers = {
        "Authorization": f"Bearer {TODOIST_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "sync_token": sync_token,
        "resource_types": '["items"]'
    }
    
    try:
        response = requests.post(TODOIST_SYNC_URL, headers=headers, json=data)
        response.raise_for_status()
        sync_data = response.json()
        process_changes(sync_data)
        sync_token = sync_data["sync_token"]
        last_sync_time = current_time
    except requests.exceptions.RequestException as e:
        logging.error(f"Sync failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
