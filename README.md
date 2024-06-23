# Todoist Focus

Todoist Focus is a FastAPI application that helps you focus on one task at a time in Todoist.

## Prerequisites

- Docker Compose
- Todoist API key

## Setup

1. Clone the repository:

```
git clone https://github.com/your-username/todoist-focus.git
cd todoist-focus
```

1. Create a `.env` file in the project directory and add yo ur Todoist API key:

```
TODOIST_API_KEY=your_todoist_api_key_here
```

Replace `your_todoist_api_key_here` with your actual Todoist API key.

## Running the Application

To run the Todoist Focus application using Docker Compose, follow these steps:

1. Build and start the Docker containers:

```
docker-compose up --build
```

This command will build the Docker image and start the containers defined in the `docker-compose.yml` file.

1. Access the application:  
   Open a web browser and navigate to `http://localhost:8007`. The FastAPI application should be accessible.
2. To stop the containers , press `Ctrl+C` in the terminal where you started the containers.

## Running Tests

To run the tests for the Todoist Focus application, execute the following command in the project directory:

```
docker-compose run todoist-focus pytest test_todoist_focus
```

This command will run the tests inside the Docker container.

## Cleanup

To remove the Docker containers, networks, and images created by Docker Compose, run the following command:

```
docker-compose down
```

This command will stop and remove the containers, as well as any associated networks and images.

