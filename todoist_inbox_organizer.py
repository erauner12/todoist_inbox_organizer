import os
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
from typing import Annotated
from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field
from starlette.requests import Request
from todoist_api_python.api import TodoistAPI
from todoist_api_python.api_async import TodoistAPIAsync
import requests
import uuid
import json
import time
from collections import defaultdict

# Load environment variables from .env file
load_dotenv()

# Access environment variables
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
TODOIST_API_KEY = os.getenv("TODOIST_API_KEY")

logging_format = "%(asctime)s %(levelname)s:%(name)s %(filename)s:%(lineno)d %(funcName)s - %(message)s"
logging.basicConfig(level=logging.DEBUG, format=logging_format)

app = FastAPI(
    debug=DEBUG,
)

todoist_api = TodoistAPIAsync(TODOIST_API_KEY)
todoist_sync_api = TodoistAPI(TODOIST_API_KEY)

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

class Task(BaseModel):
    id: str
    project_id: str
    section_id: str
    content: str

class Webhook(BaseModel):
    event_name: str
    user_id: str
    event_data: Task

# Global variables for rate limiting
rate_limited = False
rate_limit_reset_time = 0

# Use a defaultdict to track processed tasks with their timestamps
processed_tasks = defaultdict(lambda: {'timestamp': 0, 'count': 0})

# Increase the deduplication window to 30 seconds
DEDUPLICATION_WINDOW = 30

async def handle_rate_limit(reset_time):
    global rate_limited, rate_limit_reset_time
    rate_limited = True
    rate_limit_reset_time = reset_time
    logging.warning(f"Rate limit reached. Pausing requests until {time.ctime(reset_time)}")

async def check_rate_limit():
    global rate_limited, rate_limit_reset_time
    if rate_limited:
        if time.time() < rate_limit_reset_time:
            raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")
        else:
            rate_limited = False
            rate_limit_reset_time = 0

async def remove_due_date(task_id):
    url = 'https://api.todoist.com/sync/v9/sync'
    headers = {
        'Authorization': f'Bearer {TODOIST_API_KEY}',
        'Content-Type': 'application/json'
    }
    data = {
        'commands': json.dumps([
            {
                'type': 'item_update',
                'uuid': str(uuid.uuid4()),
                'args': {
                    'id': task_id,
                    'due': None
                }
            }
        ])
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        logging.info(f"Successfully removed due date from task {task_id}")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to remove due date from task {task_id}. Error: {str(e)}")
        return False

async def get_section_name(section_id):
    try:
        await check_rate_limit()
        section = await todoist_api.get_section(section_id)
        return section.name if section else None
    except Exception as e:
        if hasattr(e, 'status_code') and e.status_code == 429:
            await handle_rate_limit(int(getattr(e, 'headers', {}).get('X-Rate-Limit-Reset', time.time() + 60)))
            raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")
        logging.error(f"Error getting section name: {str(e)}")
        raise

async def get_or_create_inbox_section(project_id):
    try:
        await check_rate_limit()
        sections = await todoist_api.get_sections(project_id=project_id)
        for section in sections:
            if section.name.startswith("Inbox *"):
                return section.id
        
        # If no Inbox section exists, create one
        new_section = await todoist_api.add_section(name="Inbox *", project_id=project_id)
        return new_section.id
    except Exception as e:
        if hasattr(e, 'status_code') and e.status_code == 429:
            await handle_rate_limit(int(getattr(e, 'headers', {}).get('X-Rate-Limit-Reset', time.time() + 60)))
            raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")
        logging.error(f"Failed to get or create Inbox section for project {project_id}. Error: {str(e)}")
        return None
    
    
async def add_label_to_task(task_id, label):
    try:
        await check_rate_limit()
        task = await todoist_api.get_task(task_id)
        if label not in task.labels:
            labels = task.labels + [label]
            updated_task = todoist_sync_api.update_task(task_id=task_id, labels=labels)
            if updated_task:
                logging.info(f"Added label {label} to task {task_id}")
                return True
            else:
                logging.error(f"Failed to add label {label} to task {task_id}")
                return False
        else:
            logging.info(f"Label {label} already exists on task {task_id}. No action taken.")
            return True
    except Exception as e:
        if hasattr(e, 'status_code') and e.status_code == 429:
            await handle_rate_limit(int(getattr(e, 'headers', {}).get('X-Rate-Limit-Reset', time.time() + 60)))
            raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")
        logging.error(f"Error adding label to task: {str(e)}")
        raise
    
async def move_task_to_project_inbox(task_id, project_id):
    try:
        await check_rate_limit()
        inbox_section_id = await get_or_create_inbox_section(project_id)
        if not inbox_section_id:
            logging.error(f"Failed to get or create Inbox section for project {project_id}")
            return False

        move_command = {
            "type": "item_move",
            "args": {
                "id": task_id,
                "section_id": inbox_section_id
            },
            "uuid": str(uuid.uuid4()),
        }
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TODOIST_API_KEY}"
        }
        
        response = requests.post("https://api.todoist.com/sync/v9/sync", 
                                 json={"commands": [move_command]}, 
                                 headers=headers)
        
        if response.status_code != 200:
            logging.error(f"Failed to move task {task_id} to Inbox section of project {project_id}. Status code: {response.status_code}")
            return False

        logging.info(f"Successfully moved task {task_id} to Inbox section of project {project_id}")
        return True

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to move task {task_id} to Inbox section of project {project_id}. Error: {str(e)}")
        return False

async def set_due_date(task_id, due_string, due_lang="en", add_duration=False):
    try:
        await check_rate_limit()
        logging.debug(f"Setting due date for task {task_id} with due_string: {due_string}")
        
        update_args = {
            "task_id": task_id,
            "due_string": due_string,
            "due_lang": due_lang
        }
        
        if add_duration:
            update_args["duration"] = 60
            update_args["duration_unit"] = "minute"
        
        logging.debug(f"Updating task with args: {update_args}")
        updated_task = todoist_sync_api.update_task(**update_args)
        
        if updated_task:
            logging.info(f"Set due date to '{due_string}' for task {task_id}")
            logging.debug(f"Updated task details: {updated_task}")
            if add_duration:
                logging.info(f"Added 1 hour duration to task {task_id}")
            return True
        else:
            logging.error(f"Failed to set due date for task {task_id}")
            return False
    except Exception as e:
        if hasattr(e, 'status_code') and e.status_code == 429:
            await handle_rate_limit(int(getattr(e, 'headers', {}).get('X-Rate-Limit-Reset', time.time() + 60)))
            raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")
        logging.error(f"Error setting due date for task: {str(e)}")
        return False

async def get_section_id(project_id, section_prefix):
    try:
        await check_rate_limit()
        sections = await todoist_api.get_sections(project_id=project_id)
        for section in sections:
            if section.name.startswith(section_prefix):
                return section.id
        return None
    except Exception as e:
        if hasattr(e, 'status_code') and e.status_code == 429:
            await handle_rate_limit(int(getattr(e, 'headers', {}).get('X-Rate-Limit-Reset', time.time() + 60)))
            raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")
        logging.error(f"Failed to get section for project {project_id}. Error: {str(e)}")
        return None

async def move_task_to_project_and_section(task_id, project_id, section_prefix):
    try:
        await check_rate_limit()
        # Step 1: Move task to the project
        move_to_project_command = {
            "type": "item_move",
            "args": {
                "id": task_id,
                "project_id": project_id
            },
            "uuid": str(uuid.uuid4()),
        }
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TODOIST_API_KEY}"
        }
        
        response = requests.post("https://api.todoist.com/sync/v9/sync", 
                                 json={"commands": [move_to_project_command]}, 
                                 headers=headers)
        
        if response.status_code != 200:
            logging.error(f"Failed to move task {task_id} to project {project_id}. Status code: {response.status_code}")
            return False

        # Step 2: Move task to the specified section (if it exists)
        section_id = await get_section_id(project_id, section_prefix)
        if section_id:
            move_to_section_command = {
                "type": "item_move",
                "args": {
                    "id": task_id,
                    "section_id": section_id
                },
                "uuid": str(uuid.uuid4()),
            }
            
            response = requests.post("https://api.todoist.com/sync/v9/sync", 
                                     json={"commands": [move_to_section_command]}, 
                                     headers=headers)
            
            if response.status_code != 200:
                logging.error(f"Failed to move task {task_id} to section {section_id}. Status code: {response.status_code}")
                return False

        logging.info(f"Successfully moved task {task_id} to project {project_id}" + 
                     (f" and {section_prefix} section" if section_id else ""))
        return True

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to move task {task_id}. Error: {str(e)}")
        return False

async def process_move_section(task_id, move_type):
    try:
        await check_rate_limit()
        task = await todoist_api.get_task(task_id)
        if task and task.labels:
            for label in task.labels:
                if label in LABEL_TO_PROJECT_MAPPING:
                    target_project_id = LABEL_TO_PROJECT_MAPPING[label]
                    if move_type == "move/immediate":
                        success = await move_task_to_project_and_section(task_id, target_project_id, "Immediate--")
                    elif move_type == "move/parallel":
                        success = await move_task_to_project_and_section(task_id, target_project_id, "Parallel=-")
                    elif move_type == "move/inbox":
                        success = await move_task_to_project_inbox(task_id, target_project_id)
                    else:
                        logging.error(f"Unknown move type: {move_type}")
                        return

                    if success:
                        logging.info(f"Moved task {task_id} to project {target_project_id} based on label {label}")
                        return
                    else:
                        logging.error(f"Failed to move task {task_id} to project {target_project_id}")
        logging.info(f"Task {task_id} has no matching label for moving.")
    except Exception as e:
        if hasattr(e, 'status_code') and e.status_code == 429:
            await handle_rate_limit(int(getattr(e, 'headers', {}).get('X-Rate-Limit-Reset', time.time() + 60)))
            raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")
        logging.error(f"Error processing move section: {str(e)}")
        raise


async def process_task(task_id, project_id, section_id, content):
    try:
        await check_rate_limit()
        section_name = await get_section_name(section_id)
        task = await todoist_api.get_task(task_id)

        if section_name in SECTION_TO_LABEL_MAPPING:
            label = SECTION_TO_LABEL_MAPPING[section_name]
            if label.startswith("move/"):
                await process_move_section(task_id, label)
            elif label not in task.labels:
                await add_label_to_task(task_id, label)
                logging.info(f"Processed task {task_id}. Added label {label}")
            else:
                logging.info(f"Task {task_id} already has label {label}. No action taken.")
        elif section_name == "Due Today" and (not task.due or task.due.date != datetime.now().strftime("%Y-%m-%d")):
            await set_due_date(task_id, "today")
            logging.info(f"Processed task {task_id}. Set due date to today")
        elif section_name in DUE_TIME_SECTIONS and (not task.due or task.due.string != DUE_TIME_SECTIONS[section_name]["due_string"]):
            due_info = DUE_TIME_SECTIONS[section_name]
            await set_due_date(task_id, due_info["due_string"], due_info["due_lang"], add_duration=True)
            logging.info(f"Processed task {task_id}. Set due date to {due_info['due_string']} with 1 hour duration")
        elif section_name and section_name.startswith("Inbox *") and task.due:
            success = await remove_due_date(task_id)
            if success:
                logging.info(f"Processed task {task_id}. Removed due date as it was moved to Inbox section")
            else:
                logging.error(f"Failed to remove due date from task {task_id}")
        else:
            logging.info(f"Skipped task {task_id} as it has no matching section or no changes needed.")
    except Exception as e:
        if hasattr(e, 'status_code') and e.status_code == 429:
            await handle_rate_limit(int(getattr(e, 'headers', {}).get('X-Rate-Limit-Reset', time.time() + 60)))
            raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")
        logging.error(f"Error processing task: {str(e)}")
        raise

@app.post("/todoist/")
async def todoist_webhook(webhook: Webhook, background_tasks: BackgroundTasks):
    if webhook.event_name in ["item:added", "item:updated"]:
        task_id = webhook.event_data.id
        project_id = webhook.event_data.project_id
        section_id = webhook.event_data.section_id
        content = webhook.event_data.content

        current_time = time.time()
        task_info = processed_tasks[task_id]

        if current_time - task_info['timestamp'] < DEDUPLICATION_WINDOW:
            task_info['count'] += 1
            if task_info['count'] > 1:
                logging.info(f"Skipping task {task_id} as it was processed recently (count: {task_info['count']}).")
                return "ok"
        else:
            task_info['timestamp'] = current_time
            task_info['count'] = 1

        logging.info(f"Task {task_id} {webhook.event_name.split(':')[1]} in project {project_id}, section {section_id}")
        
        if section_id:
            try:
                await check_rate_limit()
                background_tasks.add_task(process_task, task_id, project_id, section_id, content)
            except HTTPException as e:
                if e.status_code == 429:
                    logging.warning("Rate limit reached. Skipping task processing.")
                    return "rate limited"
                raise

    return "ok"

@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    logging.error("%r", exc)
    return "Internal Server Error"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
