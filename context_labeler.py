import os
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, Depends
from pydantic import BaseModel
from starlette.requests import Request
from synctodoist import TodoistAPI
from synctodoist.models import Task, Due, Project, Section

# Load environment variables from .env file
load_dotenv()

# Access environment variables
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
TODOIST_API_KEY = os.getenv("TODOIST_API_KEY")

logging_format = "%(asctime)s %(levelname)s:%(name)s %(filename)s:%(lineno)d %(funcName)s - %(message)s"
logging.basicConfig(level=logging.DEBUG, format=logging_format)

app = FastAPI(debug=DEBUG)

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
    "Due 9am": {"due_string": "today at 9am", "due_lang": "en"},
    "Due 12pm": {"due_string": "today at 12pm", "due_lang": "en"},
    "Due 5pm": {"due_string": "today at 5pm", "due_lang": "en"},
}

class WebhookTask(BaseModel):
    id: str
    project_id: str
    section_id: Optional[str]
    content: str

class Webhook(BaseModel):
    event_name: str
    user_id: str
    event_data: WebhookTask

def get_todoist_api():
    api = TodoistAPI(api_key=TODOIST_API_KEY)
    api.sync()
    return api

async def remove_due_date(api: TodoistAPI, task_id: str) -> bool:
    try:
        task = api.get_task(task_id=task_id)
        task.due = None
        api.update_task(task_id=task.id, task=task)
        api.commit()
        logging.info(f"Successfully removed due date from task {task_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to remove due date from task {task_id}. Error: {str(e)}")
        return False

async def get_section_name(api: TodoistAPI, section_id: str) -> Optional[str]:
    section = api.get_section(section_id=section_id)
    return section.name if section else None

async def get_or_create_inbox_section(api: TodoistAPI, project_id: str) -> Optional[str]:
    try:
        sections = api.sections.find(f"^Inbox \\*", field="name", return_all=True)
        for section in sections:
            if section.project_id == project_id:
                return section.id
        
        new_section = Section(name="Inbox *", project_id=project_id)
        api.add_section(new_section)
        api.commit()
        return new_section.id
    except Exception as e:
        logging.error(f"Failed to get or create Inbox section for project {project_id}. Error: {str(e)}")
        return None

async def add_label_to_task(api: TodoistAPI, task_id: str, label: str) -> bool:
    try:
        task = api.get_task(task_id=task_id)
        labels = task.labels + [label] if task.labels else [label]
        task.labels = labels
        api.update_task(task_id=task.id, task=task)
        api.commit()
        logging.info(f"Added label {label} to task {task_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to add label {label} to task {task_id}. Error: {str(e)}")
        return False

async def move_task_to_project_inbox(api: TodoistAPI, task_id: str, project_id: str) -> bool:
    try:
        inbox_section_id = await get_or_create_inbox_section(api, project_id)
        if not inbox_section_id:
            logging.error(f"Failed to get or create Inbox section for project {project_id}")
            return False

        task = api.get_task(task_id=task_id)
        task.project_id = project_id
        task.section_id = inbox_section_id
        api.update_task(task_id=task.id, task=task)
        api.commit()

        logging.info(f"Successfully moved task {task_id} to Inbox section of project {project_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to move task {task_id} to Inbox section of project {project_id}. Error: {str(e)}")
        return False

async def set_due_date(api: TodoistAPI, task_id: str, due_string: str, due_lang: str = "en", add_duration: bool = False) -> bool:
    try:
        logging.debug(f"Setting due date for task {task_id} with due_string: {due_string}")
        
        task = api.get_task(task_id=task_id)
        task.due = Due(string=due_string, lang=due_lang)
        
        if add_duration:
            task.duration = {"unit": "minute", "amount": 60}
        
        api.update_task(task_id=task.id, task=task)
        api.commit()
        
        logging.info(f"Set due date to '{due_string}' for task {task_id}")
        if add_duration:
            logging.info(f"Added 1 hour duration to task {task_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to set due date for task {task_id}. Error: {str(e)}")
        return False

async def process_task(api: TodoistAPI, task_id: str, project_id: str, section_id: str, content: str):
    section_name = await get_section_name(api, section_id)
    if section_name == "Due Today":
        await set_due_date(api, task_id, "today")
        logging.info(f"Processed task {task_id}. Set due date to today")
    elif section_name in DUE_TIME_SECTIONS:
        due_info = DUE_TIME_SECTIONS[section_name]
        await set_due_date(api, task_id, due_info["due_string"], due_info["due_lang"], add_duration=True)
        logging.info(f"Processed task {task_id}. Set due date to {due_info['due_string']} with 1 hour duration")
    elif section_name and section_name in SECTION_TO_LABEL_MAPPING:
        label = SECTION_TO_LABEL_MAPPING[section_name]
        if label.startswith("move/"):
            await process_move_section(api, task_id, label)
        else:
            await add_label_to_task(api, task_id, label)
            logging.info(f"Processed task {task_id}. Added label {label}")
    elif section_name and section_name.startswith("Inbox *"):
        success = await remove_due_date(api, task_id)
        if success:
            logging.info(f"Processed task {task_id}. Removed due date as it was moved to Inbox section")
        else:
            logging.error(f"Failed to remove due date from task {task_id}")
    elif section_name in ["Parallel=-", "Immediate--"]:
        success = await set_due_date_today_9am(api, task_id)
        if success:
            logging.info(f"Processed task {task_id}. Set due date to today at 9am as it was moved to {section_name} section")
        else:
            logging.error(f"Failed to set due date for task {task_id}")
    else:
        logging.info(f"Skipped task {task_id} as it has no matching section.")

async def process_move_section(api: TodoistAPI, task_id: str, move_type: str):
    task = api.get_task(task_id=task_id)
    if task and task.labels:
        for label in task.labels:
            if label in LABEL_TO_PROJECT_MAPPING:
                target_project_id = LABEL_TO_PROJECT_MAPPING[label]
                if move_type == "move/immediate":
                    success = await move_task_to_project_and_section(api, task_id, target_project_id, "Immediate--")
                elif move_type == "move/parallel":
                    success = await move_task_to_project_and_section(api, task_id, target_project_id, "Parallel=-")
                elif move_type == "move/inbox":
                    success = await move_task_to_project_inbox(api, task_id, target_project_id)
                else:
                    logging.error(f"Unknown move type: {move_type}")
                    return

                if success:
                    logging.info(f"Moved task {task_id} to project {target_project_id} based on label {label}")
                    return
                else:
                    logging.error(f"Failed to move task {task_id} to project {target_project_id}")
    logging.info(f"Task {task_id} has no matching label for moving.")

async def set_due_date_today_9am(api: TodoistAPI, task_id: str) -> bool:
    return await set_due_date(api, task_id, "today at 9am")

async def move_task_to_project_and_section(api: TodoistAPI, task_id: str, project_id: str, section_prefix: str) -> bool:
    try:
        task = api.get_task(task_id=task_id)
        project = api.get_project(project_id=project_id)
        
        api.move_task(task=task, project=project)
        api.commit()
        
        return True
    except Exception as e:
        logging.error(f"Failed to move task {task_id}. Error: {str(e)}")
        return False

processed_tasks = {}

@app.post("/todoist/")
async def todoist_webhook(webhook: Webhook, background_tasks: BackgroundTasks, api: TodoistAPI = Depends(get_todoist_api)):
    if webhook.event_name in ["item:added", "item:updated"]:
        task_id = webhook.event_data.id
        project_id = webhook.event_data.project_id
        section_id = webhook.event_data.section_id
        content = webhook.event_data.content

        if task_id in processed_tasks:
            last_processed_time = processed_tasks[task_id]
            if datetime.now() - last_processed_time < timedelta(seconds=5):
                logging.info(f"Skipping task {task_id} as it was processed recently.")
                return "ok"

        logging.info(f"Task {task_id} {webhook.event_name.split(':')[1]} in project {project_id}, section {section_id}")
        
        if section_id:
            background_tasks.add_task(process_task, api, task_id, project_id, section_id, content)

        processed_tasks[task_id] = datetime.now()

    return "ok"

@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    logging.error("%r", exc)
    return "Internal Server Error"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
