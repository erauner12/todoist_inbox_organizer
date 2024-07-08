import os
from dotenv import load_dotenv
import logging
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, Depends
from pydantic import BaseModel
from starlette.requests import Request
from synctodoist import TodoistAPI
from synctodoist.models import Task, Project, Section, Due, Reminder

# Load environment variables from .env file
load_dotenv()

# Access environment variables
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
TODOIST_API_KEY = os.getenv("TODOIST_API_KEY")

logging_format = "%(asctime)s %(levelname)s:%(name)s %(filename)s:%(lineno)d %(funcName)s - %(message)s"
logging.basicConfig(level=logging.DEBUG, format=logging_format)

app = FastAPI(debug=DEBUG)

# Define the Inbox project ID
INBOX_PROJECT_ID = "2236493795"

# Define the sections to be created for new projects
DEFAULT_SECTIONS = [
    "Next Up--",
    "Next Actions=-",
    "Someday",
    "Waiting For"
]

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

async def get_section_name(api: TodoistAPI, section_id: str) -> Optional[str]:
    section = api.get_section(section_id=section_id)
    return section.name if section else None

async def create_default_sections(api: TodoistAPI, project_id: str):
    for section_name in DEFAULT_SECTIONS:
        new_section = Section(name=section_name, project_id=project_id)
        api.add_section(new_section)
    api.commit()
    logging.info(f"Created default sections for project {project_id}")

async def create_default_task(api: TodoistAPI, project_id: str):
    default_task = Task(
        content="Move this project to the appropriate workspace",
        project_id=project_id,
        labels=["todoist_admin"],
        due=Due(string="today at 5pm", lang="en")
    )
    api.add_task(default_task)
    api.commit()
    
    # Get the created task to add a reminder
    created_task = api.get_task(task_id=default_task.id)
    
    reminder = Reminder(
        item_id=created_task.id,
        type="absolute",
        due=created_task.due
    )
    api.add_reminder(reminder)
    api.commit()
    
    logging.info(f"Created default task with reminder for project {project_id}")

async def get_or_create_project(api: TodoistAPI, project_name: str) -> Project:
    try:
        project = api.find_project(pattern=f"^{project_name}$")
    except Exception:
        # If project doesn't exist, create it
        new_project = Project(name=project_name)
        api.add_project(new_project)
        api.commit()
        project = api.find_project(pattern=f"^{project_name}$")
        
        # Create default sections for the new project
        await create_default_sections(api, project.id)
        
        # Create default task with reminder
        await create_default_task(api, project.id)
    
    return project

async def move_task_to_project(api: TodoistAPI, task_id: str, project_name: str) -> bool:
    try:
        task = api.get_task(task_id=task_id)
        project = await get_or_create_project(api, project_name)
        
        api.move_task(task=task, project=project.id)
        api.commit()
        
        logging.info(f"Moved task {task_id} to project {project_name}")
        return True
    except Exception as e:
        logging.error(f"Failed to move task {task_id} to project {project_name}. Error: {str(e)}")
        return False

async def process_task(api: TodoistAPI, task: Task):
    if task.project_id != INBOX_PROJECT_ID or not task.section_id:
        return  # Only process tasks in the Inbox project and in a section
    
    section_name = await get_section_name(api, task.section_id)
    if section_name:
        await move_task_to_project(api, task.id, section_name)
        logging.info(f"Processed task {task.id}. Moved to project {section_name}")

@app.post("/todoist/")
async def todoist_webhook(webhook: Webhook, background_tasks: BackgroundTasks, api: TodoistAPI = Depends(get_todoist_api)):
    if webhook.event_name in ["item:added", "item:updated"]:
        task_id = webhook.event_data.id
        
        # Fetch the latest task information
        try:
            task = api.get_task(task_id=task_id)
        except Exception as e:
            logging.error(f"Failed to fetch task {task_id}. Error: {str(e)}")
            return "ok"

        if task.project_id != INBOX_PROJECT_ID:
            logging.info(f"Skipping task {task_id} as it's not in the Inbox project")
            return "ok"

        logging.info(f"Task {task_id} {webhook.event_name.split(':')[1]} in Inbox project, section {task.section_id}")
        
        background_tasks.add_task(process_task, api, task)

    return "ok"

@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    logging.error("%r", exc)
    return "Internal Server Error"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
