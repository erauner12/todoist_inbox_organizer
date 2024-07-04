import os
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
from typing import Dict, Any
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from todoist_api_python.api import TodoistAPI
import requests
import json
import time

load_dotenv()

DEBUG = os.getenv("DEBUG", "False").lower() == "true"
TODOIST_API_KEY = os.getenv("TODOIST_API_KEY")

logging_format = "%(asctime)s %(levelname)s:%(name)s %(filename)s:%(lineno)d %(funcName)s - %(message)s"
logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO, format=logging_format)

app = FastAPI(debug=DEBUG)

todoist_api = TodoistAPI(TODOIST_API_KEY)

TODOIST_SYNC_URL = "https://api.todoist.com/sync/v9/sync"

INBOX_PROJECT_ID = "2236493795"
SECTION_TO_LABEL_MAPPING = {
    "158952710": "context/work",
    "157482399": "context/home",
    "157481756": "context/side",
    "151168595": "move/immediate",
    "151168596": "move/parallel",
    "151168597": "move/inbox",
}
LABEL_TO_PROJECT_MAPPING = {
    "context/work": "2327425429",
    "context/home": "2244866374",
    "context/side": "2327425662",
}

sync_token = "*"
last_sync_time = None

class TodoistWebhook(BaseModel):
    event_name: str
    user_id: str
    event_data: Dict[str, Any]

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

    logging.info(f"Processing task: {task_id} - {content}")

    if section_id in SECTION_TO_LABEL_MAPPING:
        label = SECTION_TO_LABEL_MAPPING[section_id]
        if label.startswith("move/"):
            if label == "move/immediate":
                success = await move_task_to_project(task_id, project_id)
                logging.info(f"Moved task {task_id} to immediate section: {success}")
            elif label == "move/parallel":
                success = await move_task_to_project(task_id, project_id)
                logging.info(f"Moved task {task_id} to parallel section: {success}")
            elif label == "move/inbox":
                success = await move_task_to_project(task_id, INBOX_PROJECT_ID)
                logging.info(f"Moved task {task_id} to inbox: {success}")
        else:
            current_labels = task.get("labels", [])
            if label not in current_labels:
                success = await add_label_to_task(task_id, label)
                logging.info(f"Added label {label} to task {task_id}: {success}")
            else:
                logging.info(f"Task {task_id} already has label {label}. No action taken.")
    
    elif str(section_id).startswith("inbox_"):
        if task.get("due"):
            success = await remove_due_date(task_id)
            logging.info(f"Removed due date from task {task_id}: {success}")
        else:
            logging.info(f"Task {task_id} has no due date. No action taken.")
    
    else:
        logging.info(f"No action taken for task {task_id} in section {section_id}")

async def process_changes(changes: Dict[str, Any]):
    items = changes.get("items", [])
    
    sorted_items = sorted(items, key=lambda x: x.get('date_added', ''), reverse=True)
    
    processed_tasks = set()
    
    for item in sorted_items:
        task_id = item.get('id')
        if task_id in processed_tasks:
            continue
        
        processed_tasks.add(task_id)
        
        section_id = item.get('section_id')
        if section_id in SECTION_TO_LABEL_MAPPING:
            await process_task(item)
        else:
            logging.info(f"Skipping task {task_id} as it's not in a monitored section")

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
        await process_changes(sync_data)
        sync_token = sync_data["sync_token"]
        last_sync_time = current_time
        logging.info(f"Sync completed. Processed {len(sync_data.get('items', []))} items.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Sync failed: {str(e)}")

@app.post("/todoist/")
async def todoist_webhook(webhook: TodoistWebhook, background_tasks: BackgroundTasks):
    background_tasks.add_task(sync_and_process)
    return {"status": "Processing started"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
