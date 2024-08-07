import os
from dotenv import load_dotenv
import logging
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, Depends
from pydantic import BaseModel
from starlette.requests import Request
from synctodoist import TodoistAPI
from synctodoist.models import Task, Project, Section, Due, Reminder
from datetime import datetime, date, timedelta

load_dotenv()

DEBUG = os.getenv("DEBUG", "False").lower() == "true"
TODOIST_API_KEY = os.getenv("TODOIST_API_KEY")

logging_format = "%(asctime)s %(levelname)s:%(name)s %(filename)s:%(lineno)d %(funcName)s - %(message)s"
logging.basicConfig(level=logging.DEBUG, format=logging_format)

app = FastAPI(debug=DEBUG)

INBOX_PROJECT_ID = "2236493795"

DEFAULT_SECTIONS = [
    "Next Up--",
    "Next Actions=-",
    "Someday",
    "Waiting For"
]

LABEL_TO_SECTION = {
    "gtd/ready": "Next Actions",
    "gtd/waiting": "Waiting For",
    "gtd/someday": "Someday"
}

processed_tasks = {}

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

def get_section_name(api: TodoistAPI, section_id: str) -> Optional[str]:
    section = api.get_section(section_id=section_id)
    return section.name if section else None

def create_default_sections(api: TodoistAPI, project_id: str):
    for section_name in DEFAULT_SECTIONS:
        new_section = Section(name=section_name, project_id=project_id)
        api.add_section(new_section)
    api.commit()
    logging.info(f"Created default sections for project {project_id}")

def create_default_task(api: TodoistAPI, project_id: str):
    default_task = Task(
        content="Move this project to the appropriate workspace",
        project_id=project_id,
        labels=["todoist_admin"],
        due=Due(string="today at 5pm", lang="en")
    )
    api.add_task(default_task)
    api.commit()
    
    created_task = api.get_task(task_id=default_task.id)
    
    reminder = Reminder(
        item_id=created_task.id,
        type="absolute",
        due=created_task.due
    )
    api.add_reminder(reminder)
    api.commit()
    
    logging.info(f"Created default task with reminder for project {project_id}")

def get_or_create_project(api: TodoistAPI, project_name: str) -> Project:
    try:
        project = api.find_project(pattern=f"^{project_name}$")
    except Exception:
        new_project = Project(name=project_name)
        api.add_project(new_project)
        api.commit()
        project = api.find_project(pattern=f"^{project_name}$")
        
        create_default_sections(api, project.id)
        create_default_task(api, project.id)
    
    return project

def move_task_to_project(api: TodoistAPI, task_id: str, project_name: str) -> bool:
    try:
        task = api.get_task(task_id=task_id)
        project = get_or_create_project(api, project_name)
        
        api.move_task(task=task, project=project.id)
        api.commit()
        
        gtd_labels_to_remove = []
        for label in task.labels:
            if label in LABEL_TO_SECTION:
                section_name = LABEL_TO_SECTION[label]
                if move_task_to_section(api, task, section_name):
                    gtd_labels_to_remove.append(label)
        
        for label in gtd_labels_to_remove:
            task.labels.remove(label)
            api.update_task(task_id=task.id, task=task)
        api.commit()
        
        logging.info(f"Moved task {task_id} to project {project_name}")
        return True
    except Exception as e:
        logging.error(f"Failed to move task {task_id} to project {project_name}. Error: {str(e)}")
        return False

def get_or_create_section(api: TodoistAPI, project_id: str, section_name: str) -> Optional[Section]:
    logging.info(f"Attempting to get or create section '{section_name}' in project {project_id}")
    try:
        sections = api.sections.find(f"^{section_name}", field="name", return_all=True)
        for section in sections:
            if section.project_id == project_id and section.name.startswith(section_name):
                logging.info(f"Found existing section: {section.name}")
                return section
        
        if project_id != INBOX_PROJECT_ID:
            logging.info(f"Section '{section_name}' not found, creating new section")
            new_section = Section(name=section_name, project_id=project_id)
            api.add_section(new_section)
            api.commit()
            logging.info(f"Created new section: {new_section.name}")
            return new_section
        else:
            logging.info(f"Not creating section '{section_name}' in Inbox project")
            return None
    except Exception as e:
        logging.error(f"Failed to get or create {section_name} section for project {project_id}. Error: {str(e)}")
        return None

def move_task_to_section(api: TodoistAPI, task: Task, section_name: str) -> bool:
    try:
        section = get_or_create_section(api, task.project_id, section_name)
        
        if section:
            api.move_task(task=task, section=section.id)
            api.commit()
            
            logging.info(f"Moved task {task.id} to section {section.name} in project {task.project_id}")
            return True
        else:
            logging.error(f"Failed to move task {task.id}: couldn't find or create section {section_name}")
            return False
    except Exception as e:
        logging.error(f"Failed to move task {task.id} to section {section_name} in project {task.project_id}. Error: {str(e)}")
        return False

def has_due_date(due: Due) -> bool:
    return due is not None and due.date is not None

def has_due_time(due: Due) -> bool:
    return due is not None and due.date is not None and isinstance(due.date, datetime)

def add_relative_reminder(api: TodoistAPI, task: Task):
    reminder = Reminder(
        item_id=task.id,
        type="relative",
        due=task.due
    )
    api.add_reminder(reminder)
    api.commit()
    logging.info(f"Added relative reminder to task {task.id}")

def process_task(api: TodoistAPI, task: Task):
    if task.project_id != INBOX_PROJECT_ID:
        return

    if has_due_date(task.due):
        if "gtd/ready" not in task.labels:
            task.labels.append("gtd/ready")
            api.update_task(task_id=task.id, task=task)
            api.commit()
            logging.info(f"Added 'gtd/ready' label to task {task.id} due to having a due date")
    
    if has_due_time(task.due):
        add_relative_reminder(api, task)

    if task.section_id:
        section_name = get_section_name(api, task.section_id)
        if section_name and section_name not in LABEL_TO_SECTION.values():
            success = move_task_to_project(api, task.id, section_name)
            if success:
                logging.info(f"Processed task {task.id}. Moved to project {section_name}")
            else:
                logging.error(f"Failed to move task {task.id} to project {section_name}")

@app.post("/todoist/")
async def todoist_webhook(webhook: Webhook, background_tasks: BackgroundTasks, api: TodoistAPI = Depends(get_todoist_api)):
    logging.info(f"Received webhook: event_name={webhook.event_name}, task_id={webhook.event_data.id}")
    
    if webhook.event_name in ["item:added", "item:updated"]:
        task_id = webhook.event_data.id
        
        if task_id in processed_tasks:
            last_processed_time = processed_tasks[task_id]
            if datetime.now() - last_processed_time < timedelta(seconds=5):
                logging.info(f"Skipping task {task_id} as it was processed recently.")
                return {"message": "Task skipped due to recent processing"}
        
        try:
            task = api.get_task(task_id=task_id)
        except Exception as e:
            logging.error(f"Failed to fetch task {task_id}. Error: {str(e)}")
            return {"message": "Failed to fetch task"}

        if task.project_id != INBOX_PROJECT_ID:
            logging.info(f"Skipping task {task_id} as it's not in the Inbox project")
            return {"message": "Task not in Inbox project"}

        logging.info(f"Adding task {task_id} to background processing")
        background_tasks.add_task(process_task, api, task)
        processed_tasks[task_id] = datetime.now()
        
        return {"message": "Task processing initiated"}

    logging.info(f"Event not processed: {webhook.event_name}")
    return {"message": "Event not processed"}

@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    logging.error("%r", exc)
    return "Internal Server Error"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)

# TODO: create a todoist section for Sitting tasks out so that they do not have a next_action label
# TODO: add a section for "Sitting" tasks
# TODO: add a section for "Next Up--" tasks
