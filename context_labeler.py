import os
from dotenv import load_dotenv
import logging
from typing import Optional
from fastapi import FastAPI
from pydantic import BaseModel, Field
from starlette.requests import Request
from todoist_api_python.api_async import TodoistAPIAsync

# Load environment variables from .env file
load_dotenv()

# Access environment variables
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
TODOIST_API_KEY = os.getenv("TODOIST_API_KEY")

logging_format = "%(asctime)s %(levelname)s:%(name)s %(filename)s:%(lineno)d %(funcName)s - %(message)s"
logging.basicConfig(level=logging.DEBUG, format=logging_format)

app = FastAPI(debug=DEBUG)

todoist_api = TodoistAPIAsync(TODOIST_API_KEY)

SECTION_TO_LABEL_MAPPING = {
    "151168595": "context/work",
    "151168593": "context/home",
    "151168596": "context/side",
}

class Task(BaseModel):
    id: str
    labels: list[str] = Field(default_factory=list)
    section_id: Optional[str] = None

class Initiator(BaseModel):
    id: str

class Webhook(BaseModel):
    event_name: str
    user_id: str
    event_data: Task
    initiator: Initiator

async def add_context_label(task_id: str, section_id: str, current_labels: list[str]) -> list[str]:
    if section_id in SECTION_TO_LABEL_MAPPING:
        context_label = SECTION_TO_LABEL_MAPPING[section_id]
        if context_label not in current_labels:
            updated_labels = list(set(current_labels + [context_label]))
            await todoist_api.update_task(task_id=task_id, labels=updated_labels)
            logging.info(f"Added context label {context_label} to task {task_id}")
            return updated_labels
        else:
            logging.info(f"Task {task_id} already has context label {context_label}")
    else:
        logging.info(f"No context label mapping found for section {section_id}")
    return current_labels

@app.post("/todoist/")
async def todoist_webhook(webhook: Webhook):
    try:
        webhook_dict = webhook.model_dump()
    except AttributeError:
        webhook_dict = webhook.dict()
    
    logging.info(f"Received webhook: {webhook_dict}")
    task_id = webhook.event_data.id
    labels = webhook.event_data.labels
    section_id = webhook.event_data.section_id
    logging.info(f"Task {task_id} {webhook.event_name}. Labels: {labels}, Section ID: {section_id}")

    if webhook.event_name in ["item:updated", "item:added"] and section_id:
        updated_labels = await add_context_label(task_id, section_id, labels)
        return {"task_id": task_id, "updated_labels": updated_labels}

    return {"message": "No action taken"}

@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    logging.error("%r", exc)
    return {"error": "Internal Server Error"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
