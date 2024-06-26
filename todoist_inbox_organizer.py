import os
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
from typing import Annotated
from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field
from starlette.requests import Request
from todoist_api_python.api_async import TodoistAPIAsync
import requests
import uuid

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

INBOX_PROJECT_ID = "2236493795"
SECTION_TO_PROJECT_MAPPING = {
    "Work": "2327425429",
    "Home": "2244866374",
    "Side": "2327425662",
}

class Task(BaseModel):
    id: str
    section_id: str
    content: str

class Webhook(BaseModel):
    event_name: str
    user_id: str
    event_data: Task

async def get_section_name(section_id):
    section = await todoist_api.get_section(section_id)
    return section.name if section else None



async def move_task_to_project(task_id, project_id):
    try:
        body = {
            "commands": [
                {
                    "type": "item_move",
                    "args": {"id": task_id, "project_id": project_id},
                    "uuid": str(uuid.uuid4()),
                }
            ]
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TODOIST_API_KEY}"
        }
        response = requests.post("https://api.todoist.com/sync/v9/sync", json=body, headers=headers)
        if response.status_code == 200:
            logging.info(f"Moved task {task_id} to project {project_id}")
            return True
        else:
            logging.error(f"Failed to move task {task_id} to project {project_id}. Status code: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to move task {task_id} to project {project_id}. Error: {str(e)}")
        return False

async def process_task(task_id, section_id, content):
    section_name = await get_section_name(section_id)
    if section_name and section_name in SECTION_TO_PROJECT_MAPPING:
        target_project_id = SECTION_TO_PROJECT_MAPPING[section_name]
        await move_task_to_project(task_id, target_project_id)
        logging.info(f"Processed task {task_id}. Moved to project {target_project_id}")
    else:
        logging.info(f"Skipped task {task_id} as it has no matching section.")



processed_tasks = {}

@app.post("/todoist/")
async def todoist_webhook(
    webhook: Webhook,
    background_tasks: BackgroundTasks,
):
    if webhook.event_name in ["item:added", "item:updated"] and webhook.event_data.section_id:
        task_id = webhook.event_data.id
        section_id = webhook.event_data.section_id
        content = webhook.event_data.content

        # Check if the task has been processed recently
        if task_id in processed_tasks:
            last_processed_time = processed_tasks[task_id]
            if datetime.now() - last_processed_time < timedelta(seconds=5):
                logging.info(f"Skipping task {task_id} as it was processed recently.")
                return "ok"

        logging.info(f"Task {task_id} {webhook.event_name.split(':')[1]} in section {section_id}")
        background_tasks.add_task(process_task, task_id, section_id, content)

        # Update the processed tasks dictionary
        processed_tasks[task_id] = datetime.now()

    return "ok"

@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    logging.error("%r", exc)
    return "Internal Server Error"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
