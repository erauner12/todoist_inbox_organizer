import os
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, Depends
from pydantic import BaseModel
from starlette.requests import Request
from synctodoist import TodoistAPI
from synctodoist.models import Task, Due, Project, Section, Reminder

from datetime import datetime, timedelta
from dateutil import relativedelta
import pytz

# Define the user's timezone (CST for Oklahoma)
USER_TIMEZONE = pytz.timezone('America/Chicago')

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
    "Tomorrow": {"due_string": "tomorrow", "due_lang": "en"},
    "This Weekend": {"due_string": "saturday", "due_lang": "en"},
    "Next Week": {"due_string": "on monday", "due_lang": "en"},
    "In 1 hour": {"due_string": "+1 hour", "due_lang": "en", "add_reminder": True},
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

async def set_reminder(api: TodoistAPI, task_id: str) -> bool:
    try:
        task = api.get_task(task_id=task_id)
        if task.due:
            reminder = Reminder(
                item_id=task_id,
                type="relative",
                minute_offset=0,
            )
            api.add_reminder(reminder)
            api.commit()
            logging.info(f"Added reminder at due time for task {task_id}")
            return True
        else:
            # If the task doesn't have a due date, we can't set a relative reminder
            logging.error(f"Can't set reminder for task {task_id} as it has no due date")
            return False
    except Exception as e:
        logging.error(f"Failed to add reminder to task {task_id}. Error: {str(e)}")
        return False

async def get_section_name(api: TodoistAPI, section_id: str) -> Optional[str]:
    section = api.get_section(section_id=section_id)
    return section.name if section else None

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

async def set_due_date(api: TodoistAPI, task_id: str, due_string: str, due_lang: str = "en", add_duration: bool = False) -> bool:
    try:
        logging.debug(f"Setting due date for task {task_id} with due_string: {due_string}")
        
        task = api.get_task(task_id=task_id)
        
        if due_string.startswith("+"):
            # Handle relative time
            parts = due_string[1:].split()
            if len(parts) == 2:
                amount = int(parts[0])
                unit = parts[1].lower()
                now = datetime.now(USER_TIMEZONE)
                if unit in ['hour', 'hours']:
                    due_date = now + timedelta(hours=amount)
                elif unit in ['day', 'days']:
                    due_date = now + timedelta(days=amount)
                elif unit in ['week', 'weeks']:
                    due_date = now + timedelta(weeks=amount)
                elif unit in ['month', 'months']:
                    due_date = now + relativedelta.relativedelta(months=amount)
                else:
                    raise ValueError(f"Unsupported time unit: {unit}")
                
                # Convert to UTC for storage
                due_date_utc = due_date.astimezone(pytz.UTC)
                
                task.due = Due(
                    date=due_date_utc.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                    timezone=str(USER_TIMEZONE),
                    string=f"{due_date.strftime('%Y-%m-%d')} at {due_date.strftime('%I:%M %p')}",
                    lang=due_lang
                )
            else:
                raise ValueError(f"Invalid relative time format: {due_string}")
        else:
            # For non-relative times, let Todoist handle the parsing
            task.due = Due(string=due_string, lang=due_lang, timezone=str(USER_TIMEZONE))
        
        if add_duration:
            task.duration = {"unit": "minute", "amount": 60}
        
        api.update_task(task_id=task.id, task=task)
        api.commit()
        
        logging.info(f"Set due date to '{task.due.string}' for task {task_id}")
        if add_duration:
            logging.info(f"Added 1 hour duration to task {task_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to set due date for task {task_id}. Error: {str(e)}")
        return False

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

async def move_task_to_project(api: TodoistAPI, task_id: str, project_id: str) -> bool:
    try:
        task = api.get_task(task_id=task_id)
        project = api.get_project(project_id=project_id)
        
        api.move_task(task=task, project=project.id)
        api.commit()
        
        logging.info(f"Moved task {task_id} to project {project_id}")
        return True
    except Exception as e:
        logging.error(f"Failed to move task {task_id} to project {project_id}. Error: {str(e)}")
        return False

async def process_task(api: TodoistAPI, task: Task):
    if task.project_id != INBOX_PROJECT_ID or not task.section_id:
        return  # Only process tasks in the Inbox project and in a section
    
    # First, try to process context label (Home, Work, Side)
    context_processed = await process_context_label(api, task)
    if context_processed:
        return  # Exit function if context label was processed
    
    # Process specific sections for due dates
    if task.section_id:
        section_name = await get_section_name(api, task.section_id)
        
        if section_name in DUE_TIME_SECTIONS:
            due_info = DUE_TIME_SECTIONS[section_name]
            await set_due_date(api, task.id, due_info["due_string"], due_info["due_lang"], add_duration=due_info.get("add_duration", False))
            logging.info(f"Processed task {task.id}. Set due date to {due_info['due_string']}")
            
            if due_info.get("add_reminder", False):
                await set_reminder(api, task.id)
                logging.info(f"Added reminder to task {task.id}")
        elif section_name == "Due Today":
            await set_due_date(api, task.id, "today")
            logging.info(f"Processed task {task.id}. Set due date to today")

    logging.info(f"Finished processing task {task.id}")
    
async def process_context_label(api: TodoistAPI, task: Task):
    section_name = await get_section_name(api, task.section_id) if task.section_id else None
    if section_name in SECTION_TO_LABEL_MAPPING:
        label = SECTION_TO_LABEL_MAPPING[section_name]
        if label in LABEL_TO_PROJECT_MAPPING:
            target_project_id = LABEL_TO_PROJECT_MAPPING[label]
            success = await move_task_to_project(api, task.id, target_project_id)
            if success:
                await add_label_to_task(api, task.id, label)
                logging.info(f"Moved task {task.id} to project {target_project_id} and added label {label}")
            return True
    return False
        
async def move_task_to_section(api: TodoistAPI, task_id: str, project_id: str, section_name: str) -> bool:
    try:
        task = api.get_task(task_id=task_id)
        section = await get_or_create_section(api, project_id, section_name)
        
        if section:
            api.move_task(task=task, section=section.id)
            api.commit()
            
            logging.info(f"Moved task {task_id} to section {section_name} in project {project_id}")
            return True
        else:
            logging.error(f"Failed to move task {task_id}: couldn't find or create section {section_name}")
            return False
    except Exception as e:
        logging.error(f"Failed to move task {task_id} to section {section_name} in project {project_id}. Error: {str(e)}")
        return False

async def get_or_create_section(api: TodoistAPI, project_id: str, section_name: str) -> Optional[Section]:
    try:
        sections = api.sections.find(f"^{section_name}$", field="name", return_all=True)
        for section in sections:
            if section.project_id == project_id:
                return section
        
        # If no section exists, create one
        new_section = Section(name=section_name, project_id=project_id)
        api.add_section(new_section)
        api.commit()
        return new_section
    except Exception as e:
        logging.error(f"Failed to get or create {section_name} section for project {project_id}. Error: {str(e)}")
        return None

processed_tasks = {}

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

        if task_id in processed_tasks:
            last_processed_time = processed_tasks[task_id]
            if datetime.now() - last_processed_time < timedelta(seconds=5):
                logging.info(f"Skipping task {task_id} as it was processed recently.")
                return "ok"

        logging.info(f"Task {task_id} {webhook.event_name.split(':')[1]} in project {task.project_id}, section {task.section_id}")
        
        background_tasks.add_task(process_task, api, task)

        processed_tasks[task_id] = datetime.now()

    return "ok"

@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    logging.error("%r", exc)
    return "Internal Server Error"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
