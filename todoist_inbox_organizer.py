import os
from dotenv import load_dotenv
import logging
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from todoist_api_python.api_async import TodoistAPIAsync

# Load environment variables
load_dotenv()

DEBUG = os.getenv("DEBUG", "False").lower() == "true"
TODOIST_API_KEY = os.getenv("TODOIST_API_KEY")

logging_format = "%(asctime)s %(levelname)s:%(name)s %(filename)s:%(lineno)d %(funcName)s - %(message)s"
logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO, format=logging_format)

app = FastAPI(debug=DEBUG)

todoist_api = TodoistAPIAsync(TODOIST_API_KEY)

SECTION_TO_LABEL_MAPPING = {
    "158952710": "context/work",
    "157482399": "context/home",
    "157481756": "context/side",
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

async def add_label_to_task(task_id: str, label: str):
    try:
        task = await todoist_api.get_task(task_id)
        if label not in task.labels:
            updated_labels = task.labels + [label]
            await todoist_api.update_task(task_id=task_id, labels=updated_labels)
            logging.info(f"Added label {label} to task {task_id}")
        else:
            logging.info(f"Task {task_id} already has label {label}")
    except Exception as e:
        logging.error(f"Error adding label to task: {str(e)}")

async def process_task(task_id: str, section_id: str):
    if section_id in SECTION_TO_LABEL_MAPPING:
        label = SECTION_TO_LABEL_MAPPING[section_id]
        await add_label_to_task(task_id, label)
    else:
        logging.info(f"Section {section_id} is not monitored. No action taken for task {task_id}")

@app.post("/todoist/")
async def todoist_webhook(webhook: Webhook, background_tasks: BackgroundTasks):
    logging.info(f"Received webhook: {webhook.dict()}")
    
    if webhook.event_name in ["item:updated", "item:added"]:
        task_id = webhook.event_data.id
        section_id = webhook.event_data.section_id
        logging.info(f"Processing task {task_id} in section {section_id}")
        background_tasks.add_task(process_task, task_id, section_id)
    
    return {"status": "Processing started"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
